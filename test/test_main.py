from __future__ import annotations

import random
from types import SimpleNamespace

import pytest
import requests
from pysolark import SolArkAPIError, SolArkTokenExpiredError

from app.main import Backoff, _is_auth_error, _run_loop, build_client
from app.metrics import build_metrics


def test_client_retries_once_in_pysolark_only():
    """No double retry: PySolark owns GET retries; the session carries no urllib3 retry layer."""
    config = SimpleNamespace(username="user@example.invalid", password="x", timeout_seconds=5)
    client = build_client(config)
    assert client.max_retries == 3
    adapter_retry = client.session.get_adapter("https://api.example.invalid").max_retries
    assert adapter_retry.total == 0


class ScriptedCollector:
    """Collector stub: each entry in ``logins``/``polls`` is None (success) or an exception to raise."""

    def __init__(self, logins=(), polls=()):
        self.logins = list(logins)
        self.polls = list(polls)
        self.login_calls = 0
        self.poll_calls = 0

    def login(self):
        self.login_calls += 1
        outcome = self.logins.pop(0) if self.logins else None
        if outcome:
            raise outcome

    def collect_once(self):
        self.poll_calls += 1
        outcome = self.polls.pop(0) if self.polls else None
        if outcome:
            raise outcome


def _value(metrics, name):
    return metrics.registry.get_sample_value(name)


def _http_error(status):
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError(response=response)


def _no_jitter_backoff(initial=60, cap=900):
    rng = random.Random()
    rng.uniform = lambda a, b: 1.0
    return Backoff(initial, cap, rng=rng)


def test_backoff_doubles_and_caps():
    backoff = _no_jitter_backoff(60, 300)
    assert [backoff.next_delay() for _ in range(5)] == [60, 120, 240, 300, 300]
    backoff.reset()
    assert backoff.next_delay() == 60


def test_backoff_jitter_stays_within_ten_percent():
    backoff = Backoff(100, 900, rng=random.Random(1))
    assert 90 <= backoff.next_delay() <= 110


def test_startup_login_failure_backs_off_without_crashing():
    metrics = build_metrics()
    outage = requests.ConnectionError("sol-ark down")
    collector = ScriptedCollector(logins=[outage, outage, outage, None])
    sleeps = []
    _run_loop(collector, 60, metrics, sleep=sleeps.append, backoff=_no_jitter_backoff(), max_iterations=4)
    assert collector.login_calls == 4
    assert collector.poll_calls == 1
    assert sleeps == [60, 120, 240, 60]
    assert _value(metrics, "solark_exporter_up") == 1
    assert _value(metrics, "solark_exporter_errors_total") == 3


def test_login_happens_once_across_successful_polls():
    metrics = build_metrics()
    collector = ScriptedCollector()
    _run_loop(collector, 60, metrics, sleep=lambda _: None, max_iterations=3)
    assert collector.login_calls == 1
    assert collector.poll_calls == 3


def test_auth_error_triggers_relogin_on_next_iteration():
    metrics = build_metrics()
    collector = ScriptedCollector(polls=[SolArkTokenExpiredError(), None])
    sleeps = []
    _run_loop(collector, 60, metrics, sleep=sleeps.append, backoff=_no_jitter_backoff(), max_iterations=2)
    assert collector.login_calls == 2
    assert collector.poll_calls == 2
    assert _value(metrics, "solark_exporter_up") == 1


def test_non_auth_poll_error_does_not_relogin():
    metrics = build_metrics()
    collector = ScriptedCollector(polls=[SolArkAPIError(2, "No Permissions"), None])
    _run_loop(collector, 60, metrics, sleep=lambda _: None, backoff=_no_jitter_backoff(), max_iterations=2)
    assert collector.login_calls == 1
    assert collector.poll_calls == 2


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (SolArkTokenExpiredError(), True),
        (_http_error(401), True),
        (_http_error(403), True),
        (_http_error(500), False),
        (SolArkAPIError(2, "No Permissions"), False),
    ],
)
def test_is_auth_error(exc, expected):
    assert _is_auth_error(exc) is expected


def test_exporter_up_starts_at_zero_and_stays_zero_while_login_fails():
    metrics = build_metrics()
    assert _value(metrics, "solark_exporter_up") == 0
    collector = ScriptedCollector(logins=[requests.ConnectionError("down")])
    _run_loop(collector, 60, metrics, sleep=lambda _: None, backoff=_no_jitter_backoff(), max_iterations=1)
    assert _value(metrics, "solark_exporter_up") == 0
