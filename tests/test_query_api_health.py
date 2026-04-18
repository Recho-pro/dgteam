from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from urllib.request import urlopen

from dgteam.query_api.server import QueryApp, aggregate_model_candidates, build_handler, build_hot_query_candidates


def _fixture_candidate(*, model_title: str, group_title: str, row_count: int, source_count: int) -> dict[str, object]:
    return {
        "data_source": "quote_rows",
        "run_key": "fixture_run",
        "brand_title": "苹果",
        "series_title": "iPhone 17",
        "model_title": model_title,
        "group_title": group_title,
        "condition_bucket": "apple_company_pure_sealed_target",
        "row_count": row_count,
        "source_count": source_count,
        "min_price": 5000,
        "max_price": 12000,
        "latest_imported_at": "2026-04-16 12:00:00",
        "latest_gprice": "04-16",
        "variant_count": 1,
        "branch_count": 1,
    }


def _build_fixture_app(tmp_path) -> QueryApp:
    app = QueryApp(tmp_path / "fixture.db")
    raw_candidates = [
        _fixture_candidate(
            model_title="17 Pro Max 6.9寸 国行",
            group_title="星宇橙色256G",
            row_count=68,
            source_count=16,
        ),
        _fixture_candidate(
            model_title="17 Pro Max 6.9寸 国行",
            group_title="白色512G",
            row_count=54,
            source_count=15,
        ),
    ]
    aggregated = aggregate_model_candidates(raw_candidates)
    hot = build_hot_query_candidates(aggregated, limit=6)
    meta = {
        "run_key": "fixture_run",
        "latest_imported_at": "2026-04-16 12:00:00",
        "quote_count": sum(int(item["row_count"]) for item in raw_candidates),
    }
    app.cache.get_state = lambda: (meta, aggregated, hot, raw_candidates)  # type: ignore[method-assign]
    return app


def test_query_api_health_endpoint_exposes_basic_runtime_state(tmp_path):
    app = _build_fixture_app(tmp_path)
    handler = build_handler(app)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/health"
        with urlopen(url, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert payload["ok"] is True
    assert payload["service"] == "dgteam-query-api"
    assert payload["status"]["run_key"] == "fixture_run"
    assert payload["status"]["quote_count"] == 122
