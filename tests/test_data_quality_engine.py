from __future__ import annotations

from dgteam.market.market_engine import build_market_v1
from dgteam.market.quality_engine import build_data_quality_market
from dgteam.market.rules import load_rules


def make_row(
    *,
    merchant: str,
    imported_at: str,
    gprice: str,
    price_text: str,
    row_id: int,
    dstatus: str,
    brand_title: str,
    series_title: str,
    model_title: str,
    group_title: str,
) -> dict[str, object]:
    return {
        "id": row_id,
        "brand_title": brand_title,
        "series_title": series_title,
        "model_title": model_title,
        "group_title": group_title,
        "condition_bucket": "",
        "sname": merchant,
        "sno": "",
        "gid": str(5000000000000 + row_id),
        "gprice": gprice,
        "gprice_two": "",
        "price_text": price_text,
        "dstatus": dstatus,
        "activation": "",
        "imported_at": imported_at,
    }


def test_quality_engine_prefers_latest_valid_day_only():
    rows = [
        make_row(
            merchant="A",
            imported_at="2026-04-16 12:00:00",
            gprice="04-16",
            price_text="5520",
            row_id=1,
            dstatus="公司纯原封",
            brand_title="苹果",
            series_title="iPhone 17",
            model_title="17 Pro Max 6.9寸 国行",
            group_title="黑色256G",
        ),
        make_row(
            merchant="B",
            imported_at="2026-04-16 11:50:00",
            gprice="04-16",
            price_text="5530",
            row_id=2,
            dstatus="公司纯原封",
            brand_title="苹果",
            series_title="iPhone 17",
            model_title="17 Pro Max 6.9寸 国行",
            group_title="黑色256G",
        ),
        make_row(
            merchant="C",
            imported_at="2026-04-15 18:00:00",
            gprice="04-15",
            price_text="5480",
            row_id=3,
            dstatus="公司纯原封",
            brand_title="苹果",
            series_title="iPhone 17",
            model_title="17 Pro Max 6.9寸 国行",
            group_title="黑色256G",
        ),
        make_row(
            merchant="D",
            imported_at="2026-04-15 17:30:00",
            gprice="04-15",
            price_text="5490",
            row_id=4,
            dstatus="公司纯原封",
            brand_title="苹果",
            series_title="iPhone 17",
            model_title="17 Pro Max 6.9寸 国行",
            group_title="黑色256G",
        ),
    ]

    result = build_market_v1(
        rows=rows,
        brand_title="苹果",
        series_title="iPhone 17",
        model_title="17 Pro Max 6.9寸 国行",
        group_title="黑色256G",
        rules=load_rules(),
    )

    assert result["ok"] is True
    assert result["selection"]["mode"] == "latest_valid_day_only"
    assert result["selection"]["selected_labels"] == ["04-16"]
    assert result["selection"]["selected_row_count"] == 2
    assert result["best_cluster"]["price_range"] == "5520-5530"


def test_quality_engine_falls_back_when_latest_day_has_only_filtered_rows():
    rows = [
        make_row(
            merchant="A",
            imported_at="2026-04-16 12:00:00",
            gprice="04-16",
            price_text="5520",
            row_id=10,
            dstatus="公司纯原封/特定区域销售",
            brand_title="苹果",
            series_title="iPhone 17",
            model_title="17 Pro 6.3寸 国行",
            group_title="银色256G",
        ),
        make_row(
            merchant="B",
            imported_at="2026-04-16 11:00:00",
            gprice="04-16",
            price_text="5530",
            row_id=11,
            dstatus="公司纯原封/激活",
            brand_title="苹果",
            series_title="iPhone 17",
            model_title="17 Pro 6.3寸 国行",
            group_title="银色256G",
        ),
        make_row(
            merchant="C",
            imported_at="2026-04-15 18:00:00",
            gprice="04-15",
            price_text="5560",
            row_id=12,
            dstatus="公司纯原封",
            brand_title="苹果",
            series_title="iPhone 17",
            model_title="17 Pro 6.3寸 国行",
            group_title="银色256G",
        ),
    ]

    result = build_data_quality_market(
        rows=rows,
        brand_title="苹果",
        rules=load_rules(),
    )

    assert result["ok"] is True
    assert result["selection"]["fallback_applied"] is True
    assert result["quality_engine"]["selected_day_gprice_label"] == "04-15"
    assert result["quality_engine"]["filter_reason_counts"]["status_noise_keyword"] >= 2


def test_quality_engine_does_not_wash_away_sparse_category():
    rows = [
        make_row(
            merchant="Solo",
            imported_at="2026-04-16 09:30:00",
            gprice="04-16",
            price_text="7399",
            row_id=20,
            dstatus="全国纯原",
            brand_title="苹果",
            series_title="Apple Watch",
            model_title="Ultra 3",
            group_title="钛色49mm",
        ),
    ]

    result = build_data_quality_market(
        rows=rows,
        brand_title="苹果",
        rules=load_rules(),
    )

    assert result["ok"] is True
    assert result["best_cluster"]["sample_count"] == 1
    assert result["best_cluster"]["price_range"] == "7399-7399"
    assert result["quality_engine"]["low_quality_fallback"] is True


def test_quality_engine_removes_outlier_without_dropping_category():
    rows = [
        make_row(
            merchant=f"M{index}",
            imported_at=f"2026-04-16 10:{index:02d}:00",
            gprice="04-16",
            price_text=str(price),
            row_id=100 + index,
            dstatus="全国纯原",
            brand_title="VIVO",
            series_title="iQOO 15",
            model_title="iQOO 15",
            group_title="16+512G传奇版",
        )
        for index, price in enumerate([4500, 4510, 4520, 4530, 4540, 4180], start=1)
    ]

    result = build_data_quality_market(
        rows=rows,
        brand_title="VIVO",
        rules=load_rules(),
    )

    assert result["ok"] is True
    assert result["best_cluster"]["band_low"] >= 4500
    assert result["quality_engine"]["candidate_count"] == 2
    assert result["flags"]["suspicious_low_cluster_count"] >= 1
