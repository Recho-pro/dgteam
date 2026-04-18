from __future__ import annotations

import os
from collections import namedtuple
from pathlib import Path

from dgteam.core.textio import write_json_utf8
from dgteam.ops.runtime_audit import RuntimeAuditThresholds, build_runtime_audit


DiskUsage = namedtuple("usage", ["total", "used", "free"])


def _touch(path: Path, *, age_hours: float = 0.0, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if age_hours > 0:
        timestamp = path.stat().st_mtime - (age_hours * 3600.0)
        os.utime(path, (timestamp, timestamp))


def test_runtime_audit_classifies_backup_scope_and_finds_alerts(tmp_path: Path, monkeypatch) -> None:
    backup_root = tmp_path / "backups"
    _touch(backup_root / "backup_20260417_010000.tar.gz", age_hours=36, text="backup")

    _touch(tmp_path / "runtime" / "cloud" / "staging" / "deploy_candidate" / "marker.txt", age_hours=8, text="stale")
    staging_dir = tmp_path / "runtime" / "cloud" / "staging" / "deploy_candidate"
    staging_timestamp = staging_dir.stat().st_mtime - (8 * 3600.0)
    os.utime(staging_dir, (staging_timestamp, staging_timestamp))
    _touch(tmp_path / "runtime" / "cloud" / "uploads" / "bundle_a.zip", age_hours=30, text="zip")
    _touch(
        tmp_path / "runtime" / "cloud" / "deployments" / "deploy_bad" / "status.json",
        text='{"deployment_id":"deploy_bad","status":"failed"}',
    )
    _touch(tmp_path / "runtime" / "local" / "automation" / "prod" / "state" / "last_run.json", text='{"ok": true}')
    _touch(tmp_path / "runtime" / "local" / "wechat_official" / "state" / "recognition" / "queued" / "task1.json", age_hours=2, text="{}")
    _touch(tmp_path / "runtime" / "local" / "wechat_official" / "state" / "recognition" / "queued" / "task2.json", age_hours=2, text="{}")
    _touch(tmp_path / "runtime" / "local" / "wechat_official" / "state" / "recognition" / "processing" / "task3.json", age_hours=1, text="{}")
    _touch(tmp_path / "runtime" / "local" / "wechat_official" / "state" / "recognition" / "failed" / "task4.json", age_hours=1, text="{}")

    monkeypatch.setattr("dgteam.ops.runtime_audit.shutil.disk_usage", lambda _: DiskUsage(total=100, used=90, free=10))

    report = build_runtime_audit(
        project_root=tmp_path,
        backup_root=backup_root,
        thresholds=RuntimeAuditThresholds(
            disk_warn_percent=80,
            max_backup_age_hours=24.0,
            max_staging_age_hours=6.0,
            max_upload_age_hours=24.0,
            max_worker_backlog=2,
            max_failed_tasks=0,
        ),
    )

    assert report["backup_scope"]["staging"]["policy"] == "audit_only"
    assert report["backup_scope"]["uploads"]["policy"] == "audit_only"
    assert report["backup_scope"]["deployments"]["policy"] == "audit_plus_light_backup"
    assert report["backup_scope"]["automation_state"]["policy"] == "checkpoint_backup_and_audit"
    assert report["worker_backlog"]["queued"] == 2
    assert report["worker_backlog"]["processing"] == 1
    assert report["worker_backlog"]["failed"] == 1
    governance_labels = {item["label"] for item in report["disk_governance"]["largest_runtime_paths"]}
    assert "cloud_previous" in governance_labels
    assert "local_automation_state" in governance_labels

    alert_codes = {item["code"] for item in report["alerts"]}
    assert "disk_usage_high" in alert_codes
    assert "backup_stale" in alert_codes
    assert "staging_residue" in alert_codes
    assert "upload_residue" in alert_codes
    assert "worker_backlog" in alert_codes
    assert "worker_failed_tasks" in alert_codes
    assert "recent_deployment_failed" in alert_codes


def test_runtime_audit_is_ok_when_runtime_is_clean(tmp_path: Path, monkeypatch) -> None:
    write_json_utf8(
        tmp_path / "runtime" / "cloud" / "deployments" / "deploy_ok" / "status.json",
        {"deployment_id": "deploy_ok", "status": "completed"},
    )
    _touch(tmp_path / "runtime" / "local" / "automation" / "prod" / "state" / "last_run.json", text='{"ok": true}')
    _touch(tmp_path / "runtime" / "cloud" / "current" / "manifest.json", text="{}")
    _touch(tmp_path / "runtime" / "cloud" / "previous" / "manifest.json", text="{}")
    _touch(tmp_path / "backups" / "backup_20260417_030000.tar.gz", age_hours=2, text="backup")

    monkeypatch.setattr("dgteam.ops.runtime_audit.shutil.disk_usage", lambda _: DiskUsage(total=100, used=40, free=60))

    report = build_runtime_audit(project_root=tmp_path, backup_root=tmp_path / "backups")

    assert report["ok"] is True
    assert report["alerts"] == []
    assert report["disk_governance"]["largest_runtime_paths"]
