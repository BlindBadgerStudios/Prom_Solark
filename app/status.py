"""Operator status payload and the shared UP / DEGRADED / DOWN rule.

This is the Sol-Ark copy of the status helper used by the energy exporters
(prom_tigo, prom_solark, prom_emvue). The repositories are separate on purpose;
there is no shared package. Copy this module and keep ``overall_state``,
``healthy_window_seconds``, and ``hard_age_seconds`` in lockstep so the three
exporters cannot drift.

Overall state (identical on all three exporters)
------------------------------------------------
Precedence, first match wins:

1. DOWN — the latest attempt is a hard auth failure (HTTP 401/403 or an auth
   envelope). This wins even when a previous success is still inside the
   healthy window.
2. DOWN — there has been no successful poll and the process has been running
   longer than the hard age cutoff, OR the last success is older than that
   cutoff.
3. DEGRADED — the poll thread is not alive, but a success is still inside the
   hard age cutoff (the process is not polling).
4. DEGRADED — the latest attempt failed with an upstream error (5xx, timeout,
   connection, DNS, TLS, 429, or any other non-auth error) and backoff is in
   effect. The process is still polling. This applies even when the previous
   success is still inside the healthy window.
5. UP — the last success is within the healthy age window, the latest attempt
   is not a hard auth failure, and the poll thread is alive.
6. DEGRADED — anything else that is not yet DOWN (for example a success that
   is older than the healthy window but still inside the hard cutoff, or a
   process that has not completed its first poll).

Windows
-------
- Healthy window = 2 x poll interval. A single slow cycle can still be UP when
  the previous success is younger than that and authentication succeeded.
- Hard age cutoff = 15 minutes. This is a fixed cutoff, not 3 x poll interval:
  at the default 60s poll, 3 x interval is only three minutes, which is shorter
  than one backoff climb and too quick to call the exporter DOWN. If 2 x poll
  interval is longer than 15 minutes, the cutoff expands to that healthy window
  so a healthy slow poll is not marked DOWN between successes.

DEGRADED and DOWN keep the last successful plant snapshot (name, plant id,
energy today). Status output never includes secrets, tokens, passwords,
Authorization headers, cookies, account emails, latitude, longitude, or street
addresses.
"""

from __future__ import annotations

import html
import math
import os
import re
import socket
import ssl
import threading
import time
from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app import __version__

EXPORTER_NAME = "prom_solark"
EXPORTER_MODE = "MySolArk cloud API"
CONNECTION_MODE = "cloud"
EXPORTER_UP_METRIC = "solark_exporter_up"

# 15 minutes. See the module docstring for why this is not 3 x poll interval.
HARD_AGE_SECONDS = 15 * 60

PACIFIC = ZoneInfo("America/Los_Angeles")
MAX_MESSAGE_CHARS = 200

# Shown on HTML and JSON so the wording stays available to operators and bots.
HINTS: tuple[str, ...] = (
    "401/403: check API token/cred mount; do not paste into chat.",
    "Timeout: check docker3 outbound / upstream API.",
    "429: rate limit; backoff is intentional.",
    "TLS/DNS: check the network path from docker3.",
    "Zero or absent metrics in daylight with UP means the upstream returned empty, not that the scrape is down.",
    "Solark: shared logins can kick other sessions.",
    "Do not restart-loop.",
)

_SECRET_PARAM = re.compile(
    r"(?i)(access_token|refresh_token|id_token|token|password|passwd|pwd|secret|"
    r"api_key|apikey|client_secret|authorization|cookie|session|signature|sig)(=)([^&#\s]+)"
)
_USERINFO = re.compile(r"([a-z][a-z0-9+\-.]*://[^/\s:@]+):([^@\s]+)@", re.IGNORECASE)
_BEARER = re.compile(r"(?i)\bBearer\s+\S+")
_AUTH_HEADER = re.compile(r"(?i)\bAuthorization\s*[:=]\s*(?:Bearer\s+)?\S+")
_COOKIE_HEADER = re.compile(r"(?i)\bCookie\s*[:=]\s*\S+")
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b")
_DNS_MARKERS = (
    "name or service not known",
    "temporary failure in name resolution",
    "nodename nor servname",
    "getaddrinfo",
    "name resolution",
)


