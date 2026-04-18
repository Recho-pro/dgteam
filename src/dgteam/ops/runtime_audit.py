from __future__ import annotations

import shutil
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from dgteam.core.config import load_settings
from dgteam.core.textio import read_json_utf8


def _now() -> datetime:
    return datetime.now()


def _safe_age_hours(path: Path, *, now: datetime) -> float | None:
    if not path.exists():
        return None
    try:
        modified = datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return None
    delta = now - modified
    return round(max(0.0, delta.total_seconds()) / 3600.0, 3)


def _directory_entries(path: Path) -> list[Path]:
    if not path.exists() or not path.is_dir():
        return []
    return sorted(path.iterdir(), key=lambda item: item.name)


def _directory_snapshot(path: Path, *, now: datetime) -> dict[str, Any]:
    entries = _directory_entries(path)
    oldest_hours: float | None = None
    newest_hours: float | None = None
    sample: list[str] = []
    for entry in entries[:10]:
        sample.append(entry.name)
    for entry in entries:
        age = _safe_age_hours(entry, now=now)
        if age is None:
            continue
        oldest_hours = age if oldest_hours is None else max(oldest_hours, age)
        newest_hours = age if newest_hours is None else min(newest_hours, age)
    return {
        "path": str(path),
        "exists": path.exists(),
        "entry_count": len(entries),
        "entries_sample": sample,
        "oldest_entry_age_hours": oldest_hours,
        "newest_entry_age_hours": newest_hours,
    }


def _load_recent_deployments(state_dir: Path, *, limit: int = 10) -> list[dict[str, Any]]:
    if not state_dir.exists():
        return []
    items: list[tuple[float, dict[str, Any]]] = []
    for run_dir in state_dir.iterdir():
        if not run_dir.is_dir():
            continue
        status_path = run_dir / "status.json"
        if not status_path.exists():
            continue
        try:
            payload = read_json_utf8(status_path)
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        try:
            mtime = status_path.stat().st_mtime
        except OSError:
            mtime = 0.0
        items.append((mtime, dict(payload)))
    items.sort(key=lambda item: item[0], reverse=True)
    return [payload for _, payload in items[:limit]]


def _queue_counts(queue_root: Path, *, now: datetime) -> dict[str, Any]:
    queued_dir = queue_root / "queued"
    processing_dir = queue_root / "processing"
    completed_dir = queue_root / "completed"
    failed_dir = queue_root / "failed"
    status = {
        "path": str(queue_root),
        "exists": queue_root.exists(),
        "queued": len(list(queued_dir.glob("*.json"))) if queued_dir.exists() else 0,
        "processing": len(list(processing_dir.glob("*.json"))) if processing_dir.exists() else 0,
        "completed": len(list(completed_dir.glob("*.json"))) if completed_dir.exists() else 0,
        "failed": len(list(failed_dir.glob("*.json"))) if failed_dir.exists() else 0,
        "oldest_queued_age_hours": None,
        "oldest_processing_age_hours": None,
    }
    queued_ages = [_safe_age_hours(path, now=now) for path in queued_dir.glob("*.json")] if queued_dir.exists() else []
    processing_ages = (
        [_safe_age_hours(path, now=now) for path in processing_dir.glob("*.json")] if processing_dir.exists() else []
    )
    queued_ages = [age for age in queued_ages if age is not None]
    processing_ages = [age for age in processing_ages if age is not None]
    if queued_ages:
        status["oldest_queued_age_hours"] = round(max(queued_ages), 3)
    if processing_ages:
        status["oldest_processing_age_hours"] = round(max(processing_ages), 3)
    return status


