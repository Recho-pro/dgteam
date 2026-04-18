from __future__ import annotations

import csv
import json
import sqlite3
import time
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from . import storage_publish, storage_query, storage_state, storage_write


SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS runs (
    run_key TEXT PRIMARY KEY,
    source_dir TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    status TEXT,
    summary_json TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    run_key TEXT NOT NULL,
    task_key TEXT NOT NULL,
    event_time TEXT,
    brand_title TEXT,
    series_title TEXT,
    model_title TEXT,
    city_title TEXT,
    status TEXT,
    code TEXT,
    msg TEXT,
    error_text TEXT,
    total_rows_seen INTEGER DEFAULT 0,
    row_count INTEGER DEFAULT 0,
    drop_by_date INTEGER DEFAULT 0,
    drop_out_of_stock INTEGER DEFAULT 0,
    drop_by_apple_dstatus INTEGER DEFAULT 0,
    drop_by_non_apple_dstatus INTEGER DEFAULT 0,
    drop_invalid_price INTEGER DEFAULT 0,
    payload_json TEXT,
    PRIMARY KEY (run_key, task_key)
);

CREATE TABLE IF NOT EXISTS quote_rows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_key TEXT NOT NULL,
    task_key TEXT,
    brand_id TEXT,
    brand_title TEXT,
    series_id TEXT,
    series_title TEXT,
    model_id TEXT,
    model_title TEXT,
    city_id TEXT,
    city_title TEXT,
    group_title TEXT,
    gid TEXT,
    sid TEXT,
    cid TEXT,
    sno TEXT,
    sname TEXT,
    city_name TEXT,
    activation TEXT,
    dstatus TEXT,
    gprice TEXT,
    gprice_two TEXT,
    price_text TEXT,
    price_image_file TEXT,
    clean_scope TEXT,
    condition_bucket TEXT,
    is_target_price INTEGER,
    needs_review INTEGER,
    matched_positive_tags TEXT,
    matched_negative_tags TEXT,
    matched_sale_tags TEXT,
    exclude_reason TEXT,
    rule_note TEXT,
    imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS blacklist_models (
    model_id TEXT PRIMARY KEY,
    source TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    note TEXT,
    imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_key TEXT NOT NULL,
    event_key TEXT,
    event_time TEXT NOT NULL,
    event_type TEXT NOT NULL,
    task_key TEXT,
    brand_title TEXT,
    series_title TEXT,
    model_title TEXT,
    city_title TEXT,
    details_json TEXT
);

