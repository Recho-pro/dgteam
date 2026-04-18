from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Sequence

from dgteam.core.project_config import load_project_config
from dgteam.core.storage import DGTeamStorage

from .stages import run_cleaner, run_collector, run_market_engine, run_ocr_stage, run_publisher


@dataclass(frozen=True)
class PipelineStageRecord:
    name: str
    status: str
    summary: str
    details: Dict[str, Any]


@dataclass(frozen=True)
class AgentRunResult:
    run_key: str
    release_id: str
    release_dir: str
    summary: str
    stages: List[Dict[str, Any]]


def _stage_record(name: str, status: str, summary: str, details: Dict[str, Any]) -> PipelineStageRecord:
    return PipelineStageRecord(name=name, status=status, summary=summary, details=details)


def run_pipeline(
    mode: str,
    *,
    project_root: Path | None = None,
    crawler_args: Sequence[str] = (),
    run_key: str = "",
    release_id: str = "",
) -> AgentRunResult:
    config = load_project_config(project_root=project_root)
    storage = DGTeamStorage(config.paths.db_path)
    storage.init_db()

    effective_run_key = str(run_key or storage.get_preferred_run_key() or "").strip()
    stage_records: List[PipelineStageRecord] = []

    if mode == "dry-run":
        ocr_stage = run_ocr_stage(project_root=config.paths.project_root)
        stage_records.append(_stage_record(ocr_stage.name, ocr_stage.status, ocr_stage.summary, ocr_stage.details))
        summary = "DGTEAM pipeline is ready. No data was published during this dry run."
        return AgentRunResult(
            run_key=effective_run_key,
            release_id=str(release_id or ""),
            release_dir="",
            summary=summary,
            stages=[asdict(stage) for stage in stage_records],
        )

    if mode == "collect-and-publish":
        collector_stage = run_collector(project_root=config.paths.project_root, crawler_args=crawler_args)
        effective_run_key = collector_stage.run_key or effective_run_key
        stage_records.append(
            _stage_record(collector_stage.name, collector_stage.status, collector_stage.summary, collector_stage.details)
        )

    if not effective_run_key:
        raise ValueError("No completed run is available in dgteam.db. Run the crawler first or pass --run-key.")

    ocr_stage = run_ocr_stage(project_root=config.paths.project_root)
    cleaner_stage = run_cleaner(run_key=effective_run_key, project_root=config.paths.project_root)
    market_stage = run_market_engine(run_key=effective_run_key, project_root=config.paths.project_root)
    publisher_stage = run_publisher(
        run_key=effective_run_key,
        release_id=release_id,
        project_root=config.paths.project_root,
    )

    stage_records.extend(
        [
            _stage_record(ocr_stage.name, ocr_stage.status, ocr_stage.summary, ocr_stage.details),
            _stage_record(cleaner_stage.name, cleaner_stage.status, cleaner_stage.summary, cleaner_stage.details),
            _stage_record(market_stage.name, market_stage.status, market_stage.summary, market_stage.details),
            _stage_record(publisher_stage.name, publisher_stage.status, publisher_stage.summary, publisher_stage.details),
        ]
    )

    return AgentRunResult(
        run_key=publisher_stage.run_key,
        release_id=str(publisher_stage.details.get("release_id") or ""),
        release_dir=str(publisher_stage.details.get("release_dir") or ""),
        summary=f"DGTEAM pipeline completed in {mode} mode for run {effective_run_key}.",
        stages=[asdict(stage) for stage in stage_records],
    )
