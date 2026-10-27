from __future__ import annotations

import io
import json
import random
from pathlib import Path
from types import SimpleNamespace

import requests
from prometheus_client.exposition import make_wsgi_app
from pysolark import SolArkAPIError, SolArkAuthError

from app import __version__
from app.main import Backoff, _run_loop
from app.metrics import build_metrics
from app.server import make_status_wsgi_app
from app.status import (
    HARD_AGE_SECONDS,
    HINTS,
    PollState,
    access_note,
    build_status,
    describe_exception,
    hard_age_seconds,
    healthy_window_seconds,
    overall_state,
    redact,
    render_html,
)

NOW = 1_800_000_000.0
SECRET = "s3cret-password"
TOKEN = "sekret-token-value"
EMAIL = "ops@example.com"
FORBIDDEN_KEYS = {
    "password",
    "token",
    "authorization",
    "cookie",
    "latitude",
    "longitude",
    "lat",
    "lon",
    "address",
    "email",
    "username",
    "literals",
}


def _state(**overrides):
    fields = {
        "now": NOW,
        "started_at": NOW - 30,
        "last_success_at": NOW - 30,
        "poll_interval_s": 60,
        "failure_kind": None,
        "poll_thread_alive": True,
    }
    fields.update(overrides)
    return overall_state(**fields)


def _status(**kwargs) -> PollState:
    return PollState(
        poll_interval_s=kwargs.pop("poll_interval_s", 60),
        listen_port=kwargs.pop("listen_port", 10112),
        started_at=kwargs.pop("started_at", NOW - 3600),
        literals=kwargs.pop("literals", (SECRET, EMAIL)),
        thread_alive=kwargs.pop("thread_alive", None),
        version=kwargs.pop("version", __version__),
    )


