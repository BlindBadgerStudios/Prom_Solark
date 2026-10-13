from __future__ import annotations

import logging
import random
import threading
import time

import requests
from prometheus_client import start_http_server
from pysolark import SolArkAPIError, SolArkClient, SolArkTokenExpiredError
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .collector import SolArkCollector
from .config import load_config
from .metrics import build_metrics

try:  # pysolark >= 0.2.2
    from pysolark import SolArkAuthError
except ImportError:  # pragma: no cover - pysolark 0.2.1 (current pin) has no SolArkAuthError
    SolArkAuthError = SolArkTokenExpiredError

logger = logging.getLogger(__name__)

LOGIN_BACKOFF_CAP_SECONDS = 900


def build_session() -> requests.Session:
    """HTTP session that retries only idempotent GETs.

    POST /oauth/token is deliberately excluded: a retried login after a gateway 5xx can create
    extra sessions on the shared Sol-Ark account. 429 is retried and Retry-After is honored.
    """
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1,
        allowed_methods=frozenset({"GET"}),
        status_forcelist=frozenset({429, 502, 503, 504}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


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
        except Exception as exc:  # noqa: BLE001 - the poll loop must survive any failure
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
            logger.info("Next attempt in %.0fs", delay)
        finally:
            metrics.exporter_poll_duration.set(time.time() - start)
        sleep(delay)


def main() -> None:
    config = load_config()
    logging.basicConfig(level=config.log_level, format="%(asctime)s %(levelname)s %(message)s")
    metrics = build_metrics()
    session = build_session()
    client = SolArkClient(
        username=config.username, password=config.password, timeout=config.timeout_seconds, session=session
    )
    collector = SolArkCollector(client=client, config=config, metrics=metrics)
    logger.info("Starting exporter on port %d", config.listen_port)
    start_http_server(config.listen_port, registry=metrics.registry)
    thread = threading.Thread(target=_run_loop, args=(collector, config.poll_interval_seconds, metrics), daemon=True)
    thread.start()
    thread.join()
    logger.critical("Polling thread exited unexpectedly")
    raise SystemExit(1)


if __name__ == "__main__":
    main()
