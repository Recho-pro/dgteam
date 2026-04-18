from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from dgteam.core.config import load_settings
from dgteam.core.encoding_guard import assert_project_encoding_clean
from dgteam.core.project_config import load_project_config
from dgteam.core.storage import DGTeamStorage
from dgteam.release.builder import archive_release_bundle, build_local_release_bundle
from dgteam.release.retention import prune_local_runtime


@dataclass(frozen=True)
class PublisherStageResult:
    name: str
    status: str
    run_key: str
    summary: str
    details: Dict[str, Any]


def run_publisher(
    *,
    run_key: str,
    release_id: str = "",
    project_root: Path | None = None,
) -> PublisherStageResult:
    config = load_project_config(project_root=project_root)
    settings = load_settings(project_root=config.paths.project_root)
    storage = DGTeamStorage(config.paths.db_path)
    storage.init_db()
    assert_project_encoding_clean(config.paths.project_root)
    resolved_release_id = str(release_id or "").strip()
    if not resolved_release_id:
        from datetime import datetime

        resolved_release_id = f"release_{datetime.now().strftime('%Y-%m-%dT%H-%M-%S')}"
    release_dir = settings.agent.releases_dir / resolved_release_id
    bundle = build_local_release_bundle(storage, release_dir, run_key=run_key)
    archive: Dict[str, Any]
    should_create_archive = not settings.retention.enabled or settings.retention.keep_local_release_archives > 0
    if should_create_archive:
        archive = archive_release_bundle(Path(bundle["release_dir"]))
        archive["created"] = True
    else:
        archive = {
            "created": False,
            "reason": "skipped_by_retention",
            "release_dir": str(bundle["release_dir"]),
            "archive_path": "",
            "size": 0,
        }
    retention_summary: Dict[str, Any] = {}
    if settings.retention.enabled:
        retention_summary = prune_local_runtime(
            releases_dir=settings.agent.releases_dir,
            integration_smoke_dir=settings.local_root / "integration_smoke",
            keep_release_dirs=settings.retention.keep_local_releases,
            keep_release_archives=settings.retention.keep_local_release_archives,
            keep_integration_smoke_runs=settings.retention.keep_integration_smoke_runs,
        )
    summary = f"Publisher created release {bundle['release_id']} for run {bundle['run_key']}."
    if not archive.get("created"):
        summary += " Archive export was skipped by retention settings."
    if retention_summary:
        summary += f" Local retention reclaimed {retention_summary.get('reclaimed_gb', 0)} GB."
    return PublisherStageResult(
        name="publisher",
        status="completed",
        run_key=str(bundle.get("run_key") or run_key),
        summary=summary,
        details={
            "release_id": str(bundle.get("release_id") or resolved_release_id),
            "release_dir": str(bundle.get("release_dir") or release_dir),
            "archive": archive,
            "manifest": dict(bundle.get("manifest") or {}),
            "release": dict(bundle.get("release") or {}),
            "retention": retention_summary,
        },
    )
