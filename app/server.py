"""HTTP server for /metrics (unchanged) and the read-only /status page.

``/metrics`` and every other path are delegated to prometheus_client's WSGI app,
which is what ``start_http_server`` uses. Only ``/status`` is handled here.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from socketserver import ThreadingMixIn
from typing import Any
from urllib.parse import parse_qs
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from prometheus_client.exposition import make_wsgi_app

from .status import render_html

logger = logging.getLogger(__name__)


class _SilentHandler(WSGIRequestHandler):
    """Match prometheus_client: do not log request lines (paths can be noisy)."""

    def log_message(self, format: str, *args: Any) -> None:
        return


class _ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


def make_status_wsgi_app(registry: Any, build_payload: Callable[[], dict[str, Any]]) -> Callable:
    """WSGI app: ``GET /status`` is HTML, ``GET /status?format=json`` is JSON.

    Every other request, including ``GET /metrics``, is served by the Prometheus
    app unchanged.
    """
    metrics_app = make_wsgi_app(registry)

    def app(environ: dict, start_response: Callable):
        path = environ.get("PATH_INFO") or ""
        if path != "/" and path.endswith("/"):
            path = path.rstrip("/")
        if path != "/status":
            return metrics_app(environ, start_response)

        method = environ.get("REQUEST_METHOD", "GET").upper()
        if method == "OPTIONS":
            start_response("200 OK", [("Allow", "OPTIONS,GET"), ("Content-Length", "0")])
            return [b""]
        if method != "GET":
            body = b"Read-only. Use GET.\n"
            start_response(
                "405 Method Not Allowed",
                [
                    ("Allow", "GET"),
                    ("Content-Type", "text/plain; charset=utf-8"),
                    ("Content-Length", str(len(body))),
                ],
            )
            return [body]

        try:
            payload = build_payload()
            params = parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=False)
            if params.get("format", [""])[0] == "json":
                body = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
                content_type = "application/json; charset=utf-8"
            else:
                body = render_html(payload).encode("utf-8")
                content_type = "text/html; charset=utf-8"
        except Exception as exc:  # noqa: BLE001 - never echo internal errors to the client
            logger.error("status page failed: %s", type(exc).__name__)
            body = b"status unavailable\n"
            start_response(
                "500 Internal Server Error",
                [("Content-Type", "text/plain; charset=utf-8"), ("Content-Length", str(len(body)))],
            )
            return [body]

        start_response(
            "200 OK",
            [
                ("Content-Type", content_type),
                ("Content-Length", str(len(body))),
                ("Cache-Control", "no-store"),
                ("X-Content-Type-Options", "nosniff"),
            ],
        )
        return [body]

    return app


def start_exporter_http_server(
    port: int,
    registry: Any,
    build_payload: Callable[[], dict[str, Any]],
    addr: str = "0.0.0.0",
):
    """Serve /metrics and /status. The server thread is a daemon, as with prometheus_client."""
    app = make_status_wsgi_app(registry, build_payload)
    httpd = make_server(addr, port, app, server_class=_ThreadingWSGIServer, handler_class=_SilentHandler)
    thread = threading.Thread(target=httpd.serve_forever, name="solark-http", daemon=True)
    thread.start()
    return httpd
