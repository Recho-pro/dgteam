from __future__ import annotations

import csv
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from dgteam.market.rules import classify_row

from .quote_models import ImportResult
from .storage import DGTeamStorage
from .textio import read_json_utf8, read_jsonl_utf8


def _load_json(path: Path) -> Dict[str, Any]:
    return read_json_utf8(path) if path.exists() else {}


def import_run_directory(
    storage: DGTeamStorage,
    source_dir: Path,
    rules: Dict[str, Any],
    *,
    run_key: Optional[str] = None,
) -> ImportResult:
    base = Path(source_dir).expanduser().resolve()
    resolved_run_key = run_key or f"{base.name}_{time.strftime('%Y%m%d_%H%M%S')}"
    summary = _load_json(base / "run_summary.json")
    with storage.connect() as conn:
        conn.execute("DELETE FROM quote_rows WHERE run_key = ?", (resolved_run_key,))
        conn.execute("DELETE FROM run_events WHERE run_key = ?", (resolved_run_key,))
        conn.execute("DELETE FROM tasks WHERE run_key = ?", (resolved_run_key,))
        conn.execute("DELETE FROM runs WHERE run_key = ?", (resolved_run_key,))
    storage.upsert_run(
        resolved_run_key,
        base,
        json.dumps(summary, ensure_ascii=False),
        status=str(summary.get("status", "")),
        started_at=str(summary.get("started_at", "")),
        finished_at=str(summary.get("finished_at", "")),
    )

    result = ImportResult(run_key=resolved_run_key)
    progress_path = base / "progress.jsonl"
    if progress_path.exists():
        for payload in read_jsonl_utf8(progress_path):
            storage.upsert_task(resolved_run_key, payload, json.dumps(payload, ensure_ascii=False))
            result.task_count += 1

    rows_path = base / "all_rows.csv"
    if rows_path.exists():
        enriched_rows: List[Dict[str, Any]] = []
        with rows_path.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                derived = classify_row(row, rules)
                enriched_rows.append({**row, **derived})
                if len(enriched_rows) >= 1000:
                    result.quote_row_count += storage.insert_quote_rows(resolved_run_key, enriched_rows)
                    enriched_rows.clear()
            if enriched_rows:
                result.quote_row_count += storage.insert_quote_rows(resolved_run_key, enriched_rows)

    return result