def _walk(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key, item
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _assert_public(payload: dict) -> None:
    encoded = json.dumps(payload)
    assert SECRET not in encoded
    assert TOKEN not in encoded
    assert EMAIL not in encoded
    assert "Bearer " not in encoded or "Bearer [redacted]" in encoded
    for key, _value in _walk(payload):
        assert key not in FORBIDDEN_KEYS


def test_rule_is_documented_on_the_helper():
    from app import status

    doc = status.__doc__ or ""
    assert "UP" in doc and "DEGRADED" in doc and "DOWN" in doc
    assert "15 minutes" in doc
    assert "2 x" in doc
    assert "hard auth" in doc


def test_windows_are_two_intervals_and_fifteen_minutes():
    assert healthy_window_seconds(60) == 120
    assert hard_age_seconds(60) == HARD_AGE_SECONDS == 900
    assert hard_age_seconds(600) == 1200


def test_up_when_success_is_inside_the_healthy_window():
    assert _state(last_success_at=NOW - 47) == "UP"
    assert _state(last_success_at=NOW - 120) == "UP"


def test_degraded_when_success_is_older_than_the_healthy_window():
    assert _state(last_success_at=NOW - 121) == "DEGRADED"


def test_degraded_on_timeout_even_inside_the_healthy_window():
    assert _state(last_success_at=NOW - 10, failure_kind="timeout") == "DEGRADED"
    assert _state(last_success_at=NOW - 600, failure_kind="http_5xx") == "DEGRADED"
    assert _state(last_success_at=NOW - 30, failure_kind="rate_limit") == "DEGRADED"


def test_down_on_auth_even_when_the_last_success_is_fresh():
    assert _state(last_success_at=NOW - 5, failure_kind="auth") == "DOWN"


def test_down_when_last_success_is_past_the_hard_age():
    assert _state(last_success_at=NOW - 900, failure_kind="timeout") == "DEGRADED"
    assert _state(last_success_at=NOW - 901, failure_kind="timeout") == "DOWN"
    assert _state(last_success_at=NOW - 901, failure_kind=None) == "DOWN"


def test_never_succeeded_is_degraded_until_the_hard_age_then_down():
    assert _state(last_success_at=None, started_at=NOW - 60, failure_kind="timeout") == "DEGRADED"
    assert _state(last_success_at=None, started_at=NOW - 30, failure_kind=None) == "DEGRADED"
    assert _state(last_success_at=None, started_at=NOW - 901, failure_kind="timeout") == "DOWN"
    assert _state(last_success_at=None, started_at=NOW - 5, failure_kind="auth") == "DOWN"


def test_dead_poll_thread_is_not_up():
    assert _state(last_success_at=NOW - 10, poll_thread_alive=False) == "DEGRADED"
    assert _state(last_success_at=NOW - 901, poll_thread_alive=False) == "DOWN"


def test_slow_poll_interval_does_not_flip_a_healthy_cycle_to_down():
    assert _state(poll_interval_s=600, last_success_at=NOW - 1000) == "UP"
    assert _state(poll_interval_s=600, last_success_at=NOW - 1201) == "DOWN"


def test_phrases_and_absolute_pacific_timestamps():
    state = _status()
    state.record_success(plant_id=137067, plant_name="Sean Wells plant", energy_today_kwh=42.6, now=NOW - 47)
    state.note_sleep(60, reason="poll", now=NOW - 47)
    payload = build_status(state, now=NOW)
    assert payload["overall"] == "UP"
    assert payload["overall_phrase"] == "UP"
    assert payload["last_success"]["display"].endswith(" PT")
    assert payload["last_success"]["relative"] == "47s ago"
    assert payload["last_success_display"].endswith("47s ago")
    assert "T" in payload["last_success"]["at"]
    assert payload["extras"]["plant_id"] == 137067
    assert payload["extras"]["plant_name"] == "Sean Wells plant"
    assert payload["extras"]["energy_today"] == "42.6 kWh"
    assert payload["extras"]["snapshot"] == "live"
    assert payload["next_retry"] is None
    assert payload["next_retry_display"] == "n/a"
    assert payload["process"] == "Healthy"

    failed = _status()
    failed.record_success(plant_id=137067, plant_name="Sean Wells plant", energy_today_kwh=38.1, now=NOW - (17 * 60))
    failed.record_failure(SolArkAuthError(401, "bad login"), failures=4, backoff_s=480, phase="login")
    failed.note_sleep(480, reason="backoff", now=NOW)
    down = build_status(failed, now=NOW)
    assert down["overall"] == "DOWN"
    assert down["overall_phrase"] == "DOWN — no success in 17m"
    assert down["extras"]["energy_today"] == "last-known 38.1 kWh"
    assert down["extras"]["snapshot"] == "last-known"
    assert down["next_retry"]["display"].endswith(" PT")
    assert down["next_retry"]["relative"].startswith("~")
    assert " · ~" in down["next_retry_display"]
    assert down["next_poll"]["display"] == "held for backoff"
    assert "shared logins can kick other sessions" in down["hint"]
    assert down["process"] == "Running · poll thread alive"


def test_degraded_timeout_keeps_last_known_energy_and_retry():
    state = _status()
    state.record_success(plant_id=137067, plant_name="Sean Wells plant", energy_today_kwh=42.6, now=NOW - 90)
    state.record_failure(requests.Timeout("timed out"), failures=9, backoff_s=900, phase="plant detail")
    state.note_sleep(900, reason="backoff", now=NOW)
    payload = build_status(state, now=NOW)
    assert payload["overall"] == "DEGRADED"
    assert payload["overall_phrase"] == "DEGRADED — no success in 1m"
    assert payload["extras"]["energy_today"] == "last-known 42.6 kWh"
    assert payload["upstream_display"] == "timeout · plant detail"
    assert "docker3 outbound" in payload["hint"]
    assert payload["next_retry_display"] != "n/a"
    assert payload["series_note"] == "stale"


def test_redact_strips_tokens_emails_and_truncates():
    jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_Z99Umu_N6k"
    raw = (
        f"GET https://user:{SECRET}@api.example/plant?token={TOKEN}&plant=1 "
        f"Authorization: Bearer {jwt} Cookie: session=abc {EMAIL} " + ("x" * 400)
    )
    cleaned = redact(raw, literals=(SECRET,))
    assert TOKEN not in cleaned
    assert SECRET not in cleaned
    assert EMAIL not in cleaned
    assert jwt not in cleaned
    assert "abc123secret" not in redact("Authorization: Bearer abc123secret")
    assert "token=[redacted]" in cleaned
    assert "[redacted-email]" in cleaned
    assert len(cleaned) <= 200
    assert cleaned.endswith("...")


def test_describe_exception_ignores_bodies_payloads_and_tokenized_urls():
    response = requests.Response()
    response.status_code = 500
    response.reason = "Internal Server Error"
    response.url = f"https://api.example/a?token={TOKEN}"
    response._content = f'{{"password":"{SECRET}","email":"{EMAIL}"}}'.encode()
    http_error = requests.HTTPError(f"500 {response.url} {SECRET}", response=response)
    kind, code, message = describe_exception(http_error, literals=(SECRET,))
    assert (kind, code) == ("http_5xx", "500")
    assert TOKEN not in message and SECRET not in message and EMAIL not in message
    assert "Internal Server Error" in message

    timeout = requests.Timeout(f"timed out {response.url}")
    assert describe_exception(timeout) == ("timeout", "timeout", "timeout")

    connection = requests.ConnectionError(f"failed https://api.example/a?token={TOKEN} for {EMAIL}")
    kind, code, message = describe_exception(connection, literals=(SECRET,))
    assert kind == "connection"
    assert TOKEN not in message and EMAIL not in message
    assert "token=[redacted]" in message

    api = SolArkAPIError(2, f"No Permissions for {EMAIL}", {"token": TOKEN, "email": EMAIL, "password": SECRET})
    kind, code, message = describe_exception(api)
    assert (kind, code) == ("upstream", "2")
    assert TOKEN not in message and EMAIL not in message and "No Permissions" in message

    auth = SolArkAuthError(401, f"bad token={TOKEN}", {"password": SECRET})
    kind, code, message = describe_exception(auth, literals=(SECRET,))
    assert kind == "auth"
    assert TOKEN not in message and SECRET not in message


def test_json_shape_matches_html_fields():
    state = _status()
    state.set_configured_plant_id(137067)
    state.record_success(plant_id=137067, plant_name="Sean Wells plant", energy_today_kwh=42.6, now=NOW - 20)
    state.note_sleep(60, reason="poll", now=NOW - 20)
    metrics = build_metrics()
    metrics.exporter_up.set(1)
    payload = build_status(state, registry=metrics.registry, now=NOW)
    assert set(payload) == {
        "overall",
        "overall_phrase",
        "exporter",
        "version",
        "build",
        "mode",
        "connection",
        "poll_interval_s",
        "healthy_window_s",
        "hard_age_s",
        "state_rule",
        "last_success",
        "last_success_display",
        "age_seconds",
        "next_poll",
        "poll_display",
        "upstream",
        "upstream_display",
        "failures",
        "failures_display",
        "backoff_s",
        "backoff_display",
        "next_retry",
        "next_retry_display",
        "last_error",
        "last_error_display",
        "hint",
        "hints",
        "exporter_up",
        "series_count",
        "series_note",
        "series_display",
        "uptime_s",
        "uptime_display",
        "poll_thread_alive",
        "process_healthy",
        "process",
        "extras",
        "access_note",
        "read_only",
    }
    assert set(payload["extras"]) == {"plant_name", "plant_id", "energy_today_kwh", "energy_today", "snapshot"}
    assert set(payload["upstream"]) == {"code", "message"}
    assert set(payload["last_success"]) == {"at", "display", "relative"}
    assert payload["exporter"] == "prom_solark"
    assert payload["version"] == __version__
    assert payload["mode"] == "MySolArk cloud API"
    assert payload["connection"] == "cloud"
    assert payload["read_only"] is True
    assert payload["hints"] == list(HINTS)
    assert payload["exporter_up"] == 1
    assert payload["series_count"] > 0
    assert payload["access_note"] == access_note(10112)
    assert any("shared logins can kick other sessions" in hint for hint in payload["hints"])
    _assert_public(payload)

    page = render_html(payload)
    assert payload["access_note"] in page
    assert "http://127.0.0.1:10112/status" in page
    assert "docker3" in page
    assert "lab SSH hop" in page
    assert "no public listen" in page
    assert "shared logins can kick other sessions" in page
    assert "137067" in page
    assert "42.6 kWh" in page
    assert "Sean Wells plant" in page
    assert payload["state_rule"] in page
    for field in (
        payload["overall_phrase"],
        payload["upstream_display"],
        payload["poll_display"],
        payload["hint"],
        payload["process"],
        payload["series_display"],
    ):
        assert field in page


def test_html_escapes_plant_name_and_error_text():
    state = _status()
    state.record_success(
        plant_id=137067,
        plant_name="<script>alert(1)</script>",
        energy_today_kwh=1.0,
        now=NOW - 5,
    )
    state.record_failure(
        RuntimeError(f"boom <b>token={TOKEN}</b> {EMAIL}"),
        failures=1,
        backoff_s=60,
        phase="plant detail",
    )
    state.note_sleep(60, reason="backoff", now=NOW)
    payload = build_status(state, now=NOW)
    page = render_html(payload)
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert TOKEN not in page
    assert EMAIL not in page
    _assert_public(payload)


def test_daylight_zero_hint_when_up():
    state = _status()
    state.record_success(plant_id=137067, plant_name="Sean Wells plant", energy_today_kwh=0, now=NOW - 5)
    state.note_sleep(60, reason="poll", now=NOW - 5)
    payload = build_status(state, now=NOW)
    assert payload["overall"] == "UP"
    assert "upstream returned empty" in payload["hint"]
    assert "not that the scrape is down" in payload["hint"]


class _SnapshotCollector:
    def __init__(self, polls):
        self.polls = list(polls)
        self.login_calls = 0
        self.last_snapshot = None

    def login(self):
        self.login_calls += 1

    def collect_once(self):
        outcome = self.polls.pop(0) if self.polls else None
        if outcome:
            raise outcome
        self.last_snapshot = SimpleNamespace(plant_id=137067, plant_name="Sean Wells plant", energy_today_kwh=42.6)


def test_run_loop_keeps_last_known_snapshot_and_redacts_errors():
    metrics = build_metrics()
    status = PollState(poll_interval_s=60, listen_port=10112, literals=(SECRET, EMAIL), version=__version__)
    collector = _SnapshotCollector(
        [
            None,
            requests.ConnectionError(f"https://api.example/a?token={TOKEN} {EMAIL} {SECRET}"),
        ]
    )
    rng = random.Random()
    rng.uniform = lambda _a, _b: 1.0
    _run_loop(
        collector,
        60,
        metrics,
        sleep=lambda _delay: None,
        backoff=Backoff(60, 900, rng=rng),
        max_iterations=2,
        status=status,
    )
    payload = build_status(status, registry=metrics.registry)
    assert payload["overall"] == "DEGRADED"
    assert payload["extras"]["plant_id"] == 137067
    assert payload["extras"]["energy_today"].startswith("last-known 42.6")
    assert payload["failures"] == 1
    assert payload["next_retry"] is not None
    assert payload["next_retry"]["display"].endswith(" PT")
    assert payload["next_retry"]["relative"].startswith("~")
    assert payload["exporter_up"] == 0
    _assert_public(payload)
    assert metrics.registry.get_sample_value("solark_exporter_up") == 0


def test_version_matches_package_metadata():
    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    assert f'version = "{__version__}"' in pyproject
    assert __version__ == "0.3.1"


def _call(app, path, query="", method="GET"):
    captured = {}

    def start_response(status, headers, exc_info=None):
        captured["status"] = status
        captured["headers"] = dict(headers)

    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query,
        "SERVER_NAME": "127.0.0.1",
        "SERVER_PORT": "10112",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "wsgi.version": (1, 0),
        "wsgi.url_scheme": "http",
        "wsgi.input": io.BytesIO(),
        "wsgi.errors": io.StringIO(),
        "wsgi.multithread": False,
        "wsgi.multiprocess": False,
        "wsgi.run_once": False,
        "HTTP_ACCEPT": "text/plain",
        "HTTP_ACCEPT_ENCODING": "identity",
    }
    body = b"".join(app(environ, start_response))
    return captured["status"], captured["headers"], body


