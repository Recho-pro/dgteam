from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from dgteam.core.textio import write_json_utf8
from dgteam.market.quality_engine import build_data_quality_market
from dgteam.market.rules import load_rules


def _make_row(
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
) -> Dict[str, Any]:
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


def _sample_cases() -> Dict[str, List[Dict[str, Any]]]:
    return {
        "normal_latest_day_only": [
            _make_row(
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
            _make_row(
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
            _make_row(
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
        ],
        "sparse_single_quote": [
            _make_row(
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
        ],
        "outlier_cluster_isolated": [
            _make_row(
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
        ],
        "latest_day_filtered_fallback": [
            _make_row(
                merchant="A",
                imported_at="2026-04-16 12:00:00",
                gprice="04-16",
                price_text="5520",
                row_id=30,
                dstatus="公司纯原封/特定区域销售",
                brand_title="苹果",
                series_title="iPhone 17",
                model_title="17 Pro 6.3寸 国行",
                group_title="银色256G",
            ),
            _make_row(
                merchant="B",
                imported_at="2026-04-16 11:00:00",
                gprice="04-16",
                price_text="5530",
                row_id=31,
                dstatus="公司纯原封/激活",
                brand_title="苹果",
                series_title="iPhone 17",
                model_title="17 Pro 6.3寸 国行",
                group_title="银色256G",
            ),
            _make_row(
                merchant="C",
                imported_at="2026-04-15 18:00:00",
                gprice="04-15",
                price_text="5560",
                row_id=32,
                dstatus="公司纯原封",
                brand_title="苹果",
                series_title="iPhone 17",
                model_title="17 Pro 6.3寸 国行",
                group_title="银色256G",
            ),
        ],
    }


def build_validation_report() -> Dict[str, Any]:
    rules = load_rules()
    report: Dict[str, Any] = {"generated_at": "2026-04-16", "cases": []}
    for case_name, rows in _sample_cases().items():
        brand_title = str(rows[0]["brand_title"]) if rows else ""
        result = build_data_quality_market(rows=rows, brand_title=brand_title, rules=rules)
        report["cases"].append(
            {
                "case": case_name,
                "ok": bool(result.get("ok")),
                "selection_mode": str((result.get("selection") or {}).get("mode") or ""),
                "selected_labels": list((result.get("selection") or {}).get("selected_labels") or []),
                "selected_row_count": int((result.get("selection") or {}).get("selected_row_count") or 0),
                "best_cluster": dict(result.get("best_cluster") or {}),
                "filter_reason_counts": dict((result.get("quality_engine") or {}).get("filter_reason_counts") or {}),
                "candidate_count": int((result.get("quality_engine") or {}).get("candidate_count") or 0),
                "low_quality_fallback": bool((result.get("quality_engine") or {}).get("low_quality_fallback")),
                "selection_summary": str((result.get("explanation") or {}).get("selection_summary") or ""),
                "quality_summary": str((result.get("explanation") or {}).get("quality_summary") or ""),
            }
        )
    return report


def write_validation_report(path: Path) -> Path:
    target = Path(path).expanduser().resolve()
    write_json_utf8(target, build_validation_report())
    return target
