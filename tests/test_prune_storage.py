from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _touch(path: Path, *, age_hours: float = 0.0, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if age_hours > 0:
        timestamp = path.stat().st_mtime - (age_hours * 3600.0)
        os.utime(path, (timestamp, timestamp))


def _touch_dir(path: Path, *, age_hours: float = 0.0) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if age_hours > 0:
        timestamp = path.stat().st_mtime - (age_hours * 3600.0)
        os.utime(path, (timestamp, timestamp))


def _run_prune(tmp_path: Path, *extra: str) -> dict[str, object]:
    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "prune_storage.py"),
            "--project-root",
            str(tmp_path),
            *extra,
        ],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def _seed_runtime(tmp_path: Path) -> None:
    _touch(tmp_path / "runtime" / "local" / "releases" / "release_old" / "marker.txt", age_hours=12)
    _touch(tmp_path / "runtime" / "local" / "releases" / "release_new" / "marker.txt", age_hours=1)
    _touch(tmp_path / "runtime" / "local" / "releases" / "release_old.zip", age_hours=12)
    _touch(tmp_path / "runtime" / "local" / "releases" / "release_new.zip", age_hours=1)
    _touch(tmp_path / "runtime" / "local" / "integration_smoke" / "run_old" / "smoke_report.json", age_hours=12)
    _touch(tmp_path / "runtime" / "local" / "integration_smoke" / "run_new" / "smoke_report.json", age_hours=1)

    _touch(tmp_path / "runtime" / "cloud" / "releases" / "release_old" / "marker.txt", age_hours=12)
    _touch(tmp_path / "runtime" / "cloud" / "releases" / "release_new" / "marker.txt", age_hours=1)
    _touch(tmp_path / "runtime" / "cloud" / "releases" / "rolled_back_old" / "marker.txt", age_hours=12)
    _touch(tmp_path / "runtime" / "cloud" / "releases" / "rolled_back_new" / "marker.txt", age_hours=1)

    _touch(tmp_path / "runtime" / "cloud" / "staging" / "deploy_old" / "marker.txt", age_hours=12)
    _touch_dir(tmp_path / "runtime" / "cloud" / "staging" / "deploy_old", age_hours=12)
    _touch(tmp_path / "runtime" / "cloud" / "uploads" / "bundle_old.zip", age_hours=30)
    _touch(tmp_path / "runtime" / "cloud" / "uploads" / "bundle_new.zip", age_hours=1)

    _touch(tmp_path / "runtime" / "cloud" / "deployments" / "deploy_old" / "status.json", age_hours=12, text='{"status":"completed"}')
    _touch(tmp_path / "runtime" / "cloud" / "deployments" / "deploy_new" / "status.json", age_hours=1, text='{"status":"completed"}')

    _touch(tmp_path / "runtime" / "local" / "automation" / "prod" / "state" / "last_run.json", age_hours=1, text="{}")
    _touch(tmp_path / "runtime" / "local" / "automation" / "prod" / "state" / "run_old.json", age_hours=12, text="{}")
    _touch(tmp_path / "runtime" / "local" / "automation" / "prod" / "state" / "run_new.json", age_hours=1, text="{}")
    _touch(
        tmp_path / "runtime" / "local" / "automation" / "prod" / "state" / "manual_sync_recovery_old.json",
        age_hours=12,
        text="{}",
    )
    _touch(
        tmp_path / "runtime" / "local" / "automation" / "prod" / "state" / "manual_sync_recovery_new.json",
        age_hours=1,
        text="{}",
    )


def test_prune_storage_dry_run_reports_real_candidates(tmp_path: Path) -> None:
    _seed_runtime(tmp_path)

    payload = _run_prune(
        tmp_path,
        "--dry-run",
        "--keep-local-releases",
        "1",
        "--keep-local-release-archives",
        "1",
        "--keep-integration-smoke",
        "1",
        "--keep-cloud-releases",
        "1",
        "--keep-cloud-rollbacks",
        "1",
        "--keep-cloud-deployments",
        "1",
        "--keep-automation-run-states",
        "1",
        "--keep-automation-recovery-states",
        "1",
        "--max-staging-age-hours",
        "6",
        "--max-upload-age-hours",
        "24",
    )

    assert payload["mode"] == "dry_run"
    assert payload["local"]["removed_count"] >= 3
    assert payload["cloud"]["removed_count"] >= 2
    assert payload["staging"]["removed_count"] == 1
    assert payload["uploads"]["removed_count"] == 1
    assert payload["deployments"]["removed_count"] == 1
    assert payload["automation_state"]["removed_count"] == 2
    assert (tmp_path / "runtime" / "cloud" / "staging" / "deploy_old").exists()
    assert (tmp_path / "runtime" / "local" / "automation" / "prod" / "state" / "run_old.json").exists()


def test_prune_storage_applies_policy_and_keeps_safety_markers(tmp_path: Path) -> None:
    _seed_runtime(tmp_path)

    payload = _run_prune(
        tmp_path,
        "--keep-local-releases",
        "1",
        "--keep-local-release-archives",
        "1",
        "--keep-integration-smoke",
        "1",
        "--keep-cloud-releases",
        "1",
        "--keep-cloud-rollbacks",
        "1",
        "--keep-cloud-deployments",
        "1",
        "--keep-automation-run-states",
        "1",
        "--keep-automation-recovery-states",
        "1",
        "--max-staging-age-hours",
        "6",
        "--max-upload-age-hours",
        "24",
    )

    assert payload["ok"] is True
    assert not (tmp_path / "runtime" / "local" / "releases" / "release_old").exists()
    assert (tmp_path / "runtime" / "local" / "releases" / "release_new").exists()
    assert not (tmp_path / "runtime" / "cloud" / "staging" / "deploy_old").exists()
    assert not (tmp_path / "runtime" / "cloud" / "uploads" / "bundle_old.zip").exists()
    assert (tmp_path / "runtime" / "cloud" / "uploads" / "bundle_new.zip").exists()
    assert not (tmp_path / "runtime" / "cloud" / "deployments" / "deploy_old").exists()
    assert (tmp_path / "runtime" / "cloud" / "deployments" / "deploy_new").exists()
    assert not (tmp_path / "runtime" / "local" / "automation" / "prod" / "state" / "run_old.json").exists()
    assert not (tmp_path / "runtime" / "local" / "automation" / "prod" / "state" / "manual_sync_recovery_old.json").exists()
    assert (tmp_path / "runtime" / "local" / "automation" / "prod" / "state" / "last_run.json").exists()
