from __future__ import annotations

from typing import Any, Dict, List, Optional


def _normalize_search_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    return "".join(ch for ch in text if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def get_live_marker(storage: Any, run_key: Optional[str] = None) -> Dict[str, Any]:
    live_state = storage.get_live_market_state()
    effective_run_key = run_key or live_state.get("run_key") or storage.get_preferred_run_key()
    if not effective_run_key:
        return {
            "run_key": "",
            "quote_count": 0,
            "market_snapshot_count": 0,
            "latest_imported_at": "",
            "task_count": 0,
            "latest_task_event": "",
            "latest_run_event": "",
            "published_at": "",
            "live_source": "",
        }
    with storage.connect() as conn:
        quote_row = conn.execute(
            """
            SELECT COUNT(*) AS quote_count, MAX(imported_at) AS latest_imported_at
            FROM quote_rows
            WHERE run_key = ?
            """,
            (effective_run_key,),
        ).fetchone()
        snapshot_row = conn.execute(
            """
            SELECT COUNT(*) AS snapshot_count, MAX(published_at) AS published_at
            FROM market_snapshots
            WHERE run_key = ?
            """,
            (effective_run_key,),
        ).fetchone()
        task_row = conn.execute(
            """
            SELECT COUNT(*) AS task_count, MAX(event_time) AS latest_task_event
            FROM tasks
            WHERE run_key = ?
            """,
            (effective_run_key,),
        ).fetchone()
        event_row = conn.execute(
            """
            SELECT MAX(event_time) AS latest_run_event
            FROM run_events
            WHERE run_key = ?
            """,
            (effective_run_key,),
        ).fetchone()
    return {
        "run_key": effective_run_key,
        "quote_count": int(quote_row["quote_count"] or 0),
        "market_snapshot_count": int(snapshot_row["snapshot_count"] or 0),
        "latest_imported_at": str(quote_row["latest_imported_at"] or ""),
        "task_count": int(task_row["task_count"] or 0),
        "latest_task_event": str(task_row["latest_task_event"] or ""),
        "latest_run_event": str(event_row["latest_run_event"] or ""),
        "published_at": str(snapshot_row["published_at"] or live_state.get("published_at") or ""),
        "live_source": "market_snapshots" if int(snapshot_row["snapshot_count"] or 0) > 0 else "quote_rows",
    }


def list_sku_candidates(storage: Any, run_key: Optional[str] = None) -> List[Dict[str, Any]]:
    effective_run_key = run_key or storage.get_preferred_run_key()
    if not effective_run_key:
        return []
    with storage.connect() as conn:
        rows = conn.execute(
            """
            SELECT
                brand_title,
                series_title,
                model_title,
                group_title,
                condition_bucket,
                COUNT(*) AS row_count,
                COUNT(
                    DISTINCT CASE
                        WHEN TRIM(COALESCE(sname, '')) <> '' THEN LOWER(TRIM(sname))
                        WHEN TRIM(COALESCE(sno, '')) <> '' THEN LOWER(TRIM(sno))
                        ELSE CAST(id AS TEXT)
                    END
                ) AS source_count,
                MIN(CASE WHEN price_text GLOB '[0-9]*' AND price_text <> '' THEN CAST(price_text AS INTEGER) END) AS min_price,
                MAX(CASE WHEN price_text GLOB '[0-9]*' AND price_text <> '' THEN CAST(price_text AS INTEGER) END) AS max_price,
                MAX(imported_at) AS latest_imported_at,
                MAX(CASE WHEN gprice IS NULL THEN '' ELSE gprice END) AS latest_gprice
            FROM quote_rows
            WHERE run_key = ?
            GROUP BY
                brand_title,
                series_title,
                model_title,
                group_title,
                condition_bucket
            ORDER BY row_count DESC, latest_imported_at DESC, brand_title ASC, series_title ASC, model_title ASC
            """,
            (effective_run_key,),
        ).fetchall()
    results: List[Dict[str, Any]] = []
    for row in rows:
        brand_title = str(row["brand_title"] or "").strip()
        series_title = str(row["series_title"] or "").strip()
        model_title = str(row["model_title"] or "").strip()
        group_title = str(row["group_title"] or "").strip()
        condition_bucket = str(row["condition_bucket"] or "").strip()
        search_text = " ".join(
            part for part in (brand_title, series_title, model_title, group_title, condition_bucket) if part
        )
        results.append(
            {
                "run_key": effective_run_key,
                "brand_title": brand_title,
                "series_title": series_title,
                "model_title": model_title,
                "group_title": group_title,
                "condition_bucket": condition_bucket,
                "row_count": int(row["row_count"] or 0),
                "source_count": int(row["source_count"] or 0),
                "min_price": int(row["min_price"] or 0) if row["min_price"] is not None else 0,
                "max_price": int(row["max_price"] or 0) if row["max_price"] is not None else 0,
                "latest_imported_at": str(row["latest_imported_at"] or ""),
                "latest_gprice": str(row["latest_gprice"] or ""),
                "search_text": search_text,
                "search_text_normalized": _normalize_search_text(search_text),
                "model_group_normalized": _normalize_search_text(" ".join(part for part in (model_title, group_title) if part)),
                "data_source": "quote_rows",
                "external_key": "",
                "external_title": "",
                "external_source_title": "",
            }
        )
    results.extend(storage.list_external_reference_candidates())
    return results


def get_sku_rows(
    storage: Any,
    *,
    brand_title: str,
    series_title: str,
    model_title: str,
    group_title: str = "",
    condition_bucket: str = "",
    run_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    effective_run_key = run_key or storage.get_preferred_run_key()
    if not effective_run_key:
        return []
    params = (
        effective_run_key,
        str(brand_title or "").strip(),
        str(series_title or "").strip(),
        str(model_title or "").strip(),
        str(group_title or "").strip(),
        str(condition_bucket or "").strip(),
    )
    with storage.connect() as conn:
        rows = conn.execute(
            """
            SELECT
                id,
                imported_at,
                run_key,
                brand_title,
                series_title,
                model_title,
                group_title,
                condition_bucket,
                COALESCE(gprice, '') AS gprice,
                COALESCE(gprice_two, '') AS gprice_two,
                COALESCE(price_text, '') AS price_text,
                COALESCE(dstatus, '') AS dstatus,
                COALESCE(sname, '') AS sname,
                COALESCE(sno, '') AS sno,
                COALESCE(city_title, '') AS city_title
            FROM quote_rows
            WHERE run_key = ?
              AND brand_title = ?
              AND series_title = ?
              AND model_title = ?
              AND group_title = ?
              AND condition_bucket = ?
            ORDER BY imported_at DESC, id DESC
            """,
            params,
        ).fetchall()
    return [{key: row[key] for key in row.keys()} for row in rows]


def get_run_quote_rows(storage: Any, run_key: Optional[str] = None) -> List[Dict[str, Any]]:
    effective_run_key = run_key or storage.get_preferred_run_key()
    if not effective_run_key:
        return []
    with storage.connect() as conn:
        rows = conn.execute(
            """
            SELECT
                id,
                imported_at,
                run_key,
                brand_title,
                series_title,
                model_title,
                group_title,
                condition_bucket,
                COALESCE(gprice, '') AS gprice,
                COALESCE(gprice_two, '') AS gprice_two,
                COALESCE(price_text, '') AS price_text,
                COALESCE(dstatus, '') AS dstatus,
                COALESCE(sname, '') AS sname,
                COALESCE(sno, '') AS sno,
                COALESCE(city_title, '') AS city_title
            FROM quote_rows
            WHERE run_key = ?
            ORDER BY brand_title, series_title, model_title, group_title, condition_bucket, imported_at DESC, id DESC
            """,
            (effective_run_key,),
        ).fetchall()
    return [{key: row[key] for key in row.keys()} for row in rows]


def get_model_family_rows(
    storage: Any,
    *,
    brand_title: str,
    series_title: str,
    model_title: str,
    condition_bucket: str = "",
    run_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    effective_run_key = run_key or storage.get_preferred_run_key()
    if not effective_run_key:
        return []
    params = (
        effective_run_key,
        str(brand_title or "").strip(),
        str(series_title or "").strip(),
        str(model_title or "").strip(),
        str(condition_bucket or "").strip(),
    )
    with storage.connect() as conn:
        rows = conn.execute(
            """
            SELECT
                id,
                imported_at,
                run_key,
                brand_title,
                series_title,
                model_title,
                group_title,
                condition_bucket,
                COALESCE(gprice, '') AS gprice,
                COALESCE(price_text, '') AS price_text,
                COALESCE(dstatus, '') AS dstatus,
                COALESCE(sname, '') AS sname,
                COALESCE(sno, '') AS sno
            FROM quote_rows
            WHERE run_key = ?
              AND brand_title = ?
              AND series_title = ?
              AND model_title = ?
              AND condition_bucket = ?
            ORDER BY imported_at DESC, id DESC
            """,
            params,
        ).fetchall()
    return [{key: row[key] for key in row.keys()} for row in rows]
