from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Iterable, Mapping


def _payload_json_from_task(task: Mapping[str, Any]) -> str:
    try:
        return json.dumps(dict(task), ensure_ascii=False)
    except Exception:
        return "{}"


def upsert_run(
    storage: Any,
    run_key: str,
    source_dir: Path,
    summary_json: str,
    *,
    status: str = "",
    started_at: str = "",
    finished_at: str = "",
) -> None:
    created_at = time.strftime("%Y-%m-%d %H:%M:%S")
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO runs (run_key, source_dir, started_at, finished_at, status, summary_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_key) DO UPDATE SET
                source_dir = excluded.source_dir,
                started_at = excluded.started_at,
                finished_at = excluded.finished_at,
                status = excluded.status,
                summary_json = excluded.summary_json
            """,
            (run_key, str(source_dir), started_at, finished_at, status, summary_json, created_at),
        )


def upsert_task(storage: Any, run_key: str, task: Mapping[str, Any], payload_json: str) -> None:
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO tasks (
                run_key, task_key, event_time, brand_title, series_title, model_title, city_title,
                status, code, msg, error_text, total_rows_seen, row_count, drop_by_date,
                drop_out_of_stock, drop_by_apple_dstatus, drop_by_non_apple_dstatus,
                drop_invalid_price, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_key, task_key) DO UPDATE SET
                event_time = excluded.event_time,
                brand_title = excluded.brand_title,
                series_title = excluded.series_title,
                model_title = excluded.model_title,
                city_title = excluded.city_title,
                status = excluded.status,
                code = excluded.code,
                msg = excluded.msg,
                error_text = excluded.error_text,
                total_rows_seen = excluded.total_rows_seen,
                row_count = excluded.row_count,
                drop_by_date = excluded.drop_by_date,
                drop_out_of_stock = excluded.drop_out_of_stock,
                drop_by_apple_dstatus = excluded.drop_by_apple_dstatus,
                drop_by_non_apple_dstatus = excluded.drop_by_non_apple_dstatus,
                drop_invalid_price = excluded.drop_invalid_price,
                payload_json = excluded.payload_json
            """,
            (
                run_key,
                task.get("key", ""),
                task.get("time", ""),
                task.get("brand_title", ""),
                task.get("series_title", ""),
                task.get("model_title", ""),
                task.get("city_title", ""),
                task.get("status", ""),
                task.get("code", ""),
                task.get("msg", ""),
                task.get("error", ""),
                int(task.get("total_rows_seen") or 0),
                int(task.get("row_count") or 0),
                int(task.get("drop_by_date") or 0),
                int(task.get("drop_out_of_stock") or 0),
                int(task.get("drop_by_apple_dstatus") or 0),
                int(task.get("drop_by_non_apple_dstatus") or 0),
                int(task.get("drop_invalid_price") or 0),
                payload_json,
            ),
        )


def ensure_run_tasks(storage: Any, run_key: str, tasks: Iterable[Mapping[str, Any]]) -> int:
    inserted_or_updated = 0
    with storage.connect() as conn:
        for task in tasks:
            conn.execute(
                """
                INSERT INTO tasks (
                    run_key, task_key, event_time, brand_title, series_title, model_title, city_title,
                    status, code, msg, error_text, total_rows_seen, row_count, drop_by_date,
                    drop_out_of_stock, drop_by_apple_dstatus, drop_by_non_apple_dstatus,
                    drop_invalid_price, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_key, task_key) DO UPDATE SET
                    brand_title = excluded.brand_title,
                    series_title = excluded.series_title,
                    model_title = excluded.model_title,
                    city_title = excluded.city_title,
                    payload_json = CASE
                        WHEN tasks.payload_json IS NULL OR tasks.payload_json = '' THEN excluded.payload_json
                        ELSE tasks.payload_json
                    END
                """,
                (
                    run_key,
                    task.get("key", ""),
                    task.get("time", ""),
                    task.get("brand_title", ""),
                    task.get("series_title", ""),
                    task.get("model_title", ""),
                    task.get("city_title", ""),
                    task.get("status", "pending"),
                    task.get("code", ""),
                    task.get("msg", ""),
                    task.get("error", ""),
                    int(task.get("total_rows_seen") or 0),
                    int(task.get("row_count") or 0),
                    int(task.get("drop_by_date") or 0),
                    int(task.get("drop_out_of_stock") or 0),
                    int(task.get("drop_by_apple_dstatus") or 0),
                    int(task.get("drop_by_non_apple_dstatus") or 0),
                    int(task.get("drop_invalid_price") or 0),
                    _payload_json_from_task(task),
                ),
            )
            inserted_or_updated += 1
    return inserted_or_updated


def requeue_running_tasks(storage: Any, run_key: str) -> int:
    with storage.connect() as conn:
        cursor = conn.execute(
            """
            UPDATE tasks
            SET status = 'pending',
                code = '',
                msg = '',
                error_text = ''
            WHERE run_key = ? AND status = 'running'
            """,
            (run_key,),
        )
        return int(cursor.rowcount or 0)


def insert_quote_rows(storage: Any, run_key: str, rows: Iterable[Mapping[str, Any]]) -> int:
    batch = storage._prepare_quote_row_batch(run_key, rows)
    if not batch:
        return 0

    with storage.connect() as conn:
        storage._insert_quote_row_batch(conn, batch)
    return len(batch)


def replace_quote_rows_for_task(storage: Any, run_key: str, task_key: str, rows: Iterable[Mapping[str, Any]]) -> int:
    rows_list = list(rows)
    normalized_batch = storage._prepare_quote_row_batch(run_key, rows_list, task_key_override=task_key) if rows_list else []
    with storage.connect() as conn:
        conn.execute(
            "DELETE FROM quote_rows WHERE run_key = ? AND task_key = ?",
            (run_key, task_key),
        )
        if normalized_batch:
            storage._insert_quote_row_batch(conn, normalized_batch)
    return len(normalized_batch)
