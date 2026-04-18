from __future__ import annotations

from collections import defaultdict
import csv
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from dgteam.market.market_engine import build_market_v1
from dgteam.market.price_cleaning import merchant_key, parse_price_int
from dgteam.market.rules import load_rules
from dgteam.core.storage import DGTeamStorage, normalize_search_text
from dgteam.core.textio import write_json_utf8


SNAPSHOT_FIELDNAMES = [
    "run_key",
    "brand_title",
    "series_title",
    "model_title",
    "group_title",
    "condition_bucket",
    "selected_gprice_label",
    "selected_gprice_labels",
    "latest_gprice",
    "latest_imported_at",
    "source_row_count",
    "source_count",
    "min_price",
    "max_price",
    "market_price",
    "price_range",
    "trusted_status",
    "trusted_sample_count",
    "trusted_seller_count",
    "confidence_score",
    "confidence_label",
    "selection_mode",
    "selection_fallback_applied",
    "selection_sample_count",
    "selection_summary",
    "market_scope_level",
    "market_capacity_label",
    "market_variant_label",
    "reference_price",
    "reference_source_name",
    "reference_sheet_name",
    "reference_fetched_at",
    "suspicious_low_cluster_count",
    "suspicious_low_row_count",
    "suspicious_high_cluster_count",
    "suspicious_high_row_count",
    "cluster_count",
    "search_text",
    "search_text_normalized",
    "model_group_normalized",
]

CLUSTER_FIELDNAMES = [
    "run_key",
    "brand_title",
    "series_title",
    "model_title",
    "group_title",
    "condition_bucket",
    "selected_gprice_label",
    "cluster_id",
    "dstatus",
    "sample_count",
    "seller_count",
    "median_price",
    "price_range",
    "confidence_score",
    "confidence_label",
    "score",
    "selection_mode",
    "market_scope_level",
]
MAX_SKIPPED_DETAILS = 200


