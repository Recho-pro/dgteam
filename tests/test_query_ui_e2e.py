from __future__ import annotations

import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Iterator

import pytest

from dgteam.query_api.contracts import API_CONTRACT_VERSION, DETAIL_CONTRACT_VERSION
from dgteam.query_api.server import build_handler

playwright = pytest.importorskip("playwright.sync_api")


def _iphone_selection() -> dict[str, object]:
    return {
        "data_source": "quote_rows",
        "brand_title": "苹果",
        "series_title": "iPhone 17",
        "model_title": "iPhone 17 Pro Max",
        "family_title": "iPhone 17 Pro Max",
        "condition_bucket": "apple_company_pure_sealed_target",
        "label": "iPhone 17 Pro Max",
        "meta": "苹果 / iPhone 17",
        "score": 999,
        "detail_key": "detail-iphone-17pm",
        "detail_contract": DETAIL_CONTRACT_VERSION,
        "query_ref": {
            "data_source": "quote_rows",
            "brand_title": "苹果",
            "series_title": "iPhone 17",
            "model_title": "iPhone 17 Pro Max",
            "family_title": "iPhone 17 Pro Max",
            "condition_bucket": "apple_company_pure_sealed_target",
            "detail_key": "detail-iphone-17pm",
        },
        "explain": {"run_key": "fixture_run"},
    }


def _mate_selection() -> dict[str, object]:
    return {
        "data_source": "quote_rows",
        "brand_title": "华为",
        "series_title": "Mate 80",
        "model_title": "Mate 80",
        "family_title": "Mate 80",
        "condition_bucket": "non_apple_allowed",
        "label": "Mate 80",
        "meta": "华为 / Mate 80",
        "score": 880,
        "detail_key": "detail-mate-80",
        "detail_contract": DETAIL_CONTRACT_VERSION,
        "query_ref": {
            "data_source": "quote_rows",
            "brand_title": "华为",
            "series_title": "Mate 80",
            "model_title": "Mate 80",
            "family_title": "Mate 80",
            "condition_bucket": "non_apple_allowed",
            "detail_key": "detail-mate-80",
        },
        "explain": {"run_key": "fixture_run"},
    }


def _snapshot_payload(selection: dict[str, object], *, refinement_query: str = "") -> dict[str, object]:
    branches = [
        {
            "branch_title": "iPhone 17 Pro Max",
            "capacity_groups": [
                {
                    "capacity_label": "256G",
                    "price_range": "7000-7200",
                    "latest_imported_at": "2026-04-17 20:00:00",
                    "colors": [
                        {"color_label": "黑色", "price_range": "7000-7100"},
                        {"color_label": "白色", "price_range": "7100-7200"},
                    ],
                },
                {
                    "capacity_label": "512G",
                    "price_range": "8050-8200",
                    "latest_imported_at": "2026-04-17 20:00:00",
                    "colors": [
                        {"color_label": "黑色", "price_range": "8050-8120"},
                        {"color_label": "白色", "price_range": "8100-8200"},
                    ],
                },
            ],
        }
    ]
    refinement_query = str(refinement_query or "").strip()
    refinement = {
        "requested_query": refinement_query,
        "applied": False,
        "reason": "empty_query" if not refinement_query else "not_applicable",
        "summary": "",
        "matched_branch_count": 0,
        "matched_capacity_group_count": 0,
        "matched_color_count": 0,
    }
    refinement_summary = ""

    if selection["detail_key"] == "detail-iphone-17pm" and refinement_query == "512 白色":
        branches = [
            {
                "branch_title": "iPhone 17 Pro Max",
                "capacity_groups": [
                    {
                        "capacity_label": "512G",
                        "price_range": "8100-8200",
                        "latest_imported_at": "2026-04-17 20:00:00",
                        "__matched": True,
                        "colors": [
                            {
                                "color_label": "白色",
                                "price_range": "8100-8200",
                                "__matched": True,
                            }
                        ],
                    }
                ],
            }
        ]
        refinement = {
            "requested_query": refinement_query,
            "applied": True,
            "reason": "applied",
            "summary": "512 · 白色",
            "matched_branch_count": 1,
            "matched_capacity_group_count": 1,
            "matched_color_count": 1,
        }
        refinement_summary = "512 · 白色"

    if selection["detail_key"] == "detail-mate-80":
        branches = [
            {
                "branch_title": "Mate 80",
                "capacity_groups": [
                    {
                        "capacity_label": "12+256G",
                        "price_range": "5600-5900",
                        "latest_imported_at": "2026-04-17 20:00:00",
                        "colors": [
                            {"color_label": "黑色", "price_range": "5600-5750"},
                            {"color_label": "白色", "price_range": "5750-5900"},
                        ],
                    }
                ],
            }
        ]

    return {
        "ok": True,
        "contract_version": API_CONTRACT_VERSION,
        "run_key": "fixture_run",
        "query": {
            **selection["query_ref"],
            "detail_key": selection["detail_key"],
            **({"refinement_query": refinement_query} if refinement_query else {}),
        },
        "header": {
            "title": str(selection["family_title"]),
            "latest_imported_at": "2026-04-17 20:00:00",
            "selected_gprice_labels": ["04-17"],
        },
        "hero": {
            "market_price": 8100 if refinement_query == "512 白色" else 7600,
            "sample_count": 24,
            "independent_source_count": 8,
        },
        "market_v1": {
            "ok": True,
            "price_range": "7000-8200",
            "trusted_offer": {
                "label": "市场均价",
                "raw_status": "",
                "sample_count": 24,
                "seller_count": 8,
            },
            "reference_market": {"price": 0, "source_name": "", "fetched_at": "", "sheet_name": ""},
            "flags": {},
        },
        "resolution": {
            "contract_version": DETAIL_CONTRACT_VERSION,
            "used_detail_key": True,
            "requested_run_key": "fixture_run",
            "effective_run_key": "fixture_run",
            "resolved_family_title": str(selection["family_title"]),
            "resolved_condition_bucket": str(selection["condition_bucket"]),
            "branch_resolution_source": "detail_key",
            "fallback_to_live_run": False,
            "resolved_branch_models": [str(branch["branch_title"]) for branch in branches],
            "refinement": refinement,
        },
        "branches": branches,
        "default_capacity": dict(branches[0]["capacity_groups"][0]),
        **({"refinementSummary": refinement_summary} if refinement_summary else {}),
    }


