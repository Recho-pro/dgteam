from __future__ import annotations

import json
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional


def get_market_snapshot_count(storage: Any, run_key: Optional[str] = None) -> int:
    with storage.connect() as conn:
        if run_key:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM market_snapshots WHERE run_key = ?",
                (str(run_key or "").strip(),),
            ).fetchone()
            return int(row["c"] or 0) if row else 0
        row = conn.execute("SELECT COUNT(*) AS c FROM market_snapshots").fetchone()
        return int(row["c"] or 0) if row else 0


def get_live_market_run_key(storage: Any) -> str:
    preferred_live = storage.get_app_state("live_market_run_key", "")
    if preferred_live and get_market_snapshot_count(storage, preferred_live) > 0:
        return preferred_live

    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT run_key
            FROM market_snapshots
            GROUP BY run_key
            ORDER BY MAX(published_at) DESC, run_key DESC
            LIMIT 1
            """
        ).fetchone()
    if row and row["run_key"]:
        return str(row["run_key"] or "")
    return storage.get_preferred_run_key()


def publish_market_snapshots(
    storage: Any,
    run_key: str,
    rows: Iterable[Mapping[str, Any]],
    *,
    summary: Optional[Mapping[str, Any]] = None,
    published_at: Optional[str] = None,
) -> Dict[str, Any]:
    effective_run_key = str(run_key or "").strip()
    if not effective_run_key:
        raise ValueError("run_key is required for publish_market_snapshots")

    published_time = str(published_at or time.strftime("%Y-%m-%d %H:%M:%S"))
    normalized_rows: List[tuple[Any, ...]] = []
    for row in rows:
        normalized_rows.append(
            (
                effective_run_key,
                str(row.get("brand_title") or "").strip(),
                str(row.get("series_title") or "").strip(),
                str(row.get("model_title") or "").strip(),
                str(row.get("group_title") or "").strip(),
                str(row.get("condition_bucket") or "").strip(),
                str(row.get("selected_gprice_label") or "").strip(),
                str(row.get("selected_gprice_labels") or "").strip(),
                str(row.get("latest_gprice") or "").strip(),
                str(row.get("latest_imported_at") or "").strip(),
                int(row.get("source_row_count") or 0),
                int(row.get("source_count") or 0),
                int(row.get("min_price") or 0),
                int(row.get("max_price") or 0),
                int(row.get("market_price") or 0),
                str(row.get("price_range") or "").strip(),
                str(row.get("trusted_status") or "").strip(),
                int(row.get("trusted_sample_count") or 0),
                int(row.get("trusted_seller_count") or 0),
                int(row.get("confidence_score") or 0),
                str(row.get("confidence_label") or "").strip(),
                int(row.get("reference_price") or 0),
                str(row.get("reference_source_name") or "").strip(),
                str(row.get("reference_sheet_name") or "").strip(),
                str(row.get("reference_fetched_at") or "").strip(),
                int(row.get("suspicious_low_cluster_count") or 0),
                int(row.get("suspicious_low_row_count") or 0),
                int(row.get("suspicious_high_cluster_count") or 0),
                int(row.get("suspicious_high_row_count") or 0),
                int(row.get("cluster_count") or 0),
                published_time,
                str(row.get("search_text") or "").strip(),
                str(row.get("search_text_normalized") or "").strip(),
                str(row.get("model_group_normalized") or "").strip(),
            )
        )

    summary_payload = dict(summary or {})
    summary_payload.update(
        {
            "run_key": effective_run_key,
            "snapshot_count": len(normalized_rows),
            "published_at": published_time,
        }
    )
    summary_json = json.dumps(summary_payload, ensure_ascii=False)

    with storage.connect() as conn:
        conn.execute("DELETE FROM market_snapshots WHERE run_key = ?", (effective_run_key,))
        if normalized_rows:
            conn.executemany(
                """
                INSERT INTO market_snapshots (
                    run_key, brand_title, series_title, model_title, group_title, condition_bucket,
                    selected_gprice_label, selected_gprice_labels, latest_gprice, latest_imported_at,
                    source_row_count, source_count, min_price, max_price, market_price, price_range,
                    trusted_status, trusted_sample_count, trusted_seller_count, confidence_score,
                    confidence_label, reference_price, reference_source_name, reference_sheet_name,
                    reference_fetched_at, suspicious_low_cluster_count, suspicious_low_row_count,
                    suspicious_high_cluster_count, suspicious_high_row_count, cluster_count,
                    published_at, search_text, search_text_normalized, model_group_normalized
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                normalized_rows,
            )
        for state_key, state_value in (
            ("live_market_run_key", effective_run_key),
            ("live_market_published_at", published_time),
            ("live_market_summary_json", summary_json),
        ):
            conn.execute(
                """
                INSERT INTO app_state (state_key, state_value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(state_key) DO UPDATE SET
                    state_value = excluded.state_value,
                    updated_at = excluded.updated_at
                """,
                (state_key, state_value, published_time),
            )

    return {
        "run_key": effective_run_key,
        "snapshot_count": len(normalized_rows),
        "published_at": published_time,
        "summary": summary_payload,
    }


