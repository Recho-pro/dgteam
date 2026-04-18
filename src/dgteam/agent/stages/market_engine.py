from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from dgteam.core.project_config import load_project_config
from dgteam.core.storage import DGTeamStorage
from dgteam.release.live_market import build_live_market_payload


@dataclass(frozen=True)
class MarketEngineStageResult:
    name: str
    status: str
    run_key: str
    summary: str
    details: Dict[str, Any]
    payload: Dict[str, Any]


def run_market_engine(*, run_key: str, project_root: Path | None = None) -> MarketEngineStageResult:
    config = load_project_config(project_root=project_root)
    storage = DGTeamStorage(config.paths.db_path)
    storage.init_db()
    payload = build_live_market_payload(storage, run_key)
    summary = dict(payload.get("summary") or {})
    counts = dict(summary.get("counts") or {})
    return MarketEngineStageResult(
        name="market_engine",
        status="completed",
        run_key=str(payload.get("run_key") or run_key),
        summary=f"Market engine built {counts.get('published_snapshots', 0)} snapshot rows.",
        details={
            "counts": counts,
            "selection_modes": dict(summary.get("selection_modes") or {}),
            "skipped_reason_counts": dict(summary.get("skipped_reason_counts") or {}),
        },
        payload=payload,
    )
