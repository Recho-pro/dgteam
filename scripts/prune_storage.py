from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from dgteam.core.config import load_settings
from dgteam.release.retention import RemovedArtifact


@dataclass(frozen=True)
class PrunePolicy:
    keep_local_releases: int
    keep_local_release_archives: int
    keep_integration_smoke_runs: int
    keep_cloud_releases: int
    keep_cloud_rollbacks: int
    keep_cloud_deployments: int
    keep_automation_run_states: int
    keep_automation_recovery_states: int
    max_staging_age_hours: float
    max_upload_age_hours: float
    keep_cloud_uploads: bool


def path_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return int(path.stat().st_size)
        except OSError:
            return 0
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            try:
                total += int(item.stat().st_size)
            except OSError:
                continue
    return total


def _safe_mtime(path: Path) -> float:
    try:
        return float(path.stat().st_mtime)
    except OSError:
        return 0.0


def _age_hours(path: Path, *, now: datetime) -> float | None:
    if not path.exists():
        return None
    try:
        modified = datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return None
    delta = now - modified
    return round(max(0.0, delta.total_seconds()) / 3600.0, 3)


def _removed_artifact(path: Path, *, reason: str) -> RemovedArtifact:
    return RemovedArtifact(path=str(path), size_bytes=path_size(path), reason=reason)


def _remove_path(path: Path, *, reason: str, removed: List[RemovedArtifact]) -> None:
    target = Path(path)
    if not target.exists():
        return
    artifact = _removed_artifact(target, reason=reason)
    if target.is_dir():
        shutil.rmtree(target, ignore_errors=False)
    else:
        target.unlink()
    removed.append(artifact)


def _sorted_children(parent: Path, *, predicate) -> List[Path]:
    if not parent.exists():
        return []
    children = [item for item in parent.iterdir() if predicate(item)]
    return sorted(children, key=_safe_mtime, reverse=True)


def _plan_trimmed_children(
    parent: Path,
    *,
    predicate,
    keep: int,
    reason: str,
) -> List[RemovedArtifact]:
    children = _sorted_children(parent, predicate=predicate)
    return [_removed_artifact(item, reason=reason) for item in children[max(0, int(keep)) :]]


def _plan_stale_children(
    parent: Path,
    *,
    predicate,
    max_age_hours: float,
    reason: str,
    now: datetime,
) -> List[RemovedArtifact]:
    planned: List[RemovedArtifact] = []
    if not parent.exists():
        return planned
    for item in _sorted_children(parent, predicate=predicate):
        age = _age_hours(item, now=now)
        if age is None or age <= max(0.0, float(max_age_hours)):
            continue
        planned.append(_removed_artifact(item, reason=reason))
    return planned


def _plan_automation_state(
    state_dir: Path,
    *,
    keep_run_states: int,
    keep_recovery_states: int,
) -> List[RemovedArtifact]:
    if not state_dir.exists():
        return []

    preserved: set[Path] = set()
    last_run = state_dir / "last_run.json"
    if last_run.exists():
        preserved.add(last_run)

    run_states = _sorted_children(
        state_dir,
        predicate=lambda item: item.is_file() and item.name.startswith("run_") and item.suffix.lower() == ".json",
    )
    preserved.update(run_states[: max(0, int(keep_run_states))])

    recovery_states = _sorted_children(
        state_dir,
        predicate=lambda item: item.is_file() and item.name.startswith("manual_sync_recovery_") and item.suffix.lower() == ".json",
    )
    preserved.update(recovery_states[: max(0, int(keep_recovery_states))])

    planned: List[RemovedArtifact] = []
    for item in _sorted_children(state_dir, predicate=lambda entry: entry.is_file() and entry.suffix.lower() == ".json"):
        if item in preserved:
            continue
        if item.name.startswith("run_"):
            planned.append(_removed_artifact(item, reason="automation_run_state"))
        elif item.name.startswith("manual_sync_recovery_"):
            planned.append(_removed_artifact(item, reason="automation_recovery_state"))
    return planned


def _apply_plan(plan: Iterable[RemovedArtifact]) -> List[RemovedArtifact]:
    removed: List[RemovedArtifact] = []
    for item in plan:
        _remove_path(Path(item.path), reason=item.reason, removed=removed)
    return removed


def _summary_dict(removed: Iterable[RemovedArtifact], *, scope: str) -> dict:
    removed_list = list(removed)
    total_bytes = sum(item.size_bytes for item in removed_list)
    return {
        "scope": scope,
        "removed_count": len(removed_list),
        "reclaimed_bytes": total_bytes,
        "reclaimed_gb": round(total_bytes / 1024 / 1024 / 1024, 3),
        "removed": [
            {
                "path": item.path,
                "size_mb": round(item.size_bytes / 1024 / 1024, 1),
                "reason": item.reason,
            }
            for item in removed_list
        ],
    }