def list_market_snapshot_candidates(storage: Any, run_key: Optional[str] = None) -> List[Dict[str, Any]]:
    effective_run_key = str(run_key or storage.get_live_market_run_key() or "").strip()
    if not effective_run_key:
        return []
    with storage.connect() as conn:
        rows = conn.execute(
            """
            SELECT
                run_key,
                brand_title,
                series_title,
                model_title,
                COALESCE(group_title, '') AS group_title,
                COALESCE(condition_bucket, '') AS condition_bucket,
                COALESCE(source_row_count, 0) AS row_count,
                COALESCE(source_count, 0) AS source_count,
                COALESCE(min_price, 0) AS min_price,
                COALESCE(max_price, 0) AS max_price,
                COALESCE(latest_imported_at, '') AS latest_imported_at,
                COALESCE(latest_gprice, '') AS latest_gprice,
                COALESCE(search_text, '') AS search_text,
                COALESCE(search_text_normalized, '') AS search_text_normalized,
                COALESCE(model_group_normalized, '') AS model_group_normalized
            FROM market_snapshots
            WHERE run_key = ?
            ORDER BY row_count DESC, latest_imported_at DESC, brand_title ASC, series_title ASC, model_title ASC
            """,
            (effective_run_key,),
        ).fetchall()
    return [
        {key: row[key] for key in row.keys()}
        | {"data_source": "quote_rows", "external_key": "", "external_title": "", "external_source_title": ""}
        for row in rows
    ]


def list_live_sku_candidates(storage: Any, run_key: Optional[str] = None) -> List[Dict[str, Any]]:
    effective_run_key = str(run_key or storage.get_live_market_run_key() or "").strip()
    if effective_run_key and storage.get_market_snapshot_count(effective_run_key) > 0:
        results = storage.list_market_snapshot_candidates(effective_run_key)
        results.extend(storage.list_external_reference_candidates())
        return results
    return storage.list_sku_candidates(effective_run_key or None)


def get_market_snapshot_row(
    storage: Any,
    *,
    brand_title: str,
    series_title: str,
    model_title: str,
    group_title: str = "",
    condition_bucket: str = "",
    run_key: Optional[str] = None,
) -> Dict[str, Any]:
    effective_run_key = str(run_key or storage.get_live_market_run_key() or "").strip()
    if not effective_run_key:
        return {}
    params = (
        effective_run_key,
        str(brand_title or "").strip(),
        str(series_title or "").strip(),
        str(model_title or "").strip(),
        str(group_title or "").strip(),
        str(condition_bucket or "").strip(),
    )
    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM market_snapshots
            WHERE run_key = ?
              AND brand_title = ?
              AND series_title = ?
              AND model_title = ?
              AND COALESCE(group_title, '') = ?
              AND COALESCE(condition_bucket, '') = ?
            LIMIT 1
            """,
            params,
        ).fetchone()
    return {key: row[key] for key in row.keys()} if row else {}
