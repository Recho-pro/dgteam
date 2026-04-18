from __future__ import annotations

import json
import logging
import sys
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict
from urllib.parse import parse_qs, urlparse

from dgteam.query_api.error_contracts import api_error_payload, unknown_api_payload, unsupported_method_payload
from dgteam.query_api.http_endpoints import (
    health_payload,
    search_payload as endpoint_search_payload,
    sku_payload as endpoint_sku_payload,
)
from dgteam.query_api.static_assets import cache_headers_for_static_path, utf8_content_type
from dgteam.query_api.ui_assets import render_index_html, resolve_release_ui_asset_dir


LOGGER = logging.getLogger("dgteam.query_api.server")
CLIENT_DISCONNECT_ERRORS = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)
QUIET_404_PREFIXES = (
    "/wp-admin/",
    "/wordpress/",
    "/SDK/",
    "/.well-known/",
    "/favicon.ico",
    "/security.txt",
    "/info.php",
    "/phpinfo.php",
    "/boaform/",
    "/cgi-bin/",
)
QUIET_METHODS = ("PROPFIND", "OPTIONS")


def json_response(
    handler: SimpleHTTPRequestHandler,
    payload: Dict[str, Any],
    *,
    status: int = 200,
    headers: Dict[str, str] | None = None,
) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    try:
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            handler.send_header(str(key), str(value))
        handler.end_headers()
        handler.wfile.write(body)
    except CLIENT_DISCONNECT_ERRORS as exc:
        LOGGER.debug(
            "query api client disconnected during response path=%s status=%s error=%s",
            getattr(handler, "path", ""),
            status,
            exc.__class__.__name__,
        )


def html_response(
    handler: SimpleHTTPRequestHandler,
    body_text: str,
    *,
    status: int = 200,
    headers: Dict[str, str] | None = None,
) -> None:
    body = str(body_text or "").encode("utf-8")
    try:
        handler.send_response(status)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            handler.send_header(str(key), str(value))
        handler.end_headers()
        handler.wfile.write(body)
    except CLIENT_DISCONNECT_ERRORS as exc:
        LOGGER.debug(
            "query api client disconnected during html response path=%s status=%s error=%s",
            getattr(handler, "path", ""),
            status,
            exc.__class__.__name__,
        )


def is_client_disconnect_error(exc: BaseException | None) -> bool:
    current = exc
    while current is not None:
        if isinstance(current, CLIENT_DISCONNECT_ERRORS):
            return True
        current = current.__cause__ or current.__context__
    return False


def is_quiet_404_path(path: str) -> bool:
    normalized = str(path or "").strip()
    return any(normalized.startswith(prefix) for prefix in QUIET_404_PREFIXES)


def build_handler(app: Any, *, api_error_cls: type[BaseException] | None = None):
    def _ui_asset_dir() -> Path:
        resolver = getattr(app, "resolve_ui_asset_dir", None)
        if callable(resolver):
            return Path(resolver()).expanduser().resolve()
        configured_asset_dir = getattr(app, "ui_asset_dir", None)
        if configured_asset_dir:
            return Path(configured_asset_dir).expanduser().resolve()
        return resolve_release_ui_asset_dir(getattr(app, "db_path", None))

    class QueryHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(_ui_asset_dir()), **kwargs)

        @staticmethod
        def _utf8_content_type(content_type: str) -> str:
            return utf8_content_type(content_type)

        def guess_type(self, path: str) -> str:
            return self._utf8_content_type(super().guess_type(path))

        def end_headers(self) -> None:
            request_path = urlparse(getattr(self, "path", "")).path or "/"
            for name, value in cache_headers_for_static_path(request_path).items():
                self.send_header(name, value)
            super().end_headers()

        def do_GET(self):
            parsed = urlparse(self.path)
            request_id = uuid.uuid4().hex[:12]
            response_headers = {"X-Request-Id": request_id}
            try:
                if parsed.path == "/health":
                    return json_response(self, health_payload(app), headers=response_headers)
                if parsed.path == "/api/status":
                    return json_response(self, app.status_payload(), headers=response_headers)
                if parsed.path == "/api/search":
                    params = parse_qs(parsed.query)
                    return json_response(self, endpoint_search_payload(app, params), headers=response_headers)
                if parsed.path == "/api/sku":
                    params = parse_qs(parsed.query)
                    payload, status = endpoint_sku_payload(app, params)
                    return json_response(self, payload, status=status, headers=response_headers)
                if parsed.path.startswith("/api/"):
                    return json_response(
                        self,
                        unknown_api_payload(parsed.path, request_id=request_id),
                        status=404,
                        headers=response_headers,
                    )
                if parsed.path in {"", "/", "/index.html"}:
                    return html_response(self, render_index_html(asset_dir=_ui_asset_dir()), headers=response_headers)
                return super().do_GET()
            except CLIENT_DISCONNECT_ERRORS as exc:
                LOGGER.debug(
                    "query api client disconnected request_id=%s path=%s error=%s",
                    request_id,
                    parsed.path,
                    exc.__class__.__name__,
                )
                return
            except Exception as exc:
                if api_error_cls is not None and isinstance(exc, api_error_cls):
                    return json_response(
                        self,
                        api_error_payload(
                            str(getattr(exc, "message", "Request failed")),
                            code=str(getattr(exc, "code", "api_error")),
                            request_id=request_id,
                            details=dict(getattr(exc, "details", {}) or {}),
                        ),
                        status=int(getattr(exc, "status", 500)),
                        headers=response_headers,
                    )
                LOGGER.exception("query api request failed request_id=%s error=%r", request_id, exc)
                return json_response(
                    self,
                    api_error_payload(
                        "The query service encountered an internal error.",
                        code="internal_error",
                        request_id=request_id,
                    ),
                    status=500,
                    headers=response_headers,
                )

        def do_POST(self):
            parsed = urlparse(self.path)
            request_id = uuid.uuid4().hex[:12]
            response_headers = {"X-Request-Id": request_id}
            status = 405 if parsed.path.startswith("/api/") else 404
            return json_response(
                self,
                unsupported_method_payload("POST", parsed.path, request_id=request_id),
                status=status,
                headers=response_headers,
            )

        def log_message(self, format: str, *args):
            message = format % args
            path = urlparse(getattr(self, "path", "")).path or ""
            method = str(getattr(self, "command", "") or "").upper()
            if method in QUIET_METHODS:
                LOGGER.debug("query-ui %s - %s", self.address_string(), message)
                return
            if is_quiet_404_path(path) and ("code 404" in message or '" 404 ' in message):
                LOGGER.debug("query-ui %s - %s", self.address_string(), message)
                return
            LOGGER.info("query-ui %s - %s", self.address_string(), message)

    return QueryHandler


class QuietThreadingHTTPServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address) -> None:  # type: ignore[override]
        if is_client_disconnect_error(sys.exc_info()[1]):
            LOGGER.debug("query api client disconnected before response completed: %s", client_address)
            return
        super().handle_error(request, client_address)