def _latest_backup(backup_root: Path, *, now: datetime) -> dict[str, Any]:
    if not backup_root.exists():
        return {
            "path": str(backup_root),
            "exists": False,
            "checked": False,
            "latest_backup": "",
            "latest_backup_age_hours": None,
        }
    archives = sorted(backup_root.glob("backup_*.tar.gz"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not archives:
        return {
            "path": str(backup_root),
            "exists": True,
            "checked": True,
            "latest_backup": "",
            "latest_backup_age_hours": None,
        }
    latest = archives[0]
    return {
        "path": str(backup_root),
        "exists": True,
        "checked": True,
        "latest_backup": str(latest),
        "latest_backup_age_hours": _safe_age_hours(latest, now=now),
    }


def _path_size_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return int(path.stat().st_size)
        except OSError:
            return 0
    total = 0
    for item in path.rglob("*"):
        if not item.is_file():
            continue
        try:
            total += int(item.stat().st_size)
        except OSError:
            continue
    return total


def _size_gb(path: Path) -> float:
    return round(_path_size_bytes(path) / 1024 / 1024 / 1024, 3)


@dataclass(slots=True, frozen=True)
class RuntimeAuditThresholds:
    disk_warn_percent: int = 80
    max_backup_age_hours: float = 30.0
    max_staging_age_hours: float = 6.0
    max_upload_age_hours: float = 24.0
    max_worker_backlog: int = 10
    max_failed_tasks: int = 0


def build_runtime_audit(
    *,
    project_root: Path | None = None,
    backup_root: Path | None = None,
    thresholds: RuntimeAuditThresholds | None = None,
) -> dict[str, Any]:
    resolved_thresholds = thresholds or RuntimeAuditThresholds()
    settings = load_settings(project_root=project_root)
    root = settings.project_root
    now = _now()

    disk_target = settings.cloud_root if settings.cloud_root.exists() else root
    disk_usage = shutil.disk_usage(disk_target)
    used_percent = round((disk_usage.used / disk_usage.total) * 100, 2) if disk_usage.total else 0.0

    releases_snapshot = _directory_snapshot(settings.release.history_dir, now=now)
    staging_snapshot = _directory_snapshot(settings.release.staging_dir, now=now)
    uploads_snapshot = _directory_snapshot(settings.publish_api.uploads_dir, now=now)
    deployment_snapshot = _directory_snapshot(settings.release.state_dir, now=now)
    queue_status = _queue_counts(settings.wechat_official.state_dir / "recognition", now=now)
    recent_deployments = _load_recent_deployments(settings.release.state_dir, limit=10)
    latest_backup = _latest_backup(backup_root or Path("/var/backups/dgteam"), now=now)

    local_automation_state = root / "runtime" / "local" / "automation" / "prod" / "state"
    callback_state = settings.wechat_official.state_dir / "callback_dedupe"
    disk_governance = {
        "largest_runtime_paths": sorted(
            [
                {"label": "cloud_previous", "path": str(settings.release.previous_dir), "size_gb": _size_gb(settings.release.previous_dir)},
                {"label": "cloud_current", "path": str(settings.release.current_dir), "size_gb": _size_gb(settings.release.current_dir)},
                {"label": "cloud_release_history", "path": str(settings.release.history_dir), "size_gb": _size_gb(settings.release.history_dir)},
                {"label": "cloud_deployments", "path": str(settings.release.state_dir), "size_gb": _size_gb(settings.release.state_dir)},
                {"label": "cloud_uploads", "path": str(settings.publish_api.uploads_dir), "size_gb": _size_gb(settings.publish_api.uploads_dir)},
                {"label": "cloud_staging", "path": str(settings.release.staging_dir), "size_gb": _size_gb(settings.release.staging_dir)},
                {"label": "local_data", "path": str(root / "runtime" / "local" / "data"), "size_gb": _size_gb(root / "runtime" / "local" / "data")},
                {"label": "local_releases", "path": str(root / "runtime" / "local" / "releases"), "size_gb": _size_gb(root / "runtime" / "local" / "releases")},
                {"label": "local_integration_smoke", "path": str(root / "runtime" / "local" / "integration_smoke"), "size_gb": _size_gb(root / "runtime" / "local" / "integration_smoke")},
                {"label": "local_automation_state", "path": str(local_automation_state), "size_gb": _size_gb(local_automation_state)},
            ],
            key=lambda item: item["size_gb"],
            reverse=True,
        ),
        "prune_targets": [
            "runtime/cloud/releases",
            "runtime/cloud/staging",
            "runtime/cloud/uploads",
            "runtime/cloud/deployments (older journals only)",
            "runtime/local/releases",
            "runtime/local/integration_smoke",
            "runtime/local/automation/prod/state (older checkpoint files only)",
        ],
    }

    backup_scope = {
        "current_release": {
            "path": str(settings.release.current_dir),
            "policy": "backup",
            "reason": "Live SQLite and release metadata are the primary recovery source.",
        },
        "previous_release": {
            "path": str(settings.release.previous_dir),
            "policy": "backup",
            "reason": "Rollback safety depends on preserving the previous live release.",
        },
        "release_history": {
            "path": str(settings.release.history_dir),
            "policy": "retention_and_audit",
            "reason": "Release history can be rebuilt from source bundles, but should be retained briefly for rollback evidence.",
        },
        "staging": {
            "path": str(settings.release.staging_dir),
            "policy": "audit_only",
            "reason": "Staging is an ephemeral deployment workspace and should normally be empty between deploys.",
        },
        "uploads": {
            "path": str(settings.publish_api.uploads_dir),
            "policy": "audit_only",
            "reason": "Uploads are temporary ingress artifacts and should age out after import.",
        },
        "deployments": {
            "path": str(settings.release.state_dir),
            "policy": "audit_plus_light_backup",
            "reason": "Deployment journals are small but crucial for incident reconstruction.",
        },
        "automation_state": {
            "path": str(local_automation_state),
            "policy": "checkpoint_backup_and_audit",
            "reason": "Automation state is needed for local task continuity, but is not part of cloud live data restore.",
        },
        "wechat_callback_state": {
            "path": str(callback_state),
            "policy": "audit_only",
            "reason": "Callback/session state is useful for replay analysis but is not part of the current backup restore payload.",
        },
    }

    alerts: list[dict[str, Any]] = []
    if used_percent >= resolved_thresholds.disk_warn_percent:
        alerts.append(
            {
                "level": "warning",
                "code": "disk_usage_high",
                "message": f"Disk usage is {used_percent}% on {disk_target}.",
                "path": str(disk_target),
            }
        )
    if not latest_backup["exists"]:
        alerts.append(
            {
                "level": "warning",
                "code": "backup_root_missing",
                "message": "Configured backup root does not exist yet.",
                "path": latest_backup["path"],
            }
        )
    elif latest_backup["checked"] and latest_backup["latest_backup_age_hours"] is None:
        alerts.append(
            {
                "level": "warning",
                "code": "backup_missing",
                "message": "No backup archive was found in the configured backup root.",
                "path": latest_backup["path"],
            }
        )
    elif latest_backup["latest_backup_age_hours"] is not None and latest_backup["latest_backup_age_hours"] > resolved_thresholds.max_backup_age_hours:
        alerts.append(
            {
                "level": "warning",
                "code": "backup_stale",
                "message": f"Latest backup is {latest_backup['latest_backup_age_hours']} hours old.",
                "path": latest_backup["latest_backup"],
            }
        )
    if staging_snapshot["entry_count"] and (
        staging_snapshot["oldest_entry_age_hours"] is not None
        and staging_snapshot["oldest_entry_age_hours"] > resolved_thresholds.max_staging_age_hours
    ):
        alerts.append(
            {
                "level": "warning",
                "code": "staging_residue",
                "message": f"Staging contains {staging_snapshot['entry_count']} stale entries.",
                "path": staging_snapshot["path"],
            }
        )
    if uploads_snapshot["entry_count"] and (
        uploads_snapshot["oldest_entry_age_hours"] is not None
        and uploads_snapshot["oldest_entry_age_hours"] > resolved_thresholds.max_upload_age_hours
    ):
        alerts.append(
            {
                "level": "warning",
                "code": "upload_residue",
                "message": f"Uploads contains {uploads_snapshot['entry_count']} stale entries.",
                "path": uploads_snapshot["path"],
            }
        )
    backlog = int(queue_status["queued"]) + int(queue_status["processing"])
    if backlog > resolved_thresholds.max_worker_backlog:
        alerts.append(
            {
                "level": "warning",
                "code": "worker_backlog",
                "message": f"WeChat recognition backlog is {backlog}, above the threshold {resolved_thresholds.max_worker_backlog}.",
                "path": queue_status["path"],
            }
        )
    if int(queue_status["failed"]) > resolved_thresholds.max_failed_tasks:
        alerts.append(
            {
                "level": "warning",
                "code": "worker_failed_tasks",
                "message": f"WeChat recognition failed tasks count is {queue_status['failed']}.",
                "path": queue_status["path"],
            }
        )
    failed_deployments = [
        item
        for item in recent_deployments
        if str(item.get("status") or "").strip().lower() == "failed"
    ]
    if failed_deployments:
        latest_failed = failed_deployments[0]
        alerts.append(
            {
                "level": "warning",
                "code": "recent_deployment_failed",
                "message": f"Recent deployment {latest_failed.get('deployment_id', '')} finished with status=failed.",
                "path": str(settings.release.state_dir),
            }
        )

    return {
        "ok": not alerts,
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "project_root": str(root),
        "thresholds": asdict(resolved_thresholds),
        "backup_scope": backup_scope,
        "disk": {
            "path": str(disk_target),
            "used_percent": used_percent,
            "free_gb": round(disk_usage.free / 1024 / 1024 / 1024, 3),
            "total_gb": round(disk_usage.total / 1024 / 1024 / 1024, 3),
        },
        "disk_governance": disk_governance,
        "backup_freshness": latest_backup,
        "runtime": {
            "releases": releases_snapshot,
            "staging": staging_snapshot,
            "uploads": uploads_snapshot,
            "deployments": deployment_snapshot,
        },
        "worker_backlog": queue_status,
        "automation_state": {
            "local_prod": _directory_snapshot(local_automation_state, now=now),
            "wechat_official": _directory_snapshot(settings.wechat_official.state_dir, now=now),
            "wechat_clawbot": _directory_snapshot(settings.wechat_clawbot.state_dir, now=now),
        },
        "recent_deployments": recent_deployments,
        "alerts": alerts,
    }
