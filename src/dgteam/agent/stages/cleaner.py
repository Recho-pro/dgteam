from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from dgteam.core.project_config import load_project_config
from dgteam.core.storage import DGTeamStorage
from dgteam.market.price_cleaning import dedupe_latest_rows, numeric_rows, select_recent_sample_window


@dataclass(frozen=True)
class CleanerStageResult:
    name: str
    status: str
    run_key: str
    summary: str
    details: Dict[str, Any]


def run_cleaner(*, run_key: str, project_root: Path | None = None) -> CleanerStageResult:
    config = load_project_config(project_root=project_root)
    storage = DGTeamStorage(config.paths.db_path)
    storage.init_db()
    rows = storage.get_run_quote_rows(run_key)
    deduped_rows = dedupe_latest_rows(rows)
    recent_rows, labels = select_recent_sample_window(numeric_rows(deduped_rows), min_samples=10, max_labels=3)
    brand_counts = Counter(str(row.get("brand_title") or "").strip() for row in deduped_rows if str(row.get("brand_title") or "").strip())
    target_rows = [row for row in deduped_rows if int(row.get("is_target_price") or 0) == 1]
    summary = f"Cleaner prepared {len(recent_rows)} recent rows from {len(deduped_rows)} deduped rows."
    return CleanerStageResult(
        name="cleaner",
        status="completed",
        run_key=run_key,
        summary=summary,
        details={
            "source_row_count": len(rows),
            "deduped_row_count": len(deduped_rows),
            "recent_row_count": len(recent_rows),
            "recent_labels": list(labels or []),
            "target_row_count": len(target_rows),
            "top_brands": dict(brand_counts.most_common(10)),
        },
    )