def test_http_status_routes_and_metrics_are_unchanged():
    metrics = build_metrics()
    metrics.exporter_up.set(0)
    state = _status()
    state.record_success(plant_id=137067, plant_name="Sean Wells plant", energy_today_kwh=42.6, now=NOW - 10)
    state.note_sleep(60, reason="poll", now=NOW - 10)
    payload = build_status(state, registry=metrics.registry, now=NOW)
    app = make_status_wsgi_app(metrics.registry, lambda: payload)
    plain = make_wsgi_app(metrics.registry)

    status, headers, body = _call(app, "/metrics")
    plain_status, plain_headers, plain_body = _call(plain, "/metrics")
    assert status == plain_status == "200 OK"
    assert body == plain_body
    assert headers == plain_headers
    assert b"solark_exporter_up" in body
    assert b"solark_exporter_last_success_timestamp_seconds" in body

    root_status, root_headers, root_body = _call(app, "/")
    plain_root_status, plain_root_headers, plain_root_body = _call(plain, "/")
    assert (root_status, root_headers, root_body) == (plain_root_status, plain_root_headers, plain_root_body)

    filtered, _, filtered_body = _call(app, "/metrics", "name[]=solark_exporter_up")
    plain_filtered, _, plain_filtered_body = _call(plain, "/metrics", "name[]=solark_exporter_up")
    assert filtered == plain_filtered == "200 OK"
    assert filtered_body == plain_filtered_body
    assert b"solark_exporter_up" in filtered_body
    assert b"solark_plant_info" not in filtered_body

    page_status, page_headers, page = _call(app, "/status")
    assert page_status == "200 OK"
    assert page_headers["Content-Type"].startswith("text/html")
    text = page.decode()
    assert "http://127.0.0.1:10112/status" in text
    assert "docker3" in text
    assert "lab SSH hop" in text
    assert "no public listen" in text
    assert "shared logins can kick other sessions" in text
    assert b"# HELP" not in page

    json_status, json_headers, raw = _call(app, "/status", "format=json")
    assert json_status == "200 OK"
    assert json_headers["Content-Type"].startswith("application/json")
    assert json.loads(raw) == payload

    other_status, other_headers, other = _call(app, "/status", "format=JSON")
    assert other_status == "200 OK"
    assert other_headers["Content-Type"].startswith("text/html")
    assert b"<html" in other

    denied, denied_headers, denied_body = _call(app, "/status", method="POST")
    assert denied == "405 Method Not Allowed"
    assert denied_headers["Allow"] == "GET"
    assert denied_body == b"Read-only. Use GET.\n"
    assert SECRET.encode() not in denied_body