def prune_legacy_migration_artifacts(*, legacy_root: Path, removed: List[RemovedArtifact]) -> None:
    targets: Iterable[tuple[Path, str]] = (
        (legacy_root / "deploy_ready.previous", "legacy_previous_deploy_bundle"),
        (legacy_root / "data" / "reference_import_test_2026-04-13", "legacy_reference_import_test"),
        (legacy_root / "data" / "reference_import_test_2026-04-13_rerun", "legacy_reference_import_test"),
        (legacy_root / "data" / "reference_import_test_2026-04-13_final", "legacy_reference_import_test"),
        (legacy_root / "data" / "reference_import_test_2026-04-13_final_v2", "legacy_reference_import_test"),
        (legacy_root / "ylt_system" / "data" / "ylt_before_reclassify_2026-04-13.db", "legacy_backup_database"),
    )
    for path, reason in targets:
        if path.exists():
            _remove_path(path, reason=reason, removed=removed)


def prune_runtime_scratch(*, runtime_root: Path, removed: List[RemovedArtifact]) -> None:
    local_root = runtime_root / "local"
    direct_targets: Iterable[tuple[Path, str]] = (
        (local_root / "tmp_image_bench", "runtime_scratch"),
        (local_root / "tmp_wechat_reply.txt", "runtime_scratch"),
        (local_root / "publish_api_smoke.log", "runtime_scratch"),
        (local_root / "publish_api_smoke.err.log", "runtime_scratch"),
        (local_root / "query_api_smoke.log", "runtime_scratch"),
        (local_root / "query_api_smoke.err.log", "runtime_scratch"),
        (local_root / "wechat_benchmark_end_to_end.json", "runtime_scratch"),
        (local_root / "wechat_benchmark_results.json", "runtime_scratch"),
        (local_root / "wechat_model_benchmark_v2.json", "runtime_scratch"),
        (local_root / "wechat_model_benchmark_v3.json", "runtime_scratch"),
        (local_root / "wechat_official_probe_small.jpg", "runtime_scratch"),
        (local_root / "data" / "custom.db", "test_artifact"),
    )
    for path, reason in direct_targets:
        if path.exists():
            _remove_path(path, reason=reason, removed=removed)

    scratch_prefixes = (
        "wechat_official_model_bench",
        "wechat_official_model_probe_",
        "wechat_official_real_probe",
        "wechat_official_test_cache",
    )
    if local_root.exists():
        for item in local_root.iterdir():
            if any(item.name.startswith(prefix) for prefix in scratch_prefixes):
                _remove_path(item, reason="runtime_scratch", removed=removed)


def _build_policy(args: argparse.Namespace, *, project_root: Path) -> PrunePolicy:
    settings = load_settings(project_root=project_root)
    return PrunePolicy(
        keep_local_releases=max(0, int(args.keep_local_releases if args.keep_local_releases is not None else settings.retention.keep_local_releases)),
        keep_local_release_archives=max(
            0,
            int(
                args.keep_local_release_archives
                if args.keep_local_release_archives is not None
                else settings.retention.keep_local_release_archives
            ),
        ),
        keep_integration_smoke_runs=max(
            0,
            int(
                args.keep_integration_smoke
                if args.keep_integration_smoke is not None
                else max(3, settings.retention.keep_integration_smoke_runs)
            ),
        ),
        keep_cloud_releases=max(
            0,
            int(args.keep_cloud_releases if args.keep_cloud_releases is not None else settings.retention.keep_cloud_releases),
        ),
        keep_cloud_rollbacks=max(
            0,
            int(args.keep_cloud_rollbacks if args.keep_cloud_rollbacks is not None else settings.retention.keep_cloud_rollbacks),
        ),
        keep_cloud_deployments=max(0, int(args.keep_cloud_deployments if args.keep_cloud_deployments is not None else 20)),
        keep_automation_run_states=max(
            0,
            int(args.keep_automation_run_states if args.keep_automation_run_states is not None else 30),
        ),
        keep_automation_recovery_states=max(
            0,
            int(args.keep_automation_recovery_states if args.keep_automation_recovery_states is not None else 10),
        ),
        max_staging_age_hours=max(0.0, float(args.max_staging_age_hours if args.max_staging_age_hours is not None else 6.0)),
        max_upload_age_hours=max(0.0, float(args.max_upload_age_hours if args.max_upload_age_hours is not None else 24.0)),
        keep_cloud_uploads=bool(
            args.keep_cloud_uploads if args.keep_cloud_uploads is not None else not settings.retention.prune_cloud_uploads
        ),
    )


