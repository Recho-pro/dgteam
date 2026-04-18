from __future__ import annotations

from pathlib import Path

from dgteam.automation.prod_collect_and_sync import build_paths


def test_prod_collect_and_sync_uses_project_runtime(tmp_path: Path) -> None:
    project_root = tmp_path / "dgteam"
    automation_home = project_root / "config" / "automation" / "prod"
    paths = build_paths(project_root, automation_home)

    assert paths.automation_home == automation_home
    assert paths.local_root == project_root / "runtime" / "local"
    assert paths.cloud_root == project_root / "runtime" / "cloud"
    assert paths.db_path == project_root / "runtime" / "local" / "data" / "dgteam.db"
    assert paths.profile_dir == project_root / "runtime" / "local" / "browser_profile"
    assert paths.releases_dir == project_root / "runtime" / "local" / "releases"
    assert paths.state_dir == project_root / "runtime" / "local" / "automation" / "prod" / "state"
    assert paths.log_dir == project_root / "runtime" / "local" / "automation" / "prod" / "logs"
    assert paths.auth_path == automation_home / "auth.json"
    assert paths.profile_env_path == automation_home / "profile.env"
    assert paths.sync_env_path == automation_home / "sync.env"