def healthy_window_seconds(poll_interval_s: float) -> float:
    """Return the UP window: 2 x the poll interval, at least one second."""
    return max(2.0 * float(poll_interval_s), 1.0)


def hard_age_seconds(poll_interval_s: float) -> float:
    """Return the DOWN cutoff. See the module docstring."""
    return max(float(HARD_AGE_SECONDS), healthy_window_seconds(poll_interval_s))


def overall_state(
    *,
    now: float,
    started_at: float,
    last_success_at: float | None,
    poll_interval_s: float,
    failure_kind: str | None,
    poll_thread_alive: bool,
) -> str:
    """Return ``UP``, ``DEGRADED``, or ``DOWN`` using the shared rule.

    ``failure_kind`` is ``None`` when the latest attempt succeeded, ``"auth"``
    for a hard auth failure, and any other short code for an upstream failure
    the process is backing off from.
    """
    age, cutoff_age = _ages(now, started_at, last_success_at)
    hard = hard_age_seconds(poll_interval_s)
    healthy = healthy_window_seconds(poll_interval_s)
    if failure_kind == "auth":
        return "DOWN"
    if cutoff_age > hard:
        return "DOWN"
    if not poll_thread_alive:
        return "DEGRADED"
    if failure_kind is not None:
        return "DEGRADED"
    if last_success_at is not None and age is not None and age <= healthy:
        return "UP"
    return "DEGRADED"


