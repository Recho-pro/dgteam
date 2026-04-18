from __future__ import annotations

from pathlib import Path


REAL_SOURCE_DB_CONTRACT_VERSION = "real-source-db.v1"
ACTIVE_RELEASE_SOURCE_DB_RELATIVE = "runtime/cloud/current/dgteam.db"
LEGACY_WORKING_SOURCE_DB_RELATIVE = "runtime/local/data/dgteam.db"


def default_real_source_db(project_root: Path) -> Path:
    return (project_root / Path(ACTIVE_RELEASE_SOURCE_DB_RELATIVE)).expanduser().resolve()


def legacy_working_source_db(project_root: Path) -> Path:
    return (project_root / Path(LEGACY_WORKING_SOURCE_DB_RELATIVE)).expanduser().resolve()


def resolve_real_source_db(project_root: Path, explicit_source_db: Path | None = None) -> dict[str, str]:
    if explicit_source_db is not None:
        resolved = explicit_source_db.expanduser().resolve()
        return {
            "contract_version": REAL_SOURCE_DB_CONTRACT_VERSION,
            "selection": "explicit-override",
            "resolved_path": str(resolved),
            "default_relative_path": str(ACTIVE_RELEASE_SOURCE_DB_RELATIVE),
            "default_path": str(default_real_source_db(project_root)),
            "legacy_override_relative_path": str(LEGACY_WORKING_SOURCE_DB_RELATIVE),
            "legacy_override_path": str(legacy_working_source_db(project_root)),
            "notes": "DGTEAM_SMOKE_SOURCE_DB or --source-db explicitly overrides the active release default.",
        }

    resolved_default = default_real_source_db(project_root)
    return {
        "contract_version": REAL_SOURCE_DB_CONTRACT_VERSION,
        "selection": "active-release-default",
        "resolved_path": str(resolved_default),
        "default_relative_path": str(ACTIVE_RELEASE_SOURCE_DB_RELATIVE),
        "default_path": str(resolved_default),
        "legacy_override_relative_path": str(LEGACY_WORKING_SOURCE_DB_RELATIVE),
        "legacy_override_path": str(legacy_working_source_db(project_root)),
        "notes": "Real-source rehearsal defaults to the active release DB so local and /srv trusted gates sample the same published truth.",
    }