class UIFixtureQueryApp:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    def status_payload(self) -> dict[str, object]:
        return {
            "ok": True,
            "contract_version": API_CONTRACT_VERSION,
            "live": {
                "run_key": "fixture_run",
                "quote_count": 24,
                "market_snapshot_count": 2,
                "latest_imported_at": "2026-04-17 20:00:00",
                "published_at": "2026-04-17 20:00:00",
            },
            "summary": {"run_key": "fixture_run", "quote_count": 24},
            "reference_import": {},
            "endpoint_contracts": {},
            "backend_owned_logic": {"refinement": ["backend snapshot narrowing"]},
            "hot_queries": [
                {"label": "iPhone 17 Pro Max", "model_title": "iPhone 17 Pro Max", "series_title": "iPhone 17"},
                {"label": "Mate 80", "model_title": "Mate 80", "series_title": "Mate 80"},
            ],
        }

    def search_payload(self, query: str, *, limit: int = 6) -> dict[str, object]:
        lowered = str(query or "").strip().lower()
        results: list[dict[str, object]] = []
        if any(token in lowered for token in ("iphone 17", "17 pro max", "苹果17")):
            results.append(_iphone_selection())
        elif "mate 80" in lowered:
            results.append(_mate_selection())
        return {
            "ok": True,
            "contract_version": API_CONTRACT_VERSION,
            "query": query,
            "run_key": "fixture_run",
            "results": results[:limit],
        }

    def snapshot_payload(self, **kwargs: str) -> dict[str, object]:
        detail_key = str(kwargs.get("detail_key") or "")
        refinement_query = str(kwargs.get("refinement_query") or "")
        if detail_key == "detail-mate-80":
            return _snapshot_payload(_mate_selection(), refinement_query=refinement_query)
        return _snapshot_payload(_iphone_selection(), refinement_query=refinement_query)


@contextmanager
def running_query_ui_server(tmp_path: Path) -> Iterator[str]:
    app = UIFixtureQueryApp(tmp_path / "fixture.db")
    handler = build_handler(app)  # type: ignore[arg-type]
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@contextmanager
def chromium_page(*, viewport: dict[str, int] | None = None) -> Iterator[playwright.Page]:
    with playwright.sync_playwright() as runner:
        try:
            browser = runner.chromium.launch(headless=True)
        except Exception as exc:  # pragma: no cover - environment specific skip path
            pytest.skip(f"Playwright chromium is unavailable: {exc}")
        context = browser.new_context(viewport=viewport)
        page = context.new_page()
        try:
            yield page
        finally:
            context.close()
            browser.close()


@pytest.mark.e2e
def test_query_ui_search_flow_and_result_display(tmp_path: Path) -> None:
    with running_query_ui_server(tmp_path) as base_url, chromium_page() as page:
        page.goto(base_url, wait_until="networkidle")
        page.fill("#searchInput", "iPhone 17")
        page.wait_for_selector("[data-suggest-index='0']")
        assert "iPhone 17 Pro Max" in (page.text_content("[data-suggest-index='0']") or "")

        page.keyboard.press("Enter")
        page.wait_for_selector("#resultOverview:not([hidden])")

        assert (page.text_content("#resultTitle") or "").strip() == "iPhone 17 Pro Max"
        assert page.locator(".capacity-card").count() == 2
        assert "256G" in (page.text_content(".capacity-card:nth-of-type(1) .capacity-title") or "")


@pytest.mark.e2e
def test_query_ui_consumes_backend_owned_refinement(tmp_path: Path) -> None:
    with running_query_ui_server(tmp_path) as base_url, chromium_page() as page:
        page.goto(f"{base_url}/?q=iPhone%2017", wait_until="networkidle")
        page.wait_for_selector("#resultOverview:not([hidden])")

        page.fill("#searchInput", "512 白色")
        page.wait_for_function(
            "() => document.querySelector('#resultBadge')?.textContent?.includes('已筛 512 · 白色')",
        )

        assert page.locator(".capacity-card").count() == 1
        assert page.locator(".variant-row").count() == 1
        assert "白色" in (page.text_content(".variant-row .variant-title") or "")


@pytest.mark.e2e
def test_query_ui_mobile_breakpoint_has_no_horizontal_overflow(tmp_path: Path) -> None:
    with running_query_ui_server(tmp_path) as base_url, chromium_page(
        viewport={"width": 390, "height": 844},
    ) as page:
        page.goto(f"{base_url}/?q=iPhone%2017", wait_until="networkidle")
        page.wait_for_selector("#resultOverview:not([hidden])")

        assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1") is True
        assert page.locator("#queryBtn").bounding_box() is not None
        assert (page.text_content("#resultTitle") or "").strip() == "iPhone 17 Pro Max"
