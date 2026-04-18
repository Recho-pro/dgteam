from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from pathlib import Path
from urllib.request import Request, urlopen

from dgteam.query_api.contracts import API_CONTRACT_VERSION, DETAIL_CONTRACT_VERSION
from dgteam.query_api.error_contracts import UNKNOWN_API_ERROR_CODE, UNSUPPORTED_METHOD_ERROR_CODE
from dgteam.query_api.server import QueryApp, build_handler
from dgteam.query_api.service import QueryService
from dgteam.query_api.static_assets import cache_headers_for_static_path, utf8_content_type
from dgteam.query_api.ui_assets import build_ui_asset_version, package_query_ui_assets, render_index_html


class FakeQueryApp:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.search_calls: list[tuple[str, int]] = []
        self.sku_calls: list[dict[str, str]] = []

    def status_payload(self) -> dict[str, object]:
        return {
            "ok": True,
            "contract_version": API_CONTRACT_VERSION,
            "live": {
                "run_key": "fixture_run",
                "quote_count": 12,
                "market_snapshot_count": 4,
                "published_at": "2026-04-17 12:00:00",
            },
            "summary": {"run_key": "fixture_run", "quote_count": 12},
            "reference_import": {},
            "hot_queries": [],
        }

    def search_payload(self, query: str, *, limit: int = 6) -> dict[str, object]:
        self.search_calls.append((query, limit))
        return {
            "ok": True,
            "contract_version": API_CONTRACT_VERSION,
            "query": query,
            "run_key": "fixture_run",
            "results": [
                {
                    "data_source": "quote_rows",
                    "label": "Fixture Model",
                    "detail_key": "detail-fixture",
                    "detail_contract": DETAIL_CONTRACT_VERSION,
                    "query_ref": {"detail_key": "detail-fixture"},
                    "explain": {"run_key": "fixture_run"},
                }
            ],
        }

    def snapshot_payload(self, **kwargs: str) -> dict[str, object]:
        self.sku_calls.append({key: str(value) for key, value in kwargs.items()})
        return {
            "ok": True,
            "contract_version": API_CONTRACT_VERSION,
            "query": kwargs,
            "header": {"title": "Fixture Model"},
            "hero": {"market_price": 100},
            "market_v1": {"ok": True},
            "resolution": {
                "contract_version": DETAIL_CONTRACT_VERSION,
                "refinement": {
                    "requested_query": str(kwargs.get("refinement_query") or ""),
                    "applied": bool(kwargs.get("refinement_query")),
                    "reason": "applied" if kwargs.get("refinement_query") else "empty_query",
                    "summary": str(kwargs.get("refinement_query") or ""),
                    "matched_branch_count": 1 if kwargs.get("refinement_query") else 0,
                    "matched_capacity_group_count": 1 if kwargs.get("refinement_query") else 0,
                    "matched_color_count": 1 if kwargs.get("refinement_query") else 0,
                },
            },
            "branches": [],
        }


