from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Sequence

from dgteam.agent import crawler
from dgteam.core.project_config import load_project_config
from dgteam.core.storage import DGTeamStorage


@dataclass(frozen=True)
class CollectorStageResult:
    name: str
    status: str
    run_key: str
    summary: str
    details: Dict[str, Any]


def run_collector(*, project_root: Path | None = None, crawler_args: Sequence[str] = ()) -> CollectorStageResult:
    config = load_project_config(project_root=project_root)
    storage = DGTeamStorage(config.paths.db_path)
    storage.init_db()
    before_run_key = str(storage.get_preferred_run_key() or "").strip()

    forwarded = ["dgteam-crawler", *list(crawler_args or [])]
    previous_argv = list(sys.argv)
    try:
        sys.argv = forwarded
        crawler.main()
    finally:
        sys.argv = previous_argv

    after_run_key = str(storage.get_preferred_run_key() or "").strip()
    marker = storage.get_live_marker(after_run_key)
    changed = bool(after_run_key and after_run_key != before_run_key)
    status = "completed" if after_run_key else "empty"
    summary = (
        f"Collector finished with run {after_run_key}."
        if after_run_key
        else "Collector finished but did not produce a readable run."
    )
    return CollectorStageResult(
        name="collector",
        status=status,
        run_key=after_run_key,
        summary=summary,
        details={
            "before_run_key": before_run_key,
            "after_run_key": after_run_key,
            "changed": changed,
            "crawler_args": list(crawler_args or []),
            "marker": marker,
        },
    )