def _plan_runtime_cleanup(*, project_root: Path, policy: PrunePolicy, now: datetime) -> dict[str, List[RemovedArtifact]]:
    runtime_root = project_root / "runtime"
    local_root = runtime_root / "local"
    cloud_root = runtime_root / "cloud"

    return {
        "local": (
            _plan_trimmed_children(
                local_root / "releases",
                predicate=lambda item: item.is_dir(),
                keep=policy.keep_local_releases,
                reason="local_release_dir",
            )
            + _plan_trimmed_children(
                local_root / "releases",
                predicate=lambda item: item.is_file() and item.suffix.lower() == ".zip",
                keep=policy.keep_local_release_archives,
                reason="local_release_archive",
            )
            + _plan_trimmed_children(
                local_root / "integration_smoke",
                predicate=lambda item: item.is_dir(),
                keep=policy.keep_integration_smoke_runs,
                reason="integration_smoke_run",
            )
        ),
        "cloud": (
            _plan_trimmed_children(
                cloud_root / "releases",
                predicate=lambda item: item.is_dir() and not item.name.startswith("rolled_back_"),
                keep=policy.keep_cloud_releases,
                reason="cloud_release_dir",
            )
            + _plan_trimmed_children(
                cloud_root / "releases",
                predicate=lambda item: item.is_dir() and item.name.startswith("rolled_back_"),
                keep=policy.keep_cloud_rollbacks,
                reason="cloud_rollback_dir",
            )
        ),
        "staging": _plan_stale_children(
            cloud_root / "staging",
            predicate=lambda item: True,
            max_age_hours=policy.max_staging_age_hours,
            reason="staging_residue",
            now=now,
        ),
        "uploads": []
        if policy.keep_cloud_uploads
        else _plan_stale_children(
            cloud_root / "uploads",
            predicate=lambda item: True,
            max_age_hours=policy.max_upload_age_hours,
            reason="cloud_upload_artifact",
            now=now,
        ),
        "deployments": _plan_trimmed_children(
            cloud_root / "deployments",
            predicate=lambda item: item.is_dir(),
            keep=policy.keep_cloud_deployments,
            reason="deployment_journal",
        ),
        "automation_state": _plan_automation_state(
            local_root / "automation" / "prod" / "state",
            keep_run_states=policy.keep_automation_run_states,
            keep_recovery_states=policy.keep_automation_recovery_states,
        ),
    }


def summarize(scope: str, removed: List[RemovedArtifact]) -> dict:
    return _summary_dict(removed, scope=scope)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prune generated DGTEAM runtime artifacts and stale operational residue.")
    parser.add_argument("--project-root", default=str(PROJECT_ROOT))
    parser.add_argument("--legacy-root", default="")
    parser.add_argument("--keep-local-releases", type=int, default=None)
    parser.add_argument("--keep-local-release-archives", type=int, default=None)
    parser.add_argument("--keep-integration-smoke", type=int, default=None)
    parser.add_argument("--keep-cloud-releases", type=int, default=None)
    parser.add_argument("--keep-cloud-rollbacks", type=int, default=None)
    parser.add_argument("--keep-cloud-deployments", type=int, default=None)
    parser.add_argument("--keep-automation-run-states", type=int, default=None)
    parser.add_argument("--keep-automation-recovery-states", type=int, default=None)
    parser.add_argument("--max-staging-age-hours", type=float, default=None)
    parser.add_argument("--max-upload-age-hours", type=float, default=None)
    parser.add_argument("--keep-cloud-uploads", action="store_true", default=None)
    parser.add_argument("--skip-legacy-migration-cleanup", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    project_root = Path(args.project_root).expanduser().resolve()
    runtime_root = project_root / "runtime"
    now = datetime.now()
    policy = _build_policy(args, project_root=project_root)
    plan = _plan_runtime_cleanup(project_root=project_root, policy=policy, now=now)

    if args.dry_run:
        total_reclaimed_bytes = sum(sum(item.size_bytes for item in artifacts) for artifacts in plan.values())
        print(
            json.dumps(
                {
                    "ok": True,
                    "mode": "dry_run",
                    "project_root": str(project_root),
                    "runtime_root": str(runtime_root),
                    "legacy_root": str(args.legacy_root or ""),
                    "policy": policy.__dict__,
                    "reclaimed_gb": round(total_reclaimed_bytes / 1024 / 1024 / 1024, 3),
                    **{
                        key: summarize(key, list(value))
                        for key, value in plan.items()
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    summaries = {
        key: summarize(key, _apply_plan(value))
        for key, value in plan.items()
    }

    scratch_removed: List[RemovedArtifact] = []
    prune_runtime_scratch(runtime_root=runtime_root, removed=scratch_removed)
    scratch_summary = summarize("scratch", scratch_removed)

    legacy_removed: List[RemovedArtifact] = []
    legacy_root_text = str(args.legacy_root or "").strip()
    if legacy_root_text and not args.skip_legacy_migration_cleanup:
        prune_legacy_migration_artifacts(
            legacy_root=Path(legacy_root_text).expanduser().resolve(),
            removed=legacy_removed,
        )
    legacy_summary = summarize("legacy", legacy_removed)

    total_reclaimed_bytes = sum(int(summary.get("reclaimed_bytes") or 0) for summary in summaries.values())
    total_reclaimed_bytes += int(scratch_summary.get("reclaimed_bytes") or 0)
    total_reclaimed_bytes += int(legacy_summary.get("reclaimed_bytes") or 0)

    print(
        json.dumps(
            {
                "ok": True,
                "project_root": str(project_root),
                "runtime_root": str(runtime_root),
                "legacy_root": legacy_root_text,
                "policy": policy.__dict__,
                "reclaimed_gb": round(total_reclaimed_bytes / 1024 / 1024 / 1024, 3),
                **summaries,
                "scratch": scratch_summary,
                "legacy": legacy_summary,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
