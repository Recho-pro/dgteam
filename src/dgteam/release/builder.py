from __future__ import annotations

import gc
import platform
import shutil
import sqlite3
import uuid
import zipfile
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable

from dgteam.core.models import ReleaseManifest
from dgteam.core.storage import DGTeamStorage
from dgteam.core.textio import write_json_utf8
from dgteam.query_api.ui_assets import RELEASE_UI_DIRNAME, REQUIRED_UI_ASSET_FILES, package_query_ui_assets
from dgteam.release.live_market import build_live_market_payload, export_live_market_payload


def _timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _collect_file_entries(paths: Iterable[Path], *, root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            continue
        entries.append(
            {
                "path": str(path.relative_to(root)),
                "size": int(path.stat().st_size),
            }
        )
    return entries


def build_release_manifest(
    target_dir: Path,
    *,
    release_id: str | None = None,
    run_key: str = "unset",
    quote_count: int = 0,
    snapshot_count: int = 0,
    files: Iterable[Path] = (),
    rule_version: str = "dgteam-rules.v1",
    build_version: str = "dgteam-build.v1",
) -> ReleaseManifest:
    target_dir.mkdir(parents=True, exist_ok=True)
    manifest = ReleaseManifest(
        release_id=release_id or target_dir.name,
        run_key=run_key,
        published_at=_timestamp(),
        quote_count=int(quote_count),
        snapshot_count=int(snapshot_count),
        rule_version=rule_version,
        build_version=build_version,
        source_machine=platform.node() or "unknown",
        files=_collect_file_entries(files, root=target_dir),
    )
    write_json_utf8(target_dir / "manifest.json", manifest.to_dict())
    return manifest


_REQUIRED_RELEASE_FILES = (
    "manifest.json",
    "release.json",
    "summary.json",
    "market_v1_snapshot.csv",
    "market_v1_clusters.csv",
    "dgteam.db",
)
_REQUIRED_RELEASE_UI_FILES = tuple(f"{RELEASE_UI_DIRNAME}/{name}" for name in REQUIRED_UI_ASSET_FILES)


def _new_staging_dir(target: Path, purpose: str) -> Path:
    return target.parent / f".{target.name}.{purpose}_{uuid.uuid4().hex}"


def _validate_release_bundle_dir(release_dir: Path) -> None:
    missing = [name for name in (*_REQUIRED_RELEASE_FILES, *_REQUIRED_RELEASE_UI_FILES) if not (release_dir / name).is_file()]
    if missing:
        raise RuntimeError(f"Release bundle is incomplete before switch: missing {', '.join(missing)}")
    db_path = release_dir / "dgteam.db"
    with closing(sqlite3.connect(db_path)) as conn:
        result = str(conn.execute("PRAGMA quick_check").fetchone()[0])
    if result.lower() != "ok":
        raise RuntimeError(f"Release database quick_check failed before switch: {result}")


def _replace_directory_atomically(staging_dir: Path, target: Path) -> None:
    backup_dir = _new_staging_dir(target, "previous")
    moved_existing = False
    try:
        if target.exists():
            target.rename(backup_dir)
            moved_existing = True
        staging_dir.rename(target)
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
    except Exception:
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        if moved_existing and backup_dir.exists():
            backup_dir.rename(target)
        raise


def build_local_release_bundle(
    storage: DGTeamStorage,
    target_dir: Path,
    *,
    run_key: str = "",
) -> Dict[str, Any]:
    target = Path(target_dir).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    build_dir = _new_staging_dir(target, "building")

    try:
        build_dir.mkdir(parents=True, exist_ok=False)
        payload = build_live_market_payload(storage, run_key)
        export_result = export_live_market_payload(payload, build_dir, public_outdir=target)

        db_copy_path = build_dir / "dgteam.db"
        storage.export_database_snapshot(db_copy_path)
        bundle_storage = DGTeamStorage(db_copy_path)
        publish_result = bundle_storage.publish_market_snapshots(
            payload["run_key"],
            payload["snapshot_rows"],
            summary=payload["summary"],
            published_at=payload["built_at"],
        )
        gc.collect()

        ui_asset_dir = build_dir / RELEASE_UI_DIRNAME
        ui_asset_manifest = package_query_ui_assets(ui_asset_dir)
        ui_asset_files = sorted(path for path in ui_asset_dir.rglob("*") if path.is_file())

        release_metadata = {
            "release_id": target.name,
            "run_key": payload["run_key"],
            "built_at": payload["built_at"],
            "snapshot_count": int(publish_result.get("snapshot_count") or 0),
            "cluster_row_count": int(len(payload["cluster_rows"])),
            "summary": dict(payload["summary"] or {}),
            "exports": export_result,
            "database": str(target / "dgteam.db"),
            "query_ui": {
                "asset_dir": str(target / RELEASE_UI_DIRNAME),
                "asset_manifest": ui_asset_manifest,
            },
        }
        metadata_path = build_dir / "release.json"
        write_json_utf8(metadata_path, release_metadata)

        manifest = build_release_manifest(
            build_dir,
            release_id=target.name,
            run_key=payload["run_key"],
            quote_count=int(payload.get("summary", {}).get("counts", {}).get("source_rows", 0) or 0),
            snapshot_count=int(publish_result.get("snapshot_count") or 0),
            files=(
                build_dir / "market_v1_snapshot.csv",
                build_dir / "market_v1_clusters.csv",
                build_dir / "summary.json",
                metadata_path,
                db_copy_path,
                *ui_asset_files,
            ),
            rule_version="dgteam-rules.v1",
            build_version="dgteam-build.v2",
        )
        _validate_release_bundle_dir(build_dir)
        gc.collect()
        _replace_directory_atomically(build_dir, target)
    except Exception:
        if build_dir.exists():
            shutil.rmtree(build_dir, ignore_errors=True)
        raise
    return {
        "release_id": manifest.release_id,
        "release_dir": str(target),
        "run_key": manifest.run_key,
        "manifest": manifest.to_dict(),
        "release": release_metadata,
    }


def archive_release_bundle(release_dir: Path, archive_path: Path | None = None) -> Dict[str, Any]:
    source = Path(release_dir).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(f"Release directory does not exist: {source}")
    target = Path(archive_path or source.with_suffix(".zip")).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_target = target.with_name(f".{target.name}.tmp_{uuid.uuid4().hex}")
    try:
        with zipfile.ZipFile(temp_target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(source.rglob("*")):
                if path.is_file():
                    archive.write(path, arcname=str(path.relative_to(source)))
        temp_target.replace(target)
    finally:
        if temp_target.exists():
            temp_target.unlink()
    return {
        "release_dir": str(source),
        "archive_path": str(target),
        "size": int(target.stat().st_size),
    }