CREATE TABLE IF NOT EXISTS app_state (
    state_key TEXT PRIMARY KEY,
    state_value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS market_snapshots (
    run_key TEXT NOT NULL,
    brand_title TEXT NOT NULL,
    series_title TEXT NOT NULL,
    model_title TEXT NOT NULL,
    group_title TEXT NOT NULL DEFAULT '',
    condition_bucket TEXT NOT NULL DEFAULT '',
    selected_gprice_label TEXT,
    selected_gprice_labels TEXT,
    latest_gprice TEXT,
    latest_imported_at TEXT,
    source_row_count INTEGER DEFAULT 0,
    source_count INTEGER DEFAULT 0,
    min_price INTEGER DEFAULT 0,
    max_price INTEGER DEFAULT 0,
    market_price INTEGER DEFAULT 0,
    price_range TEXT,
    trusted_status TEXT,
    trusted_sample_count INTEGER DEFAULT 0,
    trusted_seller_count INTEGER DEFAULT 0,
    confidence_score INTEGER DEFAULT 0,
    confidence_label TEXT,
    reference_price INTEGER DEFAULT 0,
    reference_source_name TEXT,
    reference_sheet_name TEXT,
    reference_fetched_at TEXT,
    suspicious_low_cluster_count INTEGER DEFAULT 0,
    suspicious_low_row_count INTEGER DEFAULT 0,
    suspicious_high_cluster_count INTEGER DEFAULT 0,
    suspicious_high_row_count INTEGER DEFAULT 0,
    cluster_count INTEGER DEFAULT 0,
    published_at TEXT NOT NULL,
    search_text TEXT,
    search_text_normalized TEXT,
    model_group_normalized TEXT,
    PRIMARY KEY (run_key, brand_title, series_title, model_title, group_title, condition_bucket)
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks (run_key, status);
CREATE INDEX IF NOT EXISTS idx_quote_rows_run_target ON quote_rows (run_key, is_target_price);
CREATE INDEX IF NOT EXISTS idx_quote_rows_brand ON quote_rows (brand_title);
CREATE INDEX IF NOT EXISTS idx_quote_rows_run_task_imported ON quote_rows (run_key, task_key, imported_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_quote_rows_sku_lookup ON quote_rows (
    run_key,
    brand_title,
    series_title,
    model_title,
    group_title,
    condition_bucket,
    imported_at DESC,
    id DESC
);
CREATE INDEX IF NOT EXISTS idx_quote_rows_family_lookup ON quote_rows (
    run_key,
    brand_title,
    series_title,
    model_title,
    condition_bucket,
    imported_at DESC,
    id DESC
);
CREATE INDEX IF NOT EXISTS idx_quote_rows_run_catalog ON quote_rows (
    run_key,
    brand_title,
    series_title,
    model_title,
    group_title,
    condition_bucket
);
CREATE INDEX IF NOT EXISTS idx_run_events_type ON run_events (run_key, event_type, event_time);
CREATE UNIQUE INDEX IF NOT EXISTS idx_run_events_key ON run_events (run_key, event_key);
CREATE INDEX IF NOT EXISTS idx_market_snapshots_run_query ON market_snapshots (run_key, brand_title, series_title, model_title);
CREATE INDEX IF NOT EXISTS idx_market_snapshots_run_sort ON market_snapshots (run_key, source_row_count, latest_imported_at);

CREATE TABLE IF NOT EXISTS reference_import_runs (
    import_id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    ai_model TEXT,
    source_hint TEXT,
    image_count INTEGER DEFAULT 0,
    summary_json TEXT
);

CREATE TABLE IF NOT EXISTS reference_import_rows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    import_id INTEGER NOT NULL,
    image_name TEXT,
    source_title TEXT,
    raw_title TEXT NOT NULL,
    normalized_title TEXT NOT NULL,
    reference_price INTEGER NOT NULL,
    ocr_confidence TEXT,
    matched_run_key TEXT,
    matched_brand_title TEXT,
    matched_series_title TEXT,
    matched_model_title TEXT,
    matched_group_title TEXT,
    matched_condition_bucket TEXT,
    match_score REAL DEFAULT 0,
    payload_json TEXT,
    imported_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reference_import_runs_status ON reference_import_runs (status, created_at);
CREATE INDEX IF NOT EXISTS idx_reference_import_rows_import ON reference_import_rows (import_id, normalized_title);
CREATE INDEX IF NOT EXISTS idx_reference_import_rows_match ON reference_import_rows (
    import_id,
    matched_brand_title,
    matched_series_title,
    matched_model_title,
    matched_group_title,
    matched_condition_bucket
);
"""


QUOTE_ROWS_INSERT_SQL = """
    INSERT INTO quote_rows (
        run_key, task_key, brand_id, brand_title, series_id, series_title, model_id, model_title,
        city_id, city_title, group_title, gid, sid, cid, sno, sname, city_name,
        activation, dstatus, gprice, gprice_two, price_text, price_image_file,
        clean_scope, condition_bucket, is_target_price, needs_review,
        matched_positive_tags, matched_negative_tags, matched_sale_tags, exclude_reason, rule_note,
        imported_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def payload_json_from_task(task: Mapping[str, Any]) -> str:
    try:
        return json.dumps(dict(task), ensure_ascii=False)
    except Exception:
        return "{}"


def extract_run_date_from_run_key(run_key: str) -> str:
    match = re.search(r"(\d{4}-\d{2}-\d{2})$", str(run_key or ""))
    return match.group(1) if match else ""


BLACKLIST_FIELDNAMES = [
    "enabled",
    "model_id",
    "brand_title",
    "series_title",
    "model_title",
    "reason",
    "source_batch",
    "note",
]


BLACKLIST_REASON_NOTE_TEMPLATES: dict[str, str] = {
    "zero_row_ok_from_progress": "Imported from progress review on {source_batch}: zero matching rows remained after filtering, so this model stays blacklisted.",
    "final_code_3_from_progress": "Imported from progress review on {source_batch}: task ended with code 3 and was intentionally added to the blacklist.",
}


def normalize_search_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    return "".join(ch for ch in text if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def sku_title_from_parts(model_title: Any, group_title: Any) -> str:
    model = str(model_title or "").strip()
    group = str(group_title or "").strip()
    return f"{model}-{group}" if group else model


def parse_price_int(value: Any) -> Optional[int]:
    text = str(value or "").strip()
    if not text or not text.isdigit():
        return None
    try:
        return int(text)
    except Exception:
        return None


def percentile(values: List[int], ratio: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    if ratio <= 0:
        return ordered[0]
    if ratio >= 1:
        return ordered[-1]
    position = (len(ordered) - 1) * ratio
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight)


def parse_mmdd_label(label: Any) -> tuple[int, int]:
    text = str(label or "").strip()
    if len(text) < 5 or text[2] != "-":
        return (0, 0)
    try:
        month = int(text[:2])
        day = int(text[3:5])
    except Exception:
        return (0, 0)
    return (month, day)


def _looks_like_garbled_blacklist_note(note: str) -> bool:
    text = str(note or "").strip()
    if not text:
        return True
    if text.count("?") >= 6:
        return True
    return False


def normalize_blacklist_row(row: Mapping[str, Any]) -> Dict[str, str]:
    normalized = {field: str(row.get(field) or "") for field in BLACKLIST_FIELDNAMES}
    reason = normalized["reason"].strip()
    source_batch = normalized["source_batch"].strip() or "unknown batch"
    fallback_template = BLACKLIST_REASON_NOTE_TEMPLATES.get(reason)
    if fallback_template and _looks_like_garbled_blacklist_note(normalized["note"]):
        normalized["note"] = fallback_template.format(source_batch=source_batch)
    return normalized


def load_blacklist_rows(path: Path) -> List[Dict[str, str]]:
    target = Path(path).expanduser().resolve()
    if not target.exists():
        return []
    with target.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        return [normalize_blacklist_row(row) for row in reader]


def merge_blacklist_rows(existing_rows: Iterable[Mapping[str, Any]], new_rows: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    merged: List[Dict[str, str]] = []
    seen_model_ids: set[str] = set()
    existing_count = 0
    appended_count = 0

    def normalize_row(row: Mapping[str, Any]) -> Dict[str, str]:
        return normalize_blacklist_row(row)

    for row in existing_rows:
        normalized = normalize_row(row)
        model_id = normalized["model_id"].strip()
        if not model_id or model_id in seen_model_ids:
            continue
        seen_model_ids.add(model_id)
        merged.append(normalized)
        existing_count += 1

    for row in new_rows:
        normalized = normalize_row(row)
        model_id = normalized["model_id"].strip()
        if not model_id or model_id in seen_model_ids:
            continue
        seen_model_ids.add(model_id)
        merged.append(normalized)
        appended_count += 1

    merged.sort(key=lambda row: (row["brand_title"], row["series_title"], row["model_title"], row["model_id"]))
    return {
        "rows": merged,
        "existing_count": existing_count,
        "appended_count": appended_count,
        "total_count": len(merged),
    }


def write_blacklist_rows(path: Path, rows: Iterable[Mapping[str, Any]]) -> Path:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=BLACKLIST_FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow(normalize_blacklist_row(row))
    return target


class DGTeamStorage:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path).expanduser().resolve()

    def connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA_SQL)
            conn.execute("UPDATE quote_rows SET task_key = '' WHERE task_key IS NULL")
            conn.execute("UPDATE quote_rows SET group_title = '' WHERE group_title IS NULL")
            conn.execute("UPDATE quote_rows SET condition_bucket = '' WHERE condition_bucket IS NULL")

    def export_database_snapshot(self, target_path: Path) -> Dict[str, Any]:
        target = Path(target_path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_target = target.with_name(f"{target.name}.tmp")
        if temp_target.exists():
            temp_target.unlink()

        generated_at = time.strftime("%Y-%m-%d %H:%M:%S")
        with self.connect() as source_conn:
            vacuum_sql = "VACUUM main INTO '{0}'".format(str(temp_target).replace("'", "''"))
            source_conn.execute(vacuum_sql)

        temp_target.replace(target)
        return {
            "path": str(target),
            "size_bytes": int(target.stat().st_size),
            "generated_at": generated_at,
            "export_mode": "vacuum_into",
        }

    def _prepare_quote_row_batch(
        self,
        run_key: str,
        rows: Iterable[Mapping[str, Any]],
        *,
        task_key_override: str = "",
        imported_at: str = "",
    ) -> List[tuple]:
        timestamp = str(imported_at or time.strftime("%Y-%m-%d %H:%M:%S"))
        batch: List[tuple] = []
        run_date_suffix = extract_run_date_from_run_key(run_key)
        normalized_task_key_override = str(task_key_override or "").strip()

        for row in rows:
            task_key = normalized_task_key_override or str(row.get("task_key", "") or "").strip()
            model_id = str(row.get("model_id", "") or "").strip()
            city_id = str(row.get("city_id", "") or "").strip()
            if not task_key and model_id and city_id:
                task_key = f"{model_id}__{city_id}__{run_date_suffix}" if run_date_suffix else f"{model_id}__{city_id}"
            batch.append(
                (
                    run_key,
                    task_key,
                    row.get("brand_id", ""),
                    row.get("brand_title", ""),
                    row.get("series_id", ""),
                    row.get("series_title", ""),
                    row.get("model_id", ""),
                    row.get("model_title", ""),
                    row.get("city_id", ""),
                    row.get("city_title", ""),
                    row.get("group_title", "") or "",
                    row.get("GID", ""),
                    row.get("SID", ""),
                    row.get("CID", ""),
                    row.get("SNo", ""),
                    row.get("SName", ""),
                    row.get("cityName", ""),
                    row.get("activation", ""),
                    row.get("dstatus", ""),
                    row.get("GPrice", ""),
                    row.get("GPriceTwo", ""),
                    row.get("price_text", ""),
                    row.get("price_image_file", ""),
                    row.get("clean_scope", ""),
                    row.get("condition_bucket", "") or "",
                    int(row.get("is_target_price") or 0),
                    int(row.get("needs_review") or 0),
                    row.get("matched_positive_tags", ""),
                    row.get("matched_negative_tags", ""),
                    row.get("matched_sale_tags", ""),
                    row.get("exclude_reason", ""),
                    row.get("rule_note", ""),
                    timestamp,
                )
            )
        return batch

    def _insert_quote_row_batch(self, conn: sqlite3.Connection, batch: Iterable[tuple]) -> None:
        conn.executemany(QUOTE_ROWS_INSERT_SQL, batch)

    def get_app_state(self, state_key: str, default: str = "") -> str:
        return storage_state.get_app_state(self, state_key, default)

    def set_app_state(self, state_key: str, state_value: Any, *, updated_at: Optional[str] = None) -> None:
        storage_state.set_app_state(self, state_key, state_value, updated_at=updated_at)

    def get_live_market_state(self) -> Dict[str, Any]:
        return storage_state.get_live_market_state(self)

    def create_reference_import_run(
        self,
        *,
        ai_model: str,
        image_count: int,
        source_hint: str = "",
        status: str = "running",
        summary_json: str = "",
        created_at: Optional[str] = None,
    ) -> int:
        created = str(created_at or time.strftime("%Y-%m-%d %H:%M:%S"))
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO reference_import_runs (
                    created_at, finished_at, status, ai_model, source_hint, image_count, summary_json
                )
                VALUES (?, '', ?, ?, ?, ?, ?)
                """,
                (
                    created,
                    str(status or "running"),
                    str(ai_model or ""),
                    str(source_hint or ""),
                    int(image_count or 0),
                    str(summary_json or ""),
                ),
            )
            return int(cursor.lastrowid)

    def finalize_reference_import_run(
        self,
        import_id: int,
        *,
        status: str,
        summary: Optional[Mapping[str, Any]] = None,
        finished_at: Optional[str] = None,
    ) -> None:
        payload = json.dumps(dict(summary or {}), ensure_ascii=False)
        done_time = str(finished_at or time.strftime("%Y-%m-%d %H:%M:%S"))
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE reference_import_runs
                SET finished_at = ?, status = ?, summary_json = ?
                WHERE import_id = ?
                """,
                (done_time, str(status or ""), payload, int(import_id)),
            )

    def replace_reference_import_rows(self, import_id: int, rows: Iterable[Mapping[str, Any]]) -> int:
        imported_at = time.strftime("%Y-%m-%d %H:%M:%S")
        count = 0
        with self.connect() as conn:
            conn.execute("DELETE FROM reference_import_rows WHERE import_id = ?", (int(import_id),))
            for row in rows:
                conn.execute(
                    """
                    INSERT INTO reference_import_rows (
                        import_id, image_name, source_title, raw_title, normalized_title, reference_price,
                        ocr_confidence, matched_run_key, matched_brand_title, matched_series_title,
                        matched_model_title, matched_group_title, matched_condition_bucket, match_score,
                        payload_json, imported_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(import_id),
                        str(row.get("image_name") or ""),
                        str(row.get("source_title") or ""),
                        str(row.get("raw_title") or ""),
                        str(row.get("normalized_title") or ""),
                        int(row.get("reference_price") or 0),
                        str(row.get("ocr_confidence") or ""),
                        str(row.get("matched_run_key") or ""),
                        str(row.get("matched_brand_title") or ""),
                        str(row.get("matched_series_title") or ""),
                        str(row.get("matched_model_title") or ""),
                        str(row.get("matched_group_title") or ""),
                        str(row.get("matched_condition_bucket") or ""),
                        float(row.get("match_score") or 0),
                        payload_json_from_task(dict(row)),
                        imported_at,
                    ),
                )
                count += 1
        return count

    def get_latest_reference_import_run(self) -> Dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT import_id, created_at, finished_at, status, ai_model, source_hint, image_count, summary_json
                FROM reference_import_runs
                WHERE status = 'completed'
                ORDER BY created_at DESC, import_id DESC
                LIMIT 1
                """
            ).fetchone()
        if not row:
            return {}
        summary_json = {}
        raw_summary = str(row["summary_json"] or "")
        if raw_summary:
            try:
                summary_json = json.loads(raw_summary)
            except Exception:
                summary_json = {}
        return {
            "import_id": int(row["import_id"]),
            "created_at": str(row["created_at"] or ""),
            "finished_at": str(row["finished_at"] or ""),
            "status": str(row["status"] or ""),
            "ai_model": str(row["ai_model"] or ""),
            "source_hint": str(row["source_hint"] or ""),
            "image_count": int(row["image_count"] or 0),
            "summary": summary_json,
        }

    def get_external_reference_map(self, import_id: Optional[int] = None) -> Dict[tuple[str, str, str], Dict[str, Any]]:
        latest = self.get_latest_reference_import_run() if import_id is None else {"import_id": int(import_id)}
        effective_import_id = int(latest.get("import_id") or 0)
        if not effective_import_id:
            return {}
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    source_title,
                    raw_title,
                    reference_price,
                    imported_at,
                    matched_brand_title,
                    matched_series_title,
                    matched_model_title,
                    matched_group_title
                FROM reference_import_rows
                WHERE import_id = ?
                  AND TRIM(COALESCE(matched_model_title, '')) <> ''
                ORDER BY imported_at DESC, id DESC
                """,
                (effective_import_id,),
            ).fetchall()

        grouped: Dict[tuple[str, str, str], List[Dict[str, Any]]] = {}
        for row in rows:
            key = (
                str(row["matched_brand_title"] or "").strip(),
                str(row["matched_series_title"] or "").strip(),
                sku_title_from_parts(
                    str(row["matched_model_title"] or "").strip(),
                    str(row["matched_group_title"] or "").strip(),
                ),
            )
            grouped.setdefault(key, []).append({key_name: row[key_name] for key_name in row.keys()})

        mapping: Dict[tuple[str, str, str], Dict[str, Any]] = {}
        for key, group_rows in grouped.items():
            prices = [int(item["reference_price"]) for item in group_rows if item["reference_price"] is not None]
            if not prices:
                continue
            mapping[key] = {
                "reference_price": percentile(prices, 0.5),
                "source_title": str(group_rows[0]["source_title"] or ""),
                "fetched_at": str(group_rows[0]["imported_at"] or ""),
                "source_name": "external_import",
            }
        return mapping

    def list_external_reference_candidates(self, import_id: Optional[int] = None) -> List[Dict[str, Any]]:
        latest = self.get_latest_reference_import_run() if import_id is None else {"import_id": int(import_id)}
        effective_import_id = int(latest.get("import_id") or 0)
        if not effective_import_id:
            return []
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    normalized_title,
                    raw_title,
                    source_title,
                    reference_price,
                    imported_at
                FROM reference_import_rows
                WHERE import_id = ?
                  AND TRIM(COALESCE(matched_model_title, '')) = ''
                ORDER BY imported_at DESC, id DESC
                """,
                (effective_import_id,),
            ).fetchall()

        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            normalized_title = str(row["normalized_title"] or "").strip()
            if not normalized_title:
                continue
            grouped.setdefault(normalized_title, []).append({key: row[key] for key in row.keys()})

        candidates: List[Dict[str, Any]] = []
        for normalized_title, group_rows in grouped.items():
            prices = [int(item["reference_price"]) for item in group_rows if item["reference_price"] is not None]
            if not prices:
                continue
            raw_title = str(group_rows[0]["raw_title"] or "").strip()
            source_title = str(group_rows[0]["source_title"] or "").strip()
            latest_imported_at = str(group_rows[0]["imported_at"] or "")
            search_text = " ".join(part for part in (raw_title, source_title, "External Reference") if part)
            candidates.append(
                {
                    "run_key": "",
                    "brand_title": "",
                    "series_title": source_title or "External Reference",
                    "model_title": raw_title,
                    "group_title": "",
                    "condition_bucket": "",
                    "row_count": len(group_rows),
                    "source_count": len(group_rows),
                    "min_price": min(prices),
                    "max_price": max(prices),
                    "latest_imported_at": latest_imported_at,
                    "latest_gprice": "",
                    "search_text": search_text,
                    "search_text_normalized": normalize_search_text(search_text),
                    "model_group_normalized": normalize_search_text(raw_title),
                    "data_source": "external_reference",
                    "external_key": normalized_title,
                    "external_title": raw_title,
                    "external_source_title": source_title,
                }
            )
        candidates.sort(key=lambda item: (-int(item["row_count"]), item["external_title"]))
        return candidates

    def get_external_reference_rows(self, external_key: str, import_id: Optional[int] = None) -> List[Dict[str, Any]]:
        latest = self.get_latest_reference_import_run() if import_id is None else {"import_id": int(import_id)}
        effective_import_id = int(latest.get("import_id") or 0)
        if not effective_import_id:
            return []
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    import_id, image_name, source_title, raw_title, normalized_title,
                    reference_price, ocr_confidence, imported_at, payload_json
                FROM reference_import_rows
                WHERE import_id = ?
                  AND normalized_title = ?
                  AND TRIM(COALESCE(matched_model_title, '')) = ''
                ORDER BY imported_at DESC, id DESC
                """,
                (effective_import_id, str(external_key or "").strip()),
            ).fetchall()
        return [{key: row[key] for key in row.keys()} for row in rows]

    def append_event(self, run_key: str, event_type: str, payload: Mapping[str, Any], *, event_key: str = "") -> int:
        event_time = str(payload.get("time") or time.strftime("%Y-%m-%d %H:%M:%S"))
        details_json = payload_json_from_task(payload)
        normalized_event_key = str(event_key or "").strip()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO run_events (
                    run_key, event_key, event_time, event_type, task_key,
                    brand_title, series_title, model_title, city_title, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_key, event_key) DO UPDATE SET
                    event_time = excluded.event_time,
                    brand_title = excluded.brand_title,
                    series_title = excluded.series_title,
                    model_title = excluded.model_title,
                    city_title = excluded.city_title,
                    details_json = excluded.details_json
                """,
                (
                    run_key,
                    normalized_event_key or None,
                    event_time,
                    str(event_type or "").strip(),
                    str(payload.get("task_key") or payload.get("key") or "").strip(),
                    str(payload.get("brand_title") or "").strip(),
                    str(payload.get("series_title") or "").strip(),
                    str(payload.get("model_title") or "").strip(),
                    str(payload.get("city_title") or "").strip(),
                    details_json,
                ),
            )
        return 1

    def upsert_run(self, run_key: str, source_dir: Path, summary_json: str, *, status: str = "", started_at: str = "", finished_at: str = "") -> None:
        storage_write.upsert_run(
            self,
            run_key,
            source_dir,
            summary_json,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
        )

    def upsert_task(self, run_key: str, task: Mapping[str, Any], payload_json: str) -> None:
        storage_write.upsert_task(self, run_key, task, payload_json)

    def ensure_run_tasks(self, run_key: str, tasks: Iterable[Mapping[str, Any]]) -> int:
        return storage_write.ensure_run_tasks(self, run_key, tasks)

    def requeue_running_tasks(self, run_key: str) -> int:
        return storage_write.requeue_running_tasks(self, run_key)

    def insert_quote_rows(self, run_key: str, rows: Iterable[Mapping[str, Any]]) -> int:
        return storage_write.insert_quote_rows(self, run_key, rows)

    def replace_quote_rows_for_task(self, run_key: str, task_key: str, rows: Iterable[Mapping[str, Any]]) -> int:
        return storage_write.replace_quote_rows_for_task(self, run_key, task_key, rows)

    def reclassify_quote_rows(
        self,
        rules: Mapping[str, Any],
        *,
        run_key: Optional[str] = None,
        batch_size: int = 1000,
    ) -> Dict[str, Any]:
        from .rules import classify_row

        effective_run_key = str(run_key or "").strip()
        fetch_size = max(int(batch_size or 1000), 1)
        where_sql = "WHERE run_key = ?" if effective_run_key else ""
        select_sql = f"""
            SELECT
                id,
                run_key,
                brand_title,
                series_title,
                model_title,
                COALESCE(group_title, '') AS group_title,
                COALESCE(activation, '') AS activation,
                COALESCE(dstatus, '') AS dstatus,
                COALESCE(clean_scope, '') AS clean_scope,
                COALESCE(condition_bucket, '') AS condition_bucket,
                COALESCE(is_target_price, 0) AS is_target_price,
                COALESCE(needs_review, 0) AS needs_review,
                COALESCE(matched_positive_tags, '') AS matched_positive_tags,
                COALESCE(matched_negative_tags, '') AS matched_negative_tags,
                COALESCE(matched_sale_tags, '') AS matched_sale_tags,
                COALESCE(exclude_reason, '') AS exclude_reason,
                COALESCE(rule_note, '') AS rule_note
            FROM quote_rows
            {where_sql}
            ORDER BY id ASC
        """
        params: tuple[Any, ...] = (effective_run_key,) if effective_run_key else ()
        update_sql = """
            UPDATE quote_rows
            SET
                clean_scope = ?,
                condition_bucket = ?,
                is_target_price = ?,
                needs_review = ?,
                matched_positive_tags = ?,
                matched_negative_tags = ?,
                matched_sale_tags = ?,
                exclude_reason = ?,
                rule_note = ?
            WHERE id = ?
        """
        scanned = 0
        updated = 0
        per_run: Dict[str, Dict[str, int]] = {}

        with self.connect() as conn:
            cursor = conn.execute(select_sql, params)
            while True:
                chunk = cursor.fetchmany(fetch_size)
                if not chunk:
                    break
                update_batch: List[tuple[Any, ...]] = []
                for row in chunk:
                    row_run_key = str(row["run_key"] or "")
                    stats = per_run.setdefault(row_run_key, {"scanned": 0, "updated": 0})
                    stats["scanned"] += 1
                    scanned += 1

                    derived = classify_row({key: row[key] for key in row.keys()}, rules)
                    current_fields = {
                        "clean_scope": str(row["clean_scope"] or ""),
                        "condition_bucket": str(row["condition_bucket"] or ""),
                        "is_target_price": "1" if int(row["is_target_price"] or 0) else "0",
                        "needs_review": "1" if int(row["needs_review"] or 0) else "0",
                        "matched_positive_tags": str(row["matched_positive_tags"] or ""),
                        "matched_negative_tags": str(row["matched_negative_tags"] or ""),
                        "matched_sale_tags": str(row["matched_sale_tags"] or ""),
                        "exclude_reason": str(row["exclude_reason"] or ""),
                        "rule_note": str(row["rule_note"] or ""),
                    }
                    if current_fields == derived:
                        continue

                    update_batch.append(
                        (
                            str(derived.get("clean_scope") or ""),
                            str(derived.get("condition_bucket") or ""),
                            int(derived.get("is_target_price") or 0),
                            int(derived.get("needs_review") or 0),
                            str(derived.get("matched_positive_tags") or ""),
                            str(derived.get("matched_negative_tags") or ""),
                            str(derived.get("matched_sale_tags") or ""),
                            str(derived.get("exclude_reason") or ""),
                            str(derived.get("rule_note") or ""),
                            int(row["id"]),
                        )
                    )
                    stats["updated"] += 1
                    updated += 1
                if update_batch:
                    conn.executemany(update_sql, update_batch)

        event_time = time.strftime("%Y-%m-%d %H:%M:%S")
        touched_runs: List[Dict[str, Any]] = []
        for row_run_key in sorted(per_run):
            stats = per_run[row_run_key]
            touched_runs.append(
                {
                    "run_key": row_run_key,
                    "scanned_rows": int(stats["scanned"]),
                    "updated_rows": int(stats["updated"]),
                }
            )
            self.append_event(
                row_run_key,
                "maintenance.reclassify_quote_rows",
                {
                    "time": event_time,
                    "run_key": row_run_key,
                    "scanned_rows": int(stats["scanned"]),
                    "updated_rows": int(stats["updated"]),
                    "rules_scope": "current_rules",
                },
                event_key=f"maintenance.reclassify_quote_rows:{event_time}",
            )

        return {
            "scope": effective_run_key or "all_runs",
            "scanned_rows": scanned,
            "updated_rows": updated,
            "touched_run_count": len(touched_runs),
            "runs": touched_runs,
            "event_time": event_time,
        }

    def import_blacklist_csv(self, blacklist_path: Path) -> int:
        path = Path(blacklist_path).expanduser().resolve()
        count = 0
        imported_at = time.strftime("%Y-%m-%d %H:%M:%S")
        with path.open("r", encoding="utf-8-sig", newline="") as fh, self.connect() as conn:
            reader = csv.DictReader(fh)
            for row in reader:
                normalized = normalize_blacklist_row(row)
                model_id = normalized["model_id"].strip()
                if not model_id:
                    continue
                conn.execute(
                    """
                    INSERT INTO blacklist_models (model_id, source, enabled, note, imported_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(model_id) DO UPDATE SET
                        source = excluded.source,
                        enabled = excluded.enabled,
                        note = excluded.note,
                        imported_at = excluded.imported_at
                    """,
                    (
                        model_id,
                        str(path),
                        1 if normalized["enabled"].strip() not in {"0", "false", "False"} else 0,
                        normalized["note"].strip(),
                        imported_at,
                    ),
                )
                count += 1
        return count

    def get_market_snapshot_count(self, run_key: Optional[str] = None) -> int:
        return storage_publish.get_market_snapshot_count(self, run_key)

    def get_live_market_run_key(self) -> str:
        return storage_publish.get_live_market_run_key(self)

    def publish_market_snapshots(
        self,
        run_key: str,
        rows: Iterable[Mapping[str, Any]],
        *,
        summary: Optional[Mapping[str, Any]] = None,
        published_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        return storage_publish.publish_market_snapshots(
            self,
            run_key,
            rows,
            summary=summary,
            published_at=published_at,
        )

    def list_market_snapshot_candidates(self, run_key: Optional[str] = None) -> List[Dict[str, Any]]:
        return storage_publish.list_market_snapshot_candidates(self, run_key)

    def list_live_sku_candidates(self, run_key: Optional[str] = None) -> List[Dict[str, Any]]:
        return storage_publish.list_live_sku_candidates(self, run_key)

    def get_market_snapshot_row(
        self,
        *,
        brand_title: str,
        series_title: str,
        model_title: str,
        group_title: str = "",
        condition_bucket: str = "",
        run_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        return storage_publish.get_market_snapshot_row(
            self,
            brand_title=brand_title,
            series_title=series_title,
            model_title=model_title,
            group_title=group_title,
            condition_bucket=condition_bucket,
            run_key=run_key,
        )

    def get_task_count(self, run_key: Optional[str] = None) -> int:
        with self.connect() as conn:
            if run_key:
                return int(conn.execute("SELECT COUNT(*) FROM tasks WHERE run_key = ?", (run_key,)).fetchone()[0])
            return int(conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])

    def get_task_keys_by_status(self, run_key: str, statuses: Iterable[str]) -> List[str]:
        status_list = [str(status) for status in statuses if str(status)]
        if not status_list:
            return []
        placeholders = ",".join("?" for _ in status_list)
        params: List[Any] = [run_key, *status_list]
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT task_key FROM tasks WHERE run_key = ? AND status IN ({placeholders}) ORDER BY task_key",
                tuple(params),
            ).fetchall()
        return [str(row["task_key"]) for row in rows if row["task_key"]]

    def get_task_payloads(self, run_key: str, statuses: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
        params: List[Any] = [run_key]
        sql = """
            SELECT
                rowid,
                task_key,
                event_time,
                status,
                code,
                msg,
                error_text,
                total_rows_seen,
                row_count,
                drop_by_date,
                drop_out_of_stock,
                drop_by_apple_dstatus,
                drop_by_non_apple_dstatus,
                drop_invalid_price,
                brand_title,
                series_title,
                model_title,
                city_title,
                payload_json
            FROM tasks
            WHERE run_key = ?
        """
        status_list = [str(status) for status in (statuses or []) if str(status)]
        if status_list:
            placeholders = ",".join("?" for _ in status_list)
            sql += f" AND status IN ({placeholders})"
            params.extend(status_list)
        sql += " ORDER BY rowid ASC"
        payloads: List[Dict[str, Any]] = []
        with self.connect() as conn:
            for row in conn.execute(sql, tuple(params)):
                payload: Dict[str, Any] = {}
                raw_payload = row["payload_json"] or ""
                if raw_payload:
                    try:
                        parsed = json.loads(raw_payload)
                        if isinstance(parsed, dict):
                            payload = parsed
                    except Exception:
                        payload = {}
                if not payload:
                    payload = {
                        "key": row["task_key"],
                        "brand_title": row["brand_title"] or "",
                        "series_title": row["series_title"] or "",
                        "model_title": row["model_title"] or "",
                        "city_title": row["city_title"] or "",
                    }
                payload["key"] = payload.get("key") or row["task_key"]
                payload["status"] = row["status"] or payload.get("status", "")
                payload["time"] = payload.get("time") or row["event_time"] or ""
                payload["code"] = payload.get("code") or row["code"] or ""
                payload["msg"] = payload.get("msg") or row["msg"] or ""
                payload["error"] = payload.get("error") or row["error_text"] or ""
                payload["total_rows_seen"] = int(payload.get("total_rows_seen") or row["total_rows_seen"] or 0)
                payload["row_count"] = int(payload.get("row_count") or row["row_count"] or 0)
                payload["drop_by_date"] = int(payload.get("drop_by_date") or row["drop_by_date"] or 0)
                payload["drop_out_of_stock"] = int(payload.get("drop_out_of_stock") or row["drop_out_of_stock"] or 0)
                payload["drop_by_apple_dstatus"] = int(payload.get("drop_by_apple_dstatus") or row["drop_by_apple_dstatus"] or 0)
                payload["drop_by_non_apple_dstatus"] = int(payload.get("drop_by_non_apple_dstatus") or row["drop_by_non_apple_dstatus"] or 0)
                payload["drop_invalid_price"] = int(payload.get("drop_invalid_price") or row["drop_invalid_price"] or 0)
                payloads.append(payload)
        return payloads

    def list_events(self, run_key: str, event_types: Optional[Iterable[str]] = None, *, limit: int = 20) -> List[Dict[str, Any]]:
        params: List[Any] = [run_key]
        sql = """
            SELECT id, run_key, event_key, event_time, event_type, task_key,
                   brand_title, series_title, model_title, city_title, details_json
            FROM run_events
            WHERE run_key = ?
        """
        type_list = [str(event_type) for event_type in (event_types or []) if str(event_type)]
        if type_list:
            placeholders = ",".join("?" for _ in type_list)
            sql += f" AND event_type IN ({placeholders})"
            params.extend(type_list)
        sql += " ORDER BY id DESC"
        if limit and limit > 0:
            sql += " LIMIT ?"
            params.append(int(limit))

        rows: List[Dict[str, Any]] = []
        with self.connect() as conn:
            for row in conn.execute(sql, tuple(params)):
                payload: Dict[str, Any] = {}
                raw_details = row["details_json"] or ""
                if raw_details:
                    try:
                        parsed = json.loads(raw_details)
                        if isinstance(parsed, dict):
                            payload = parsed
                    except Exception:
                        payload = {}
                record = {
                    "id": int(row["id"]),
                    "run_key": str(row["run_key"] or ""),
                    "event_key": str(row["event_key"] or ""),
                    "time": str(row["event_time"] or ""),
                    "event_type": str(row["event_type"] or ""),
                    "task_key": str(row["task_key"] or ""),
                    "brand_title": str(row["brand_title"] or ""),
                    "series_title": str(row["series_title"] or ""),
                    "model_title": str(row["model_title"] or ""),
                    "city_title": str(row["city_title"] or ""),
                    "details": payload,
                }
                rows.append(record)
        return rows

    def bootstrap_tasks_from_progress_jsonl(self, run_key: str, progress_path: Path) -> int:
        path = Path(progress_path).expanduser().resolve()
        if not path.exists():
            return 0
        imported = 0
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except Exception:
                    continue
                self.upsert_task(run_key, payload, json.dumps(payload, ensure_ascii=False))
                imported += 1
        return imported

    def report(self, kind: str, run_key: Optional[str] = None, *, limit: int = 20) -> List[Dict[str, Any]]:
        params: List[Any] = []
        where_clause = ""
        if run_key:
            where_clause = "WHERE run_key = ?"
            params.append(run_key)

        limit_sql = ""
        if limit and limit > 0:
            limit_sql = " LIMIT ?"
            params.append(int(limit))

        with self.connect() as conn:
            if kind == "brands":
                sql = f"""
                    SELECT
                        brand_title,
                        COUNT(*) AS task_count,
                        SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) AS ok_tasks,
                        SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS error_tasks,
                        SUM(CASE WHEN status = 'exception' THEN 1 ELSE 0 END) AS exception_tasks,
                        SUM(CASE WHEN row_count > 0 THEN 1 ELSE 0 END) AS kept_task_count,
                        SUM(row_count) AS kept_rows
                    FROM tasks
                    {where_clause}
                    GROUP BY brand_title
                    ORDER BY kept_rows DESC, task_count DESC, brand_title ASC
                    {limit_sql}
                """
            elif kind == "series":
                sql = f"""
                    SELECT
                        brand_title,
                        series_title,
                        COUNT(*) AS task_count,
                        SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) AS ok_tasks,
                        SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS error_tasks,
                        SUM(CASE WHEN status = 'exception' THEN 1 ELSE 0 END) AS exception_tasks,
                        SUM(CASE WHEN row_count > 0 THEN 1 ELSE 0 END) AS kept_task_count,
                        SUM(row_count) AS kept_rows
                    FROM tasks
                    {where_clause}
                    GROUP BY brand_title, series_title
                    ORDER BY kept_rows DESC, task_count DESC, brand_title ASC, series_title ASC
                    {limit_sql}
                """
            elif kind == "codes":
                sql = f"""
                    SELECT
                        status,
                        code,
                        msg,
                        COUNT(*) AS task_count,
                        SUM(row_count) AS kept_rows,
                        MIN(model_title) AS example_model
                    FROM tasks
                    {where_clause}
                    GROUP BY status, code, msg
                    ORDER BY task_count DESC, kept_rows DESC, status ASC, code ASC, msg ASC
                    {limit_sql}
                """
            elif kind == "condition_buckets":
                sql = f"""
                    SELECT
                        condition_bucket,
                        COUNT(*) AS quote_count,
                        SUM(CASE WHEN is_target_price = 1 THEN 1 ELSE 0 END) AS target_quote_count,
                        SUM(CASE WHEN needs_review = 1 THEN 1 ELSE 0 END) AS review_quote_count,
                        MIN(model_title) AS example_model
                    FROM quote_rows
                    {where_clause}
                    GROUP BY condition_bucket
                    ORDER BY quote_count DESC, condition_bucket ASC
                    {limit_sql}
                """
            elif kind == "gprice_labels":
                sql = f"""
                    SELECT
                        CASE
                            WHEN gprice IS NULL OR gprice = '' THEN ''
                            WHEN length(gprice) >= 5 THEN substr(gprice, 1, 5)
                            ELSE gprice
                        END AS gprice_label,
                        COUNT(*) AS quote_count,
                        SUM(CASE WHEN is_target_price = 1 THEN 1 ELSE 0 END) AS target_quote_count,
                        MIN(model_title) AS example_model
                    FROM quote_rows
                    {where_clause}
                    GROUP BY gprice_label
                    ORDER BY quote_count DESC, gprice_label DESC
                    {limit_sql}
                """
            elif kind == "brand_condition_buckets":
                sql = f"""
                    SELECT
                        brand_title,
                        condition_bucket,
                        COUNT(*) AS quote_count,
                        SUM(CASE WHEN is_target_price = 1 THEN 1 ELSE 0 END) AS target_quote_count,
                        SUM(CASE WHEN needs_review = 1 THEN 1 ELSE 0 END) AS review_quote_count,
                        MIN(model_title) AS example_model
                    FROM quote_rows
                    {where_clause}
                    GROUP BY brand_title, condition_bucket
                    ORDER BY quote_count DESC, brand_title ASC, condition_bucket ASC
                    {limit_sql}
                """
            elif kind == "brand_gprice_labels":
                sql = f"""
                    SELECT
                        brand_title,
                        CASE
                            WHEN gprice IS NULL OR gprice = '' THEN ''
                            WHEN length(gprice) >= 5 THEN substr(gprice, 1, 5)
                            ELSE gprice
                        END AS gprice_label,
                        COUNT(*) AS quote_count,
                        SUM(CASE WHEN is_target_price = 1 THEN 1 ELSE 0 END) AS target_quote_count,
                        MIN(model_title) AS example_model
                    FROM quote_rows
                    {where_clause}
                    GROUP BY brand_title, gprice_label
                    ORDER BY quote_count DESC, brand_title ASC, gprice_label DESC
                    {limit_sql}
                """
            elif kind == "code_brands":
                sql = f"""
                    SELECT
                        status,
                        code,
                        brand_title,
                        COUNT(*) AS task_count,
                        SUM(row_count) AS kept_rows,
                        MIN(model_title) AS example_model
                    FROM tasks
                    {where_clause}
                    GROUP BY status, code, brand_title
                    ORDER BY task_count DESC, kept_rows DESC, status ASC, code ASC, brand_title ASC
                    {limit_sql}
                """
            elif kind == "error_brands":
                sql = f"""
                    SELECT
                        brand_title,
                        code,
                        COUNT(*) AS task_count,
                        SUM(CASE WHEN row_count > 0 THEN 1 ELSE 0 END) AS kept_task_count,
                        MIN(model_title) AS example_model
                    FROM tasks
                    {where_clause}
                    GROUP BY brand_title, code
                    ORDER BY task_count DESC, brand_title ASC, code ASC
                    {limit_sql}
                """
            else:
                raise ValueError(f"Unsupported report kind: {kind}")

            rows = conn.execute(sql, tuple(params)).fetchall()

        return [{key: row[key] for key in row.keys()} for row in rows]

    def build_blacklist_candidates(
        self,
        run_key: str,
        *,
        code: str = "3",
        statuses: Optional[Iterable[str]] = None,
        source_batch: str = "",
    ) -> List[Dict[str, str]]:
        desired_statuses = list(statuses or ("error",))
        payloads = self.get_task_payloads(run_key, desired_statuses)
        batch_label = source_batch or extract_run_date_from_run_key(run_key)
        candidates: List[Dict[str, str]] = []
        seen_model_ids: set[str] = set()

        for payload in payloads:
            if str(payload.get("code") or "") != str(code):
                continue
            model_id = str(payload.get("model_id") or "").strip()
            if not model_id or model_id in seen_model_ids:
                continue
            seen_model_ids.add(model_id)
            status = str(payload.get("status") or "").strip()
            msg = str(payload.get("msg") or payload.get("error") or "").strip()
            candidates.append(
                {
                    "enabled": "1",
                    "model_id": model_id,
                    "brand_title": str(payload.get("brand_title") or "").strip(),
                    "series_title": str(payload.get("series_title") or "").strip(),
                    "model_title": str(payload.get("model_title") or "").strip(),
                    "reason": f"final_code_{code}_from_sqlite",
                    "source_batch": batch_label,
                    "note": f"status={status}; msg={msg}".strip(),
                }
            )

        candidates.sort(key=lambda row: (row["brand_title"], row["series_title"], row["model_title"], row["model_id"]))
        return candidates

    def summary(self, run_key: Optional[str] = None) -> Dict[str, Any]:
        with self.connect() as conn:
            params: tuple[Any, ...] = ()
            task_where = ""
            quote_where = ""
            run_row = None
            if run_key:
                task_where = "WHERE run_key = ?"
                quote_where = "WHERE run_key = ?"
                params = (run_key,)
                run_row = conn.execute(
                    """
                    SELECT run_key, source_dir, started_at, finished_at, status, summary_json, created_at
                    FROM runs
                    WHERE run_key = ?
                    """,
                    params,
                ).fetchone()

            run_count = conn.execute(
                "SELECT COUNT(*) FROM runs" + (" WHERE run_key = ?" if run_key else ""),
                params,
            ).fetchone()[0]
            task_count = conn.execute(f"SELECT COUNT(*) FROM tasks {task_where}", params).fetchone()[0]
            quote_count = conn.execute(f"SELECT COUNT(*) FROM quote_rows {quote_where}", params).fetchone()[0]
            target_count = conn.execute(
                f"SELECT COUNT(*) FROM quote_rows {quote_where}{' AND ' if quote_where else ' WHERE '}is_target_price = 1",
                params,
            ).fetchone()[0]
            review_count = conn.execute(
                f"SELECT COUNT(*) FROM quote_rows {quote_where}{' AND ' if quote_where else ' WHERE '}needs_review = 1",
                params,
            ).fetchone()[0]
            top_status_rows = conn.execute(
                f"SELECT status, COUNT(*) AS c FROM tasks {task_where} GROUP BY status ORDER BY c DESC",
                params,
            ).fetchall()
            top_brand_rows = conn.execute(
                f"SELECT brand_title, COUNT(*) AS c FROM quote_rows {quote_where} GROUP BY brand_title ORDER BY c DESC LIMIT 10",
                params,
            ).fetchall()
            api_code_rows = conn.execute(
                f"SELECT code, COUNT(*) AS c FROM tasks {task_where}{' AND ' if task_where else ' WHERE '}code <> '' GROUP BY code ORDER BY c DESC, code ASC",
                params,
            ).fetchall()
            task_row_totals = conn.execute(
                f"""
                SELECT
                    COALESCE(SUM(total_rows_seen), 0) AS total_rows_seen,
                    COALESCE(SUM(row_count), 0) AS kept_rows,
                    COALESCE(SUM(drop_by_date), 0) AS drop_by_date,
                    COALESCE(SUM(drop_out_of_stock), 0) AS drop_out_of_stock,
                    COALESCE(SUM(drop_by_apple_dstatus), 0) AS drop_by_apple_dstatus,
                    COALESCE(SUM(drop_by_non_apple_dstatus), 0) AS drop_by_non_apple_dstatus,
                    COALESCE(SUM(drop_invalid_price), 0) AS drop_invalid_price
                FROM tasks
                {task_where}
                """,
                params,
            ).fetchone()
            top_condition_rows = conn.execute(
                f"""
                SELECT condition_bucket, COUNT(*) AS c
                FROM quote_rows
                {quote_where}
                GROUP BY condition_bucket
                ORDER BY c DESC, condition_bucket ASC
                LIMIT 10
                """,
                params,
            ).fetchall()
            top_gprice_rows = conn.execute(
                f"""
                SELECT
                    CASE
                        WHEN gprice IS NULL OR gprice = '' THEN ''
                        WHEN length(gprice) >= 5 THEN substr(gprice, 1, 5)
                        ELSE gprice
                    END AS gprice_label,
                    COUNT(*) AS c
                FROM quote_rows
                {quote_where}
                GROUP BY gprice_label
                ORDER BY c DESC, gprice_label DESC
                LIMIT 10
                """,
                params,
            ).fetchall()
            event_rows = conn.execute(
                f"""
                SELECT event_type, COUNT(*) AS c
                FROM run_events
                {'WHERE run_key = ?' if run_key else ''}
                GROUP BY event_type
                ORDER BY c DESC, event_type ASC
                """,
                params,
            ).fetchall()

        stored_summary: Dict[str, Any] = {}
        if run_row and run_row["summary_json"]:
            try:
                parsed = json.loads(run_row["summary_json"])
                if isinstance(parsed, dict):
                    stored_summary = parsed
            except Exception:
                stored_summary = {}

        return {
            "run_key": run_key or "",
            "run_count": run_count,
            "run": {
                "source_dir": str(run_row["source_dir"]) if run_row else "",
                "started_at": str(run_row["started_at"] or "") if run_row else "",
                "finished_at": str(run_row["finished_at"] or "") if run_row else "",
                "status": str(run_row["status"] or "") if run_row else "",
                "created_at": str(run_row["created_at"] or "") if run_row else "",
            },
            "task_count": task_count,
            "quote_count": quote_count,
            "target_quote_count": target_count,
            "review_quote_count": review_count,
            "task_status_counts": {str(row["status"] or ""): int(row["c"]) for row in top_status_rows},
            "api_code_counts": {str(row["code"] or ""): int(row["c"]) for row in api_code_rows},
            "task_row_totals": {
                "total_rows_seen": int(task_row_totals["total_rows_seen"] or 0),
                "kept_rows": int(task_row_totals["kept_rows"] or 0),
                "drop_by_date": int(task_row_totals["drop_by_date"] or 0),
                "drop_out_of_stock": int(task_row_totals["drop_out_of_stock"] or 0),
                "drop_by_apple_dstatus": int(task_row_totals["drop_by_apple_dstatus"] or 0),
                "drop_by_non_apple_dstatus": int(task_row_totals["drop_by_non_apple_dstatus"] or 0),
                "drop_invalid_price": int(task_row_totals["drop_invalid_price"] or 0),
            },
            "event_type_counts": {str(row["event_type"] or ""): int(row["c"]) for row in event_rows},
            "top_brands": {str(row["brand_title"] or ""): int(row["c"]) for row in top_brand_rows},
            "top_condition_buckets": {str(row["condition_bucket"] or ""): int(row["c"]) for row in top_condition_rows},
            "top_gprice_labels": {str(row["gprice_label"] or ""): int(row["c"]) for row in top_gprice_rows},
            "stored_summary_available": bool(stored_summary),
            "stored_summary_snapshot": {
                "integrations": stored_summary.get("integrations", {}),
                "task_counts": stored_summary.get("task_counts", {}),
                "row_counts": stored_summary.get("row_counts", {}),
                "current": stored_summary.get("current", {}),
                "last_error": stored_summary.get("last_error", {}),
            },
        }

    def get_preferred_run_key(self) -> str:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT runs.run_key
                FROM runs
                LEFT JOIN (
                    SELECT run_key, COUNT(*) AS quote_count, MAX(imported_at) AS latest_imported_at
                    FROM quote_rows
                    GROUP BY run_key
                ) quote_stats ON quote_stats.run_key = runs.run_key
                ORDER BY
                    CASE WHEN COALESCE(quote_stats.quote_count, 0) > 0 THEN 0 ELSE 1 END ASC,
                    COALESCE(quote_stats.latest_imported_at, runs.started_at, runs.finished_at, runs.created_at) DESC,
                    CASE WHEN runs.status = 'completed' THEN 0 WHEN runs.status = 'running' THEN 1 ELSE 2 END ASC,
                    runs.created_at DESC
                LIMIT 1
                """
            ).fetchone()
        return str(row["run_key"] or "") if row else ""

    def get_live_marker(self, run_key: Optional[str] = None) -> Dict[str, Any]:
        return storage_query.get_live_marker(self, run_key)

    def list_sku_candidates(self, run_key: Optional[str] = None) -> List[Dict[str, Any]]:
        return storage_query.list_sku_candidates(self, run_key)

    def get_sku_rows(
        self,
        *,
        brand_title: str,
        series_title: str,
        model_title: str,
        group_title: str = "",
        condition_bucket: str = "",
        run_key: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return storage_query.get_sku_rows(
            self,
            brand_title=brand_title,
            series_title=series_title,
            model_title=model_title,
            group_title=group_title,
            condition_bucket=condition_bucket,
            run_key=run_key,
        )

    def get_run_quote_rows(self, run_key: Optional[str] = None) -> List[Dict[str, Any]]:
        return storage_query.get_run_quote_rows(self, run_key)

    def get_model_family_rows(
        self,
        *,
        brand_title: str,
        series_title: str,
        model_title: str,
        condition_bucket: str = "",
        run_key: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return storage_query.get_model_family_rows(
            self,
            brand_title=brand_title,
            series_title=series_title,
            model_title=model_title,
            condition_bucket=condition_bucket,
            run_key=run_key,
        )

