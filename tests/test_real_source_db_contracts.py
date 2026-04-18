from __future__ import annotations

from pathlib import Path

from dgteam.ops.real_source_db import (
    ACTIVE_RELEASE_SOURCE_DB_RELATIVE,
    LEGACY_WORKING_SOURCE_DB_RELATIVE,
    REAL_SOURCE_DB_CONTRACT_VERSION,
    default_real_source_db,
    legacy_working_source_db,
    resolve_real_source_db,
)


def test_default_real_source_db_points_at_active_release_db(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)

    resolved = resolve_real_source_db(project_root)

    assert resolved["contract_version"] == REAL_SOURCE_DB_CONTRACT_VERSION
    assert resolved["selection"] == "active-release-default"
    assert resolved["default_relative_path"] == str(ACTIVE_RELEASE_SOURCE_DB_RELATIVE)
    assert resolved["legacy_override_relative_path"] == str(LEGACY_WORKING_SOURCE_DB_RELATIVE)
    assert Path(resolved["resolved_path"]) == default_real_source_db(project_root)
    assert Path(resolved["legacy_override_path"]) == legacy_working_source_db(project_root)


def test_explicit_source_db_override_keeps_legacy_working_db_available(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    explicit = project_root / LEGACY_WORKING_SOURCE_DB_RELATIVE
    explicit.parent.mkdir(parents=True, exist_ok=True)
    explicit.write_text("", encoding="utf-8")

    resolved = resolve_real_source_db(project_root, explicit_source_db=explicit)

    assert resolved["selection"] == "explicit-override"
    assert Path(resolved["resolved_path"]) == explicit.resolve()
    assert resolved["default_relative_path"] == str(ACTIVE_RELEASE_SOURCE_DB_RELATIVE)
    assert resolved["legacy_override_relative_path"] == str(LEGACY_WORKING_SOURCE_DB_RELATIVE)