def _get_json(server: ThreadingHTTPServer, path: str) -> dict[str, object]:
    with urlopen(f"http://127.0.0.1:{server.server_port}{path}", timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_text(server: ThreadingHTTPServer, path: str) -> str:
    with urlopen(f"http://127.0.0.1:{server.server_port}{path}", timeout=5) as response:
        return response.read().decode("utf-8")


def _get_text_and_headers(server: ThreadingHTTPServer, path: str) -> tuple[str, dict[str, str]]:
    with urlopen(f"http://127.0.0.1:{server.server_port}{path}", timeout=5) as response:
        return response.read().decode("utf-8"), {key.lower(): value for key, value in response.headers.items()}


def _get_http_error_json(server: ThreadingHTTPServer, path: str) -> tuple[int, dict[str, object], dict[str, str]]:
    try:
        with urlopen(f"http://127.0.0.1:{server.server_port}{path}", timeout=5):
            raise AssertionError(f"Expected HTTP error for {path}")
    except HTTPError as exc:
        payload = json.loads(exc.read().decode("utf-8"))
        headers = {key.lower(): value for key, value in exc.headers.items()}
        return int(exc.code), payload, headers


def _post_http_error_json(server: ThreadingHTTPServer, path: str) -> tuple[int, dict[str, object], dict[str, str]]:
    request = Request(f"http://127.0.0.1:{server.server_port}{path}", method="POST")
    try:
        with urlopen(request, timeout=5):
            raise AssertionError(f"Expected HTTP error for POST {path}")
    except HTTPError as exc:
        payload = json.loads(exc.read().decode("utf-8"))
        headers = {key.lower(): value for key, value in exc.headers.items()}
        return int(exc.code), payload, headers


def test_status_payload_publishes_query_contracts_and_backend_ownership(tmp_path: Path) -> None:
    app = QueryApp(tmp_path / "fixture.db")
    meta = {
        "run_key": "fixture_run",
        "quote_count": 0,
        "market_snapshot_count": 0,
        "latest_imported_at": "2026-04-17 12:00:00",
    }
    app.cache.get_state = lambda: (meta, [], [], [])  # type: ignore[method-assign]

    payload = app.status_payload()

    assert payload["contract_version"] == API_CONTRACT_VERSION
    endpoint_contracts = payload["endpoint_contracts"]
    assert set(endpoint_contracts) == {"/health", "/api/status", "/api/search", "/api/sku", "/api/*"}
    assert endpoint_contracts["/api/search"]["result_required_fields"][:3] == [
        "data_source",
        "brand_title",
        "series_title",
    ]
    assert endpoint_contracts["/api/*"]["error_codes"] == [UNKNOWN_API_ERROR_CODE]
    assert "search_normalization" in payload["backend_owned_logic"]
    assert "snapshot_assembly" in payload["backend_owned_logic"]


def test_query_http_routes_freeze_contract_and_compatibility_params(tmp_path: Path) -> None:
    app = FakeQueryApp(tmp_path / "fake.db")
    handler = build_handler(app)  # type: ignore[arg-type]
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        health = _get_json(server, "/health")
        status = _get_json(server, "/api/status")
        search = _get_json(server, "/api/search?query=Fixture&limit=99")
        sku = _get_json(
            server,
            "/api/sku?detailKey=detail-fixture&source=quote_rows&brand=BrandA&family=Fixture%20Model&refinement=512%20white",
        )
        index_html = _get_text(server, "/")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert health["service"] == "dgteam-query-api"
    assert health["status"]["run_key"] == "fixture_run"
    assert status["contract_version"] == API_CONTRACT_VERSION
    assert search["contract_version"] == API_CONTRACT_VERSION
    assert search["results"][0]["detail_contract"] == DETAIL_CONTRACT_VERSION
    assert sku["contract_version"] == API_CONTRACT_VERSION
    assert app.search_calls == [("Fixture", 6)]
    assert app.sku_calls[0]["detail_key"] == "detail-fixture"
    assert app.sku_calls[0]["data_source"] == "quote_rows"
    assert app.sku_calls[0]["family_title"] == "Fixture Model"
    assert app.sku_calls[0]["refinement_query"] == "512 white"
    assert "?v=" in index_html
    assert build_ui_asset_version() in index_html


def test_unknown_api_get_returns_json_contract_not_static_404(tmp_path: Path) -> None:
    app = FakeQueryApp(tmp_path / "fake.db")
    handler = build_handler(app)  # type: ignore[arg-type]
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, payload, headers = _get_http_error_json(server, "/api/not-a-real-route")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert status == 404
    assert payload["ok"] is False
    assert payload["contract_version"] == API_CONTRACT_VERSION
    assert payload["error_code"] == UNKNOWN_API_ERROR_CODE
    assert payload["details"]["path"] == "/api/not-a-real-route"  # type: ignore[index]
    assert headers["content-type"].startswith("application/json")
    assert headers["cache-control"] == "no-store"
    assert "x-request-id" in headers


def test_api_post_returns_json_method_contract(tmp_path: Path) -> None:
    app = FakeQueryApp(tmp_path / "fake.db")
    handler = build_handler(app)  # type: ignore[arg-type]
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, payload, headers = _post_http_error_json(server, "/api/search")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert status == 405
    assert payload["ok"] is False
    assert payload["contract_version"] == API_CONTRACT_VERSION
    assert payload["error_code"] == UNSUPPORTED_METHOD_ERROR_CODE
    assert payload["details"]["path"] == "/api/search"  # type: ignore[index]
    assert payload["details"]["method"] == "POST"  # type: ignore[index]
    assert headers["content-type"].startswith("application/json")
    assert headers["cache-control"] == "no-store"
    assert "x-request-id" in headers


def test_query_handler_serves_release_scoped_ui_assets_and_cache_headers(tmp_path: Path) -> None:
    release_dir = tmp_path / "current"
    source_ui = tmp_path / "source_ui"
    source_ui.mkdir(parents=True)
    (source_ui / "index.html").write_text(
        (
            '<html><head><meta name="dgteam-asset-version" content="__DGTEAM_ASSET_VERSION__">'
            '<link rel="stylesheet" href="__DGTEAM_STYLES_HREF__"></head>'
            '<body><main>release-scoped-ui</main><script src="__DGTEAM_APP_HREF__"></script></body></html>'
        ),
        encoding="utf-8",
    )
    (source_ui / "app.js").write_text("window.DGTEAM_RELEASE_UI = 'release-scoped-ui';\n", encoding="utf-8")
    (source_ui / "styles.css").write_text("main { color: #173b2f; }\n", encoding="utf-8")
    manifest = package_query_ui_assets(release_dir / "query_ui", source_dir=source_ui)

    app = FakeQueryApp(release_dir / "dgteam.db")
    handler = build_handler(app)  # type: ignore[arg-type]
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        index_html, index_headers = _get_text_and_headers(server, "/")
        app_js, app_headers = _get_text_and_headers(server, f"/app.js?v={manifest['version']}")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert "release-scoped-ui" in index_html
    assert f'content="{manifest["version"]}"' in index_html
    assert f"./app.js?v={manifest['version']}" in index_html
    assert "window.DGTEAM_RELEASE_UI" in app_js
    assert index_headers["cache-control"] == "no-store, max-age=0"
    assert app_headers["cache-control"] == "public, max-age=31536000, immutable"


def test_query_service_exposes_compatibility_snapshot_and_contracts(tmp_path: Path) -> None:
    service = QueryService(db_path=tmp_path / "service.db")

    contracts = service.endpoint_contracts()
    owned_logic = service.backend_owned_logic()
    snapshot = service.snapshot(
        data_source="quote_rows",
        brand_title="",
        series_title="",
        model_title="",
        family_title="",
        group_title="",
        condition_bucket="",
        detail_key="",
        external_key="",
        refinement_query="512",
    )

    assert "/api/sku" in contracts
    assert "refinement" in owned_logic
    assert snapshot["ok"] is False
    assert snapshot["error_code"] == "missing_model_identifier"


def test_render_index_html_injects_fingerprinted_asset_urls() -> None:
    version = build_ui_asset_version()
    html = render_index_html()

    assert "__DGTEAM_" not in html
    assert f'content="{version}"' in html
    assert f"./styles.css?v={version}" in html
    assert f"./app.js?v={version}" in html


def test_static_asset_contract_is_isolated_from_http_handler() -> None:
    assert utf8_content_type("text/html") == "text/html; charset=utf-8"
    assert cache_headers_for_static_path("/")["Cache-Control"] == "no-store, max-age=0"
    assert cache_headers_for_static_path("/app.js")["Cache-Control"] == "public, max-age=31536000, immutable"
    assert cache_headers_for_static_path("/api/status") == {}