def redact(text: str, *, literals: Iterable[str] = (), limit: int = MAX_MESSAGE_CHARS) -> str:
    """Remove secrets and collapse a message to one short line.

    Tokenized URLs, bearer tokens, Authorization and Cookie headers, JWTs,
    account emails, and any caller-supplied secret literals are replaced.
    The result is truncated to ``limit`` characters.
    """
    if not text:
        return ""
    cleaned = text.replace("\r", " ").replace("\n", " ")
    for literal in literals:
        if isinstance(literal, str) and len(literal) >= 4 and literal in cleaned:
            cleaned = cleaned.replace(literal, "[redacted]")
    cleaned = _USERINFO.sub(r"\1:[redacted]@", cleaned)
    cleaned = _BEARER.sub("Bearer [redacted]", cleaned)
    cleaned = _AUTH_HEADER.sub("Authorization: [redacted]", cleaned)
    cleaned = _COOKIE_HEADER.sub("Cookie: [redacted]", cleaned)
    cleaned = _SECRET_PARAM.sub(r"\1\2[redacted]", cleaned)
    cleaned = _JWT.sub("[redacted]", cleaned)
    cleaned = _EMAIL.sub("[redacted-email]", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip() + "..."


def describe_exception(exc: BaseException, *, literals: Iterable[str] = ()) -> tuple[str, str, str]:
    """Return ``(kind, code, message)`` for an upstream failure.

    HTTP failures contribute status and reason only. Response bodies are not
    read. Sol-Ark errors contribute their code and message, never the payload.
    """
    mro = {cls.__name__ for cls in type(exc).__mro__}
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    reason = getattr(response, "reason", None) or ""

    if "SolArkAuthError" in mro or "SolArkTokenExpiredError" in mro:
        code = _code(getattr(exc, "code", None), default="401")
        message = redact(_public_message(exc), literals=literals) or "auth failure"
        return "auth", code, message
    if isinstance(status, int) and status in (401, 403):
        return "auth", str(status), redact(f"{status} {reason}".strip(), literals=literals)
    if isinstance(status, int) and status == 429:
        return "rate_limit", "429", redact(f"429 {reason}".strip() or "429 Too Many Requests", literals=literals)
    if isinstance(status, int) and status >= 500:
        return "http_5xx", str(status), redact(f"{status} {reason}".strip(), literals=literals)
    if isinstance(status, int):
        return "upstream", str(status), redact(f"{status} {reason}".strip(), literals=literals)
    if "SolArkAPIError" in mro:
        code = _code(getattr(exc, "code", None), default="error")
        message = redact(_public_message(exc), literals=literals) or "Sol-Ark API error"
        return "upstream", code, message
    if type(exc).__name__ == "ValueError" and "not authenticated" in str(exc).lower():
        return "auth", "auth", "Not authenticated"

    if isinstance(exc, TimeoutError) or mro & {"Timeout", "ConnectTimeout", "ReadTimeout"}:
        return "timeout", "timeout", "timeout"
    if "SSLError" in mro or isinstance(exc, ssl.SSLError):
        detail = redact(str(exc), literals=literals) or "TLS error"
        return "tls", "tls", detail
    if isinstance(exc, socket.gaierror) or "gaierror" in mro:
        detail = redact(str(exc), literals=literals) or "DNS error"
        return "dns", "dns", detail
    if isinstance(exc, ConnectionError) or "ConnectionError" in mro:
        detail = redact(str(exc), literals=literals)
        lowered = detail.lower()
        if any(marker in lowered for marker in _DNS_MARKERS):
            return "dns", "dns", detail or "DNS error"
        if "ssl" in lowered or "tls" in lowered or "certificate" in lowered:
            return "tls", "tls", detail or "TLS error"
        return "connection", "connection", detail or "connection error"

    detail = redact(str(exc), literals=literals) or type(exc).__name__
    return "upstream", type(exc).__name__, detail


def access_note(port: int) -> str:
    return f"Reachable on docker3 as http://127.0.0.1:{int(port)}/status via the lab SSH hop (no public listen)."


def hint_for(
    *,
    overall: str,
    failure_kind: str | None,
    last_success_at: float | None,
    energy_today_kwh: float | None,
    series_count: int,
) -> str:
    """Contextual operator hint. Static catalogue text lives in ``HINTS``."""
    if overall == "UP":
        if energy_today_kwh == 0 or series_count == 0:
            return (
                "No action needed. Zero or absent metrics in daylight with UP means the upstream "
                "returned empty, not that the scrape is down."
            )
        return "No action needed. Prefer SOLARK_PLANT_ID set so the plant is not auto-selected."
    if failure_kind == "auth":
        return (
            "Check API cred mount (SOLARK_*). Do not paste secrets into chat. "
            "Avoid thrash restarts — shared logins can kick other sessions."
        )
    if failure_kind == "rate_limit":
        return "Rate limit; backoff is intentional. Do not restart-loop. Shared logins can kick other sessions."
    if failure_kind in {"timeout", "connection"}:
        return (
            "Timeout: check docker3 outbound / MySolArk. "
            "If the wrong plant is selected, set SOLARK_PLANT_ID and restart once."
        )
    if failure_kind in {"tls", "dns"}:
        return (
            "TLS/DNS: check the network path from docker3. Do not restart-loop. Shared logins can kick other sessions."
        )
    if failure_kind == "http_5xx":
        return (
            "Upstream error: check docker3 outbound / upstream API. "
            "Do not restart-loop. Shared logins can kick other sessions."
        )
    if last_success_at is None and failure_kind is None:
        return "Waiting for the first successful poll. Do not restart-loop. Shared logins can kick other sessions."
    return "Do not restart-loop. Shared logins can kick other sessions."


class PollState:
    """Thread-safe poll memory for ``/status``.

    Secret literals are kept only so error text can be scrubbed. They are never
    copied into the status payload.
    """

    def __init__(
        self,
        *,
        poll_interval_s: int,
        listen_port: int,
        started_at: float | None = None,
        literals: Iterable[str] = (),
        thread_alive: Callable[[], bool] | None = None,
        version: str = __version__,
    ) -> None:
        self.poll_interval_s = int(poll_interval_s)
        self.listen_port = int(listen_port)
        self.started_at = time.time() if started_at is None else float(started_at)
        self.version = version
        self._literals = tuple(item for item in literals if isinstance(item, str) and len(item) >= 4)
        self._thread_alive = thread_alive or (lambda: True)
        self._lock = threading.Lock()
        self.last_success_at: float | None = None
        self.consecutive_failures = 0
        self.backoff_s: float | None = None
        self.next_wake_at: float | None = None
        self.wake_reason = "poll"
        self.failure_kind: str | None = None
        self.upstream_code: str | None = None
        self.upstream_message: str | None = None
        self.last_error_code: str | None = None
        self.last_error_message: str | None = None
        self.plant_id: int | None = None
        self.plant_name: str | None = None
        self.energy_today_kwh: float | None = None
        self.snapshot_source = "none"
        self.exporter_up = 0

    def bind_thread(self, thread: threading.Thread) -> None:
        self._thread_alive = thread.is_alive

    def set_configured_plant_id(self, plant_id: int | None) -> None:
        if plant_id is None:
            return
        with self._lock:
            if self.snapshot_source == "none":
                self.plant_id = int(plant_id)
                self.snapshot_source = "configured"

    def record_success(
        self,
        *,
        plant_id: int | None,
        plant_name: str | None,
        energy_today_kwh: float | None,
        now: float | None = None,
    ) -> None:
        moment = time.time() if now is None else float(now)
        with self._lock:
            self.last_success_at = moment
            self.consecutive_failures = 0
            self.backoff_s = None
            self.failure_kind = None
            self.upstream_code = "200"
            self.upstream_message = "OK · plant detail"
            self.last_error_code = None
            self.last_error_message = None
            self.exporter_up = 1
            if plant_id is not None:
                self.plant_id = int(plant_id)
            if plant_name:
                self.plant_name = plant_name
            if energy_today_kwh is not None and _finite(energy_today_kwh) is not None:
                self.energy_today_kwh = float(energy_today_kwh)
            if plant_id is not None or plant_name or energy_today_kwh is not None:
                self.snapshot_source = "live"

    def record_failure(
        self,
        exc: BaseException,
        *,
        failures: int,
        backoff_s: float,
        phase: str,
    ) -> None:
        kind, code, message = describe_exception(exc, literals=self._literals)
        upstream = redact(f"{message} · {phase}" if phase else message, literals=self._literals)
        with self._lock:
            self.failure_kind = kind
            self.consecutive_failures = int(failures)
            self.backoff_s = float(backoff_s)
            self.upstream_code = code
            self.upstream_message = upstream
            self.last_error_code = code
            self.last_error_message = message
            self.exporter_up = 0
            if self.snapshot_source == "live":
                self.snapshot_source = "last-known"

    def note_sleep(self, delay: float, *, reason: str, now: float | None = None) -> None:
        moment = time.time() if now is None else float(now)
        with self._lock:
            self.next_wake_at = moment + float(delay)
            self.wake_reason = reason

    def thread_alive(self) -> bool:
        try:
            return bool(self._thread_alive())
        except Exception:  # noqa: BLE001 - a status read must not take the process down
            return False

    def secret_literals(self) -> tuple[str, ...]:
        return self._literals

    def view(self) -> dict[str, Any]:
        """Copy the operator-visible fields. Secret literals are not included."""
        with self._lock:
            return {
                "poll_interval_s": self.poll_interval_s,
                "listen_port": self.listen_port,
                "started_at": self.started_at,
                "version": self.version,
                "last_success_at": self.last_success_at,
                "consecutive_failures": self.consecutive_failures,
                "backoff_s": self.backoff_s,
                "next_wake_at": self.next_wake_at,
                "wake_reason": self.wake_reason,
                "failure_kind": self.failure_kind,
                "upstream_code": self.upstream_code,
                "upstream_message": self.upstream_message,
                "last_error_code": self.last_error_code,
                "last_error_message": self.last_error_message,
                "plant_id": self.plant_id,
                "plant_name": self.plant_name,
                "energy_today_kwh": self.energy_today_kwh,
                "snapshot_source": self.snapshot_source,
                "exporter_up": self.exporter_up,
            }


def build_status(
    state: PollState,
    *,
    registry: Any = None,
    now: float | None = None,
    version: str | None = None,
) -> dict[str, Any]:
    """Build the field set shared by HTML and JSON."""
    moment = time.time() if now is None else float(now)
    snap = state.view()
    literals = state.secret_literals()
    alive = state.thread_alive()
    overall = overall_state(
        now=moment,
        started_at=snap["started_at"],
        last_success_at=snap["last_success_at"],
        poll_interval_s=snap["poll_interval_s"],
        failure_kind=snap["failure_kind"],
        poll_thread_alive=alive,
    )
    age, _cutoff = _ages(moment, snap["started_at"], snap["last_success_at"])
    age_seconds = None if age is None else int(age)
    exporter_up = _exporter_up(registry, snap["exporter_up"])
    series_count = _series_count(registry)
    energy = _finite(snap["energy_today_kwh"])
    last_known = overall != "UP" and snap["snapshot_source"] == "last-known"
    snapshot = snap["snapshot_source"]
    if overall == "UP" and snapshot == "live":
        snapshot = "live"
    elif last_known:
        snapshot = "last-known"

    last_success = _stamp(snap["last_success_at"], moment) if snap["last_success_at"] is not None else None
    backing_off = snap["wake_reason"] == "backoff" and snap["next_wake_at"] is not None
    if backing_off:
        next_poll = {"at": None, "display": "held for backoff", "relative": "held for backoff"}
        next_retry = _stamp(snap["next_wake_at"], moment, future=True)
    elif snap["next_wake_at"] is not None:
        next_poll = _stamp(snap["next_wake_at"], moment, future=True)
        next_retry = None
    else:
        next_poll = {"at": None, "display": "waiting for first poll", "relative": "waiting for first poll"}
        next_retry = None

    if snap["upstream_code"] is None and snap["upstream_message"] is None:
        upstream_code = "none"
        upstream_message = "no poll yet"
    else:
        upstream_code = snap["upstream_code"] or "none"
        upstream_message = redact(snap["upstream_message"] or "", literals=literals)
    last_error = None
    if snap["last_error_code"] or snap["last_error_message"]:
        last_error = {
            "code": snap["last_error_code"] or "error",
            "message": redact(snap["last_error_message"] or "", literals=literals),
        }

    phrase = _overall_phrase(overall, age_seconds, snap["last_success_at"])
    process_healthy = bool(alive and overall == "UP" and exporter_up == 1)
    if not alive:
        process = "Poll thread stopped"
    elif overall == "UP":
        process = "Healthy"
    else:
        process = "Running · poll thread alive"

    failures = int(snap["consecutive_failures"])
    backoff_s = snap["backoff_s"] if snap["failure_kind"] is not None else None
    energy_display = None if energy is None else _format_energy(energy, last_known=last_known)
    hint = hint_for(
        overall=overall,
        failure_kind=snap["failure_kind"],
        last_success_at=snap["last_success_at"],
        energy_today_kwh=energy,
        series_count=series_count,
    )
    build = _build_sha()
    version_text = version or snap["version"]
    uptime_s = max(0, int(moment - snap["started_at"]))
    return {
        "overall": overall,
        "overall_phrase": phrase,
        "exporter": EXPORTER_NAME,
        "version": version_text,
        "build": build,
        "mode": EXPORTER_MODE,
        "connection": CONNECTION_MODE,
        "poll_interval_s": snap["poll_interval_s"],
        "healthy_window_s": healthy_window_seconds(snap["poll_interval_s"]),
        "hard_age_s": hard_age_seconds(snap["poll_interval_s"]),
        "state_rule": (
            "UP: last success within 2x poll interval and no hard auth failure. "
            "DEGRADED: upstream 5xx/timeout (or other non-auth failure) with backoff while still polling. "
            "DOWN: auth failure, or no success past the 15 minute hard age cutoff."
        ),
        "last_success": last_success,
        "last_success_display": _combined_stamp(last_success) if last_success else "none yet",
        "age_seconds": age_seconds,
        "next_poll": next_poll,
        "poll_display": f"every {snap['poll_interval_s']}s · next {next_poll['relative']}",
        "upstream": {"code": upstream_code, "message": upstream_message},
        "upstream_display": _upstream_display(upstream_code, upstream_message),
        "failures": failures,
        "failures_display": f"{failures} consecutive" if failures else "0",
        "backoff_s": backoff_s,
        "backoff_display": "None" if backoff_s is None else _format_duration(backoff_s),
        "next_retry": next_retry,
        "next_retry_display": _combined_stamp(next_retry) if next_retry else "n/a",
        "last_error": last_error,
        "last_error_display": "None" if last_error is None else f"{last_error['code']} · {last_error['message']}",
        "hint": hint,
        "hints": list(HINTS),
        "exporter_up": exporter_up,
        "series_count": series_count,
        "series_note": "live" if overall == "UP" else "stale",
        "series_display": f"~{series_count}" if overall == "UP" else f"stale · {series_count}",
        "uptime_s": uptime_s,
        "uptime_display": _format_duration(uptime_s),
        "poll_thread_alive": alive,
        "process_healthy": process_healthy,
        "process": process,
        "extras": {
            "plant_name": snap["plant_name"],
            "plant_id": snap["plant_id"],
            "energy_today_kwh": energy,
            "energy_today": energy_display,
            "snapshot": snapshot,
        },
        "access_note": access_note(snap["listen_port"]),
        "read_only": True,
    }


def render_html(payload: dict[str, Any]) -> str:
    """Render the operator page from the same fields ``build_status`` returns."""
    overall = payload["overall"] if payload["overall"] in {"UP", "DEGRADED", "DOWN"} else "DOWN"
    build = f" · build {payload['build']}" if payload.get("build") else ""
    extras = payload["extras"]
    sections = [
        (
            "Overview",
            _dl(
                [
                    ("Overall", None),
                    ("Exporter", f"{payload['exporter']} {payload['version']}{build}"),
                    ("Mode", payload["mode"]),
                    ("Last success", payload["last_success_display"]),
                    ("Poll", payload["poll_display"]),
                    ("Upstream", payload["upstream_display"]),
                    ("Failures", payload["failures_display"]),
                    ("Backoff", payload["backoff_display"]),
                    ("Next retry", payload["next_retry_display"]),
                ],
                overall_html=f'<span class="{overall}">{html.escape(payload["overall_phrase"])}</span>',
            ),
        ),
        (
            "Error and hint",
            _dl(
                [
                    ("Last error", payload["last_error_display"]),
                    ("Hint", payload["hint"]),
                ]
            ),
        ),
        (
            "Series and process",
            _dl(
                [
                    ("exporter_up", str(payload["exporter_up"])),
                    ("Series", payload["series_display"]),
                    ("Uptime", payload["uptime_display"]),
                    ("Process", payload["process"]),
                ]
            ),
        ),
        (
            "Exporter extras",
            _dl(
                [
                    ("Plant", extras["plant_name"] or "unknown"),
                    ("Plant ID", "unknown" if extras["plant_id"] is None else str(extras["plant_id"])),
                    ("Energy today", extras["energy_today"] or "unknown"),
                    ("Snapshot", extras["snapshot"]),
                ]
            ),
        ),
    ]
    blocks = []
    for title, body in sections:
        blocks.append(f"<section><h2>{html.escape(title)}</h2>{body}</section>")
    hints = "".join(f"<li>{html.escape(item)}</li>" for item in payload["hints"])
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{html.escape(payload["exporter"])} · /status</title>
<style>
  body {{ font: 15px/1.45 -apple-system, "Segoe UI", sans-serif; margin: 2rem; color: #1b1b1b; background: #f6f5f2; }}
  h1 {{ font-size: 1.45rem; margin-bottom: 0.25rem; }}
  h2 {{ font-size: 1rem; margin: 0 0 0.5rem; }}
  .banner {{ background: #eef3f8; border: 1px solid #c5d4e4; padding: 0.75rem 1rem; margin: 1rem 0; }}
  .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }}
  section {{ background: #fff; border: 1px solid #ddd; padding: 0.75rem 1rem; }}
  dl {{ margin: 0; }}
  dt {{ color: #555; font-size: 0.85rem; }}
  dd {{ margin: 0 0 0.65rem; }}
  .UP {{ color: #0a7a32; font-weight: 700; }}
  .DEGRADED {{ color: #8a5a00; font-weight: 700; }}
  .DOWN {{ color: #a32020; font-weight: 700; }}
  .rule {{ color: #444; font-size: 0.9rem; }}
  @media (max-width: 800px) {{ .grid {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
  <h1>{html.escape(payload["exporter"])} · /status</h1>
  <p class="banner">{html.escape(payload["access_note"])}</p>
  <p class="rule">{html.escape(payload["state_rule"])}</p>
  <div class="grid">
    {"".join(blocks)}
  </div>
  <section>
    <h2>Hints</h2>
    <ul>{hints}</ul>
  </section>
  <p class="rule">Read-only. This page does not change the exporter or the plant.</p>
</body>
</html>
"""


def _ages(now: float, started_at: float, last_success_at: float | None) -> tuple[float | None, float]:
    if last_success_at is None:
        return None, max(0.0, now - started_at)
    age = max(0.0, now - last_success_at)
    return age, age


def _overall_phrase(overall: str, age_seconds: int | None, last_success_at: float | None) -> str:
    if overall == "UP":
        return "UP"
    if last_success_at is None or age_seconds is None:
        return f"{overall} — no success yet"
    return f"{overall} — no success in {_format_duration(age_seconds)}"


def _stamp(ts: float, now: float, *, future: bool = False) -> dict[str, str]:
    delta = (ts - now) if future else (now - ts)
    relative = _format_eta(delta) if future else f"{_format_duration(delta)} ago"
    return {"at": _iso_pt(ts), "display": _format_absolute_pt(ts), "relative": relative}


def _combined_stamp(stamp: dict[str, str] | None) -> str:
    if not stamp:
        return "n/a"
    return f"{stamp['display']} · {stamp['relative']}"


def _iso_pt(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=PACIFIC).isoformat(timespec="seconds")


def _format_absolute_pt(ts: float) -> str:
    dt = datetime.fromtimestamp(ts, tz=PACIFIC)
    return f"{dt.strftime('%b')} {dt.day}, {dt.year} {dt.strftime('%H:%M:%S')} PT"


def _format_duration(seconds: float) -> str:
    whole = max(0, int(seconds))
    if whole < 60:
        return f"{whole}s"
    minutes = whole // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    rem_m = minutes % 60
    if hours < 48:
        return f"{hours}h {rem_m}m" if rem_m else f"{hours}h"
    days = hours // 24
    rem_h = hours % 24
    return f"{days}d {rem_h}h" if rem_h else f"{days}d"


def _format_eta(seconds: float) -> str:
    whole = max(0, round(seconds))
    if whole < 60:
        return f"~{whole}s"
    minutes = max(1, round(whole / 60))
    if minutes < 90:
        return f"~{minutes} min"
    hours = minutes // 60
    rem_m = minutes % 60
    return f"~{hours}h {rem_m}m" if rem_m else f"~{hours}h"


def _format_energy(kwh: float, *, last_known: bool) -> str:
    text = f"{kwh:.1f} kWh"
    return f"last-known {text}" if last_known else text


def _upstream_display(code: str, message: str) -> str:
    if code in {"", "none"}:
        return message or "no poll yet"
    if message.lower().startswith(str(code).lower()):
        return message
    return f"{code} {message}".strip()


def _public_message(exc: BaseException) -> str:
    message = getattr(exc, "message", None)
    if isinstance(message, str) and message:
        return message
    return ""


def _code(value: Any, *, default: str) -> str:
    if value is None or value == "":
        return default
    return str(value)


def _finite(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _exporter_up(registry: Any, fallback: int) -> int:
    if registry is None:
        return 1 if fallback else 0
    sample = registry.get_sample_value(EXPORTER_UP_METRIC)
    if sample is None:
        return 1 if fallback else 0
    return 1 if sample >= 1 else 0


def _series_count(registry: Any) -> int:
    if registry is None:
        return 0
    total = 0
    for metric in registry.collect():
        total += len(metric.samples)
    return total


def _build_sha() -> str | None:
    raw = os.getenv("BUILD_SHA") or os.getenv("GIT_SHA") or os.getenv("SOURCE_COMMIT") or ""
    raw = raw.strip()
    if re.fullmatch(r"[0-9a-fA-F]{7,64}", raw):
        return raw[:12]
    return None


def _dl(pairs: list[tuple[str, str | None]], *, overall_html: str | None = None) -> str:
    rows: list[str] = []
    for label, value in pairs:
        if label == "Overall" and overall_html is not None:
            rendered = overall_html
        else:
            rendered = html.escape("" if value is None else str(value))
        rows.append(f"<dt>{html.escape(label)}</dt><dd>{rendered}</dd>")
    return "<dl>" + "".join(rows) + "</dl>"