def _write_csv(path: Path, fieldnames: List[str], rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows_list = list(rows)
    with path.open("w", encoding="utf-8", newline="") as fh:
        if not rows_list:
            fh.write("")
            return
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows_list:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def build_live_market_payload(storage: DGTeamStorage, run_key: str = "") -> Dict[str, Any]:
    effective_run_key = str(run_key or storage.get_preferred_run_key() or "").strip()
    if not effective_run_key:
        raise ValueError("No run_key available for live market build.")

    built_at = time.strftime("%Y-%m-%d %H:%M:%S")
    rules = load_rules()
    reference_map = storage.get_external_reference_map()
    reference_context = storage.get_latest_reference_import_run()
    run_rows = storage.get_run_quote_rows(effective_run_key)
    grouped_rows: Dict[tuple[str, str, str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    for row in run_rows:
        key = (
            str(row.get("brand_title") or "").strip(),
            str(row.get("series_title") or "").strip(),
            str(row.get("model_title") or "").strip(),
            str(row.get("group_title") or "").strip(),
            str(row.get("condition_bucket") or "").strip(),
        )
        grouped_rows[key].append(dict(row))

    snapshot_rows: List[Dict[str, Any]] = []
    cluster_rows: List[Dict[str, Any]] = []
    skipped_candidates = 0
    skipped_details: List[Dict[str, Any]] = []
    skipped_reason_counts: Dict[str, int] = defaultdict(int)
    selection_mode_counts: Dict[str, int] = defaultdict(int)
    fallback_snapshot_count = 0

    for key, rows in grouped_rows.items():
        brand_title, series_title, model_title, group_title, condition_bucket = key
        row_count = len(rows)
        source_count = len({merchant_key(row) for row in rows})
        imported_values = [str(row.get("imported_at") or "") for row in rows if str(row.get("imported_at") or "").strip()]
        latest_imported_at = max(imported_values) if imported_values else ""
        numeric_prices = [int(price) for price in (parse_price_int(row.get("price_text")) for row in rows) if price is not None]
        latest_gprice = ""
        if rows:
            latest_gprice = str(rows[0].get("gprice") or "").strip()

        market_v1 = build_market_v1(
            rows=rows,
            brand_title=brand_title,
            series_title=series_title,
            model_title=model_title,
            group_title=group_title,
            reference_map=reference_map,
            reference_context=reference_context,
            rules=rules,
        )
        selection = dict(market_v1.get("selection") or {})
        market_scope = dict(market_v1.get("market_scope") or {})
        if not market_v1.get("ok"):
            reason = str(market_v1.get("reason") or "unknown")
            skipped_reason_counts[reason] += 1
            if len(skipped_details) < MAX_SKIPPED_DETAILS:
                skipped_details.append(
                    {
                        "brand_title": brand_title,
                        "series_title": series_title,
                        "model_title": model_title,
                        "group_title": group_title,
                        "condition_bucket": condition_bucket,
                        "reason": reason,
                        "selection": selection,
                        "market_scope": market_scope,
                    }
                )
            skipped_candidates += 1
            continue

        search_text = " ".join(part for part in (brand_title, series_title, model_title, group_title, condition_bucket) if part)
        best = dict(market_v1.get("best_cluster") or {})
        reference = dict(market_v1.get("reference") or {})
        flags = dict(market_v1.get("flags") or {})
        explanation = dict(market_v1.get("explanation") or {})
        selected_labels = [str(label or "").strip() for label in list(market_v1.get("selected_gprice_labels") or []) if str(label or "").strip()]
        selected_label = selected_labels[0] if selected_labels else latest_gprice
        selection_mode = str(selection.get("mode") or "")
        if selection_mode:
            selection_mode_counts[selection_mode] += 1
        if bool(selection.get("fallback_applied")):
            fallback_snapshot_count += 1

        snapshot_rows.append(
            {
                "run_key": effective_run_key,
                "brand_title": brand_title,
                "series_title": series_title,
                "model_title": model_title,
                "group_title": group_title,
                "condition_bucket": condition_bucket,
                "selected_gprice_label": selected_label,
                "selected_gprice_labels": "|".join(selected_labels),
                "latest_gprice": selected_label,
                "latest_imported_at": latest_imported_at,
                "source_row_count": row_count,
                "source_count": source_count,
                "min_price": min(numeric_prices) if numeric_prices else 0,
                "max_price": max(numeric_prices) if numeric_prices else 0,
                "market_price": int(best.get("median_price") or 0),
                "price_range": str(best.get("price_range") or ""),
                "trusted_status": str(best.get("dstatus") or ""),
                "trusted_sample_count": int(best.get("sample_count") or 0),
                "trusted_seller_count": int(best.get("seller_count") or 0),
                "confidence_score": int(best.get("confidence_score") or 0),
                "confidence_label": str(best.get("confidence_label") or ""),
                "selection_mode": selection_mode,
                "selection_fallback_applied": 1 if bool(selection.get("fallback_applied")) else 0,
                "selection_sample_count": int(selection.get("selected_row_count") or 0),
                "selection_summary": str(explanation.get("selection_summary") or ""),
                "market_scope_level": str(market_scope.get("scope_level") or ""),
                "market_capacity_label": str(market_scope.get("capacity_label") or ""),
                "market_variant_label": str(market_scope.get("variant_label") or ""),
                "reference_price": int(reference.get("price") or 0),
                "reference_source_name": str(reference.get("source_name") or reference.get("source_title") or ""),
                "reference_sheet_name": str(reference.get("sheet_name") or ""),
                "reference_fetched_at": str(reference.get("fetched_at") or ""),
                "suspicious_low_cluster_count": int(flags.get("suspicious_low_cluster_count") or 0),
                "suspicious_low_row_count": int(flags.get("suspicious_low_row_count") or 0),
                "suspicious_high_cluster_count": int(flags.get("suspicious_high_cluster_count") or 0),
                "suspicious_high_row_count": int(flags.get("suspicious_high_row_count") or 0),
                "cluster_count": int(market_v1.get("cluster_count") or 0),
                "search_text": search_text,
                "search_text_normalized": normalize_search_text(search_text),
                "model_group_normalized": normalize_search_text(" ".join(part for part in (model_title, group_title) if part)),
            }
        )

        for cluster in list(market_v1.get("clusters") or []):
            cluster_rows.append(
                {
                    "run_key": effective_run_key,
                    "brand_title": brand_title,
                    "series_title": series_title,
                    "model_title": model_title,
                    "group_title": group_title,
                    "condition_bucket": condition_bucket,
                    "selected_gprice_label": selected_label,
                    "cluster_id": int(cluster.get("cluster_id") or 0),
                    "dstatus": str(cluster.get("dstatus") or ""),
                    "sample_count": int(cluster.get("sample_count") or 0),
                    "seller_count": int(cluster.get("seller_count") or 0),
                    "median_price": int(cluster.get("median_price") or 0),
                    "price_range": str(cluster.get("price_range") or ""),
                    "confidence_score": int(cluster.get("confidence_score") or 0),
                    "confidence_label": str(cluster.get("confidence_label") or ""),
                    "score": cluster.get("score") or 0,
                    "selection_mode": selection_mode,
                    "market_scope_level": str(market_scope.get("scope_level") or ""),
                }
            )

    summary = {
        "run_key": effective_run_key,
        "built_at": built_at,
        "reference_context": {
            "import_id": int(reference_context.get("import_id") or 0) if reference_context else 0,
            "source_hint": str(reference_context.get("source_hint") or "") if reference_context else "",
            "fetched_at": str(reference_context.get("finished_at") or reference_context.get("created_at") or "") if reference_context else "",
        },
        "counts": {
            "source_candidates": len(grouped_rows),
            "source_rows": len(run_rows),
            "published_snapshots": len(snapshot_rows),
            "cluster_rows": len(cluster_rows),
            "skipped_candidates": skipped_candidates,
            "fallback_snapshots": fallback_snapshot_count,
        },
        "selection_modes": dict(selection_mode_counts),
        "skipped_reason_counts": dict(skipped_reason_counts),
        "skipped_details": skipped_details,
    }
    return {
        "run_key": effective_run_key,
        "built_at": built_at,
        "summary": summary,
        "snapshot_rows": snapshot_rows,
        "cluster_rows": cluster_rows,
    }


def export_live_market_payload(
    payload: Mapping[str, Any],
    outdir: Path,
    *,
    public_outdir: Path | None = None,
) -> Dict[str, Any]:
    target_dir = Path(outdir).expanduser().resolve()
    public_dir = Path(public_outdir).expanduser().resolve() if public_outdir is not None else target_dir
    snapshot_path = target_dir / "market_v1_snapshot.csv"
    cluster_path = target_dir / "market_v1_clusters.csv"
    summary_path = target_dir / "summary.json"
    public_snapshot_path = public_dir / snapshot_path.name
    public_cluster_path = public_dir / cluster_path.name
    public_summary_path = public_dir / summary_path.name

    snapshot_rows = list(payload.get("snapshot_rows") or [])
    cluster_rows = list(payload.get("cluster_rows") or [])
    summary = dict(payload.get("summary") or {})
    summary.setdefault("outputs", {})
    summary["outputs"].update(
        {
            "snapshot_csv": str(public_snapshot_path),
            "clusters_csv": str(public_cluster_path),
            "summary_json": str(public_summary_path),
        }
    )

    _write_csv(snapshot_path, SNAPSHOT_FIELDNAMES, snapshot_rows)
    _write_csv(cluster_path, CLUSTER_FIELDNAMES, cluster_rows)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    write_json_utf8(summary_path, summary)
    return {
        "outdir": str(target_dir),
        "snapshot_csv": str(snapshot_path),
        "clusters_csv": str(cluster_path),
        "summary_json": str(summary_path),
        "public_outdir": str(public_dir),
        "public_snapshot_csv": str(public_snapshot_path),
        "public_clusters_csv": str(public_cluster_path),
        "public_summary_json": str(public_summary_path),
        "counts": dict(summary.get("counts") or {}),
    }


def publish_live_market(storage: DGTeamStorage, run_key: str = "") -> Dict[str, Any]:
    payload = build_live_market_payload(storage, run_key)
    publish_result = storage.publish_market_snapshots(
        payload["run_key"],
        payload["snapshot_rows"],
        summary=payload["summary"],
        published_at=payload["built_at"],
    )
    reference_context = dict(payload["summary"].get("reference_context") or {})
    storage.append_event(
        payload["run_key"],
        "live_market_published",
        {
            "time": payload["built_at"],
            "run_key": payload["run_key"],
            "snapshot_count": int(publish_result.get("snapshot_count") or 0),
            "cluster_row_count": int(len(payload["cluster_rows"])),
            "reference_import_id": int(reference_context.get("import_id") or 0),
            "reference_fetched_at": str(reference_context.get("fetched_at") or ""),
        },
        event_key=f"live_market_published:{payload['run_key']}:{payload['built_at']}",
    )
    return {
        "run_key": payload["run_key"],
        "built_at": payload["built_at"],
        "snapshot_count": int(publish_result.get("snapshot_count") or 0),
        "cluster_row_count": int(len(payload["cluster_rows"])),
        "summary": dict(payload["summary"] or {}),
    }
