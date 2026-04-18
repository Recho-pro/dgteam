from __future__ import annotations

from pathlib import Path

from dgteam.query_api.server import (
    QueryApp,
    aggregate_model_candidates,
    build_hot_query_candidates,
    prepare_query_context,
)


def make_raw_candidate(
    *,
    brand_title: str,
    series_title: str,
    model_title: str,
    group_title: str,
    row_count: int,
    source_count: int,
    latest_imported_at: str = "2026-04-16 12:00:00",
    condition_bucket: str = "non_apple_allowed",
) -> dict[str, object]:
    return {
        "data_source": "quote_rows",
        "run_key": "fixture_run",
        "brand_title": brand_title,
        "series_title": series_title,
        "model_title": model_title,
        "group_title": group_title,
        "condition_bucket": condition_bucket,
        "row_count": row_count,
        "source_count": source_count,
        "min_price": 5000,
        "max_price": 12000,
        "latest_imported_at": latest_imported_at,
        "latest_gprice": "04-16",
        "variant_count": 1,
        "branch_count": 1,
    }


def build_fixture_candidates() -> list[dict[str, object]]:
    return [
        make_raw_candidate(
            brand_title="苹果",
            series_title="iPhone 17",
            model_title="17 6.3寸 国行",
            group_title="黑色256G",
            row_count=20,
            source_count=8,
            condition_bucket="apple_company_pure_sealed_target",
        ),
        make_raw_candidate(
            brand_title="苹果",
            series_title="iPhone 17",
            model_title="17 6.3寸 国行",
            group_title="白色512G",
            row_count=12,
            source_count=5,
            condition_bucket="apple_company_pure_sealed_target",
        ),
        make_raw_candidate(
            brand_title="苹果",
            series_title="iPhone 17",
            model_title="17 Pro 6.3寸 国行",
            group_title="白色256G",
            row_count=42,
            source_count=12,
            condition_bucket="apple_company_pure_sealed_target",
        ),
        make_raw_candidate(
            brand_title="苹果",
            series_title="iPhone 17",
            model_title="17 Pro Max 6.9寸 国行",
            group_title="深蓝色256G",
            row_count=68,
            source_count=16,
            condition_bucket="apple_company_pure_sealed_target",
        ),
        make_raw_candidate(
            brand_title="苹果",
            series_title="iPhone 17",
            model_title="17 Pro Max 6.9寸 国行",
            group_title="白色512G",
            row_count=54,
            source_count=15,
            condition_bucket="apple_company_pure_sealed_target",
        ),
        make_raw_candidate(
            brand_title="苹果",
            series_title="Apple 配件",
            model_title="Apple充电头/数据线",
            group_title="Apple 20W 充电头",
            row_count=18,
            source_count=8,
            condition_bucket="apple_company_pure_sealed_target",
        ),
        make_raw_candidate(
            brand_title="苹果",
            series_title="Apple 配件",
            model_title="Apple Pencil (手写笔)",
            group_title="白色",
            row_count=11,
            source_count=5,
            condition_bucket="apple_company_pure_sealed_target",
        ),
        make_raw_candidate(
            brand_title="红米",
            series_title="Redmi K系列",
            model_title="Redmi K80",
            group_title="黑色12+256G",
            row_count=35,
            source_count=13,
        ),
        make_raw_candidate(
            brand_title="红米",
            series_title="Redmi K系列",
            model_title="Redmi K80 Pro",
            group_title="白色12+256G",
            row_count=28,
            source_count=11,
        ),
        make_raw_candidate(
            brand_title="华为",
            series_title="Mate 80",
            model_title="Mate80RS 非凡大师",
            group_title="黑色16+512G",
            row_count=26,
            source_count=9,
        ),
        make_raw_candidate(
            brand_title="联想电脑",
            series_title="联想小新系列",
            model_title="小新14SE(14C) 2025款",
            group_title="R7 16G 512G",
            row_count=17,
            source_count=6,
        ),
    ]


def build_fixture_app(tmp_path: Path) -> QueryApp:
    app = QueryApp(tmp_path / "fixture.db")
    raw_candidates = build_fixture_candidates()
    aggregated = aggregate_model_candidates(raw_candidates)
    hot = build_hot_query_candidates(aggregated, limit=6)
    meta = {
        "run_key": "fixture_run",
        "latest_imported_at": "2026-04-16 12:00:00",
        "quote_count": sum(int(item["row_count"]) for item in raw_candidates),
    }
    app.cache.get_state = lambda: (meta, aggregated, hot, raw_candidates)  # type: ignore[method-assign]
    return app


def top_label(app: QueryApp, query: str) -> str:
    payload = app.search_payload(query, limit=6)
    results = payload.get("results") or []
    return str(results[0].get("label") or "") if results else ""


def test_search_accepts_spaced_pinyin_for_apple(tmp_path: Path):
    app = build_fixture_app(tmp_path)
    assert top_label(app, "ping guo 17 pm") == "iPhone 17 Pro Max"


def test_search_accepts_spaced_pinyin_for_redmi(tmp_path: Path):
    app = build_fixture_app(tmp_path)
    assert top_label(app, "hong mi k80") == "Redmi K80"


def test_search_prefers_exact_family_when_capacity_noise_is_present(tmp_path: Path):
    app = build_fixture_app(tmp_path)
    assert top_label(app, "苹果17 256") == "iPhone 17"


def test_search_routes_apple_wattage_query_to_accessory_family(tmp_path: Path):
    app = build_fixture_app(tmp_path)
    assert top_label(app, "苹果20w") == "Apple充电头/数据线"


def test_search_supports_compact_model_shorthand(tmp_path: Path):
    app = build_fixture_app(tmp_path)
    assert top_label(app, "mate80rs") == "Mate80RS 非凡大师"


def test_search_supports_spaced_lenovo_series_pinyin(tmp_path: Path):
    app = build_fixture_app(tmp_path)
    assert top_label(app, "xiao xin 14 se") == "小新14SE(14C) 2025款"


def test_prepare_query_context_marks_capacity_color_only_query_as_refinement():
    context = prepare_query_context("白色 512")
    assert context.only_refinement is True
    assert "512" in context.capacity_hints
    assert "白色" in context.color_hints


def test_search_returns_empty_for_refinement_only_query_without_family_context(tmp_path: Path):
    app = build_fixture_app(tmp_path)
    payload = app.search_payload("白色 512", limit=6)
    assert payload["results"] == []
