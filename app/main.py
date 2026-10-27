from __future__ import annotations

import logging
import random
import threading
import time

import requests
from pysolark import SolArkAPIError, SolArkAuthError, SolArkClient, SolArkTokenExpiredError

from . import __version__
from .collector import SolArkCollector
from .config import load_config
from .metrics import build_metrics
from .server import start_exporter_http_server
from .status import PollState, build_status

logger = logging.getLogger(__name__)

LOGIN_BACKOFF_CAP_SECONDS = 900


def build_client(config) -> SolArkClient:
    """Create the Sol-Ark client. HTTP retries are left to PySolark (>= 0.2.2), which retries only
    idempotent GETs with backoff and honors 429 Retry-After, and never retries POST /oauth/token.
    The exporter deliberately adds no retry layer of its own, so a failure is not retried twice."""
    return SolArkClient(username=config.username, password=config.password, timeout=config.timeout_seconds)


def _is_auth_error(exc: Exception) -> bool:
    if isinstance(exc, (SolArkTokenExpiredError, SolArkAuthError)):
        return True
    return (
        isinstance(exc, requests.exceptions.HTTPError)
        and exc.response is not None
        and exc.response.status_code in (401, 403)
    )


class Backoff:
    """Jittered exponential backoff: initial, 2x, 4x, ... capped at ``cap`` seconds."""

    def __init__(self, initial: float, cap: float, rng: random.Random | None = None) -> None:
        self.initial = max(initial, 1.0)
        self.cap = max(cap, self.initial)
        self.failures = 0
        self._rng = rng or random.Random()

    def reset(self) -> None:
        self.failures = 0

    def next_delay(self) -> float:
        base = min(self.initial * (2**self.failures), self.cap)
        self.failures += 1
        return base * self._rng.uniform(0.9, 1.1)


def _log_failure(exc: Exception, what: str) -> None:
    if isinstance(exc, SolArkAPIError):
        # Log code/message only: the envelope payload can echo account details.
        logger.error("%s: Sol-Ark API error code=%s message=%s", what, exc.code, exc.message)
    else:
        logger.error("%s: %s: %s", what, type(exc).__name__, exc)
        logger.debug("%s traceback", what, exc_info=exc)


def _run_loop(
    collector: SolArkCollector,
    poll_interval_seconds: int,
    metrics,
    *,
    sleep=time.sleep,
    backoff: Backoff | None = None,
    max_iterations: int | None = None,
    status: PollState | None = None,
) -> None:
    """Poll forever. The first login happens here, not at startup, so a Sol-Ark outage
    leads to backed-off retries instead of a crash/restart login storm."""
    backoff = backoff or Backoff(poll_interval_seconds, LOGIN_BACKOFF_CAP_SECONDS)
    authenticated = False
    iterations = 0
    while max_iterations is None or iterations < max_iterations:
        iterations += 1
        start = time.time()
        delay: float = poll_interval_seconds
        try:
            if not authenticated:
                logger.info("Logging in to Sol-Ark")
                collector.login()
                authenticated = True
                logger.info("Sol-Ark login successful")
            collector.collect_once()
            metrics.exporter_up.set(1)
            metrics.exporter_last_success_timestamp.set(time.time())
            backoff.reset()
            logger.info("Poll succeeded in %.2fs", time.time() - start)
            if status is not None:
                snap = getattr(collector, "last_snapshot", None)
                status.record_success(
                    plant_id=getattr(snap, "plant_id", None),
                    plant_name=getattr(snap, "plant_name", None),
                    energy_today_kwh=getattr(snap, "energy_today_kwh", None),
                )
        except Exception as exc:  # noqa: BLE001 - the poll loop must survive any failure
            phase = "login" if (not authenticated or _is_auth_error(exc)) else "plant detail"
            if not authenticated:
                _log_failure(exc, "Sol-Ark login failed")
            elif _is_auth_error(exc):
                logger.warning("Sol-Ark rejected the token; will re-authenticate before the next poll")
                authenticated = False
            else:
                _log_failure(exc, "Polling cycle failed")
            metrics.exporter_errors_total.inc()
            metrics.exporter_up.set(0)
            delay = max(poll_interval_seconds, backoff.next_delay())
            if status is not None:
                status.record_failure(exc, failures=backoff.failures, backoff_s=delay, phase=phase)
            logger.info("Next attempt in %.0fs", delay)
        finally:
            metrics.exporter_poll_duration.set(time.time() - start)
        if status is not None:
            status.note_sleep(delay, reason="backoff" if backoff.failures else "poll")
        sleep(delay)


def main() -> None:
    config = load_config()
    logging.basicConfig(level=config.log_level, format="%(asctime)s %(levelname)s %(message)s")
    metrics = build_metrics()
    client = build_client(config)
    collector = SolArkCollector(client=client, config=config, metrics=metrics)
    status = PollState(
        poll_interval_s=config.poll_interval_seconds,
        listen_port=config.listen_port,
        literals=(config.username, config.password),
        version=__version__,
    )
    status.set_configured_plant_id(config.plant_id)
    # Explicitly report "not up" until the first successful poll; /metrics is now served
    # before the first login, so Prometheus sees solark_exporter_up 0 during a Sol-Ark outage.
    metrics.exporter_up.set(0)
    logger.info("Starting exporter on port %d (/metrics and /status)", config.listen_port)
    thread = threading.Thread(
        target=_run_loop,
        args=(collector, config.poll_interval_seconds, metrics),
        kwargs={"status": status},
        daemon=True,
    )
    status.bind_thread(thread)
    thread.start()
    start_exporter_http_server(
        config.listen_port,
        metrics.registry,
        lambda: build_status(status, registry=metrics.registry, version=__version__),
    )
    thread.join()
    logger.critical("Polling thread exited unexpectedly")
    raise SystemExit(1)


if __name__ == "__main__":
    main()
