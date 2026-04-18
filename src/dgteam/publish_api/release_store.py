from __future__ import annotations

import gc
import hashlib
import io
import shutil
import sqlite3
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from dgteam.core.storage import DGTeamStorage
from dgteam.core.textio import read_json_utf8, write_json_utf8
from dgteam.query_api.ui_assets import RELEASE_UI_DIRNAME, REQUIRED_UI_ASSET_FILES, read_ui_asset_manifest
from dgteam.release.deploy_state import DeploymentJournal, deployment_id_for_release


REQUIRED_RELEASE_FILES = (
    "manifest.json",
    "release.json",
    "summary.json",
    "market_v1_snapshot.csv",
    "market_v1_clusters.csv",
    "dgteam.db",
)
REQUIRED_QUERY_UI_FILES = tuple(f"{RELEASE_UI_DIRNAME}/{name}" for name in REQUIRED_UI_ASSET_FILES)
REQUIRED_SQLITE_TABLES = (
    "runs",
    "quote_rows",
    "market_snapshots",
    "app_state",
)
REQUIRED_SQLITE_INDEXES = (
    "idx_quote_rows_sku_lookup",
    "idx_quote_rows_run_catalog",
    "idx_market_snapshots_run_query",
    "idx_market_snapshots_run_sort",
)
SQLITE_TRANSIENT_COPY_IGNORE = shutil.ignore_patterns("*.db-wal", "*.db-shm", "*.db-journal")


def _timestamp_slug() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H-%M-%S")


class ReleaseStore:
    def __init__(
        self,
        root: Path,
        *,
        current_dir: Path,
        previous_dir: Path,
        history_dir: Path,
        staging_dir: Path,
        state_dir: Path,
        uploads_dir: Path | None = None,
    ):
        self.root = Path(root).expanduser().resolve()
        self.current_dir = Path(current_dir).expanduser().resolve()
        self.previous_dir = Path(previous_dir).expanduser().resolve()
        self.history_dir = Path(history_dir).expanduser().resolve()
        self.staging_dir = Path(staging_dir).expanduser().resolve()
        self.state_dir = Path(state_dir).expanduser().resolve()
        self.uploads_dir = Path(uploads_dir or (self.root / "uploads")).expanduser().resolve()
        self.ensure_dirs()

    def ensure_dirs(self) -> None:
        for path in (
            self.root,
            self.current_dir,
            self.previous_dir,
            self.history_dir,
            self.staging_dir,
            self.state_dir,
            self.uploads_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def _manifest_from_dir(self, directory: Path) -> Dict[str, Any]:
        manifest_path = directory / "manifest.json"
        if not manifest_path.exists():
            return {}
        try:
            return read_json_utf8(manifest_path)
        except Exception:
            return {}

    def _staging_workspace(self, deployment_id: str) -> Path:
        return self.staging_dir / str(deployment_id or "").strip()

    def _journal(self, deployment_id: str) -> DeploymentJournal:
        return DeploymentJournal(self.state_dir, deployment_id)

    def _replace_directory(self, source: Path, destination: Path) -> None:
        staging = destination.parent / f"{destination.name}.staging"
        backup = destination.parent / f"{destination.name}.previous_staging"
        if staging.exists():
            self._rmtree_with_retry(staging)
        if backup.exists():
            self._rmtree_with_retry(backup)
        self._copytree_with_retry(source, staging, dirs_exist_ok=True)
        moved_existing = False
        try:
            if destination.exists():
                self._retry_file_op(destination.rename, backup)
                moved_existing = True
            self._retry_file_op(staging.rename, destination)
            if backup.exists():
                self._rmtree_with_retry(backup)
        except Exception:
            if destination.exists():
                self._rmtree_with_retry(destination)
            if moved_existing and backup.exists():
                self._retry_file_op(backup.rename, destination)
            raise
        finally:
            if staging.exists():
                self._rmtree_with_retry(staging)

    @staticmethod
    def _should_retry_file_error(exc: BaseException) -> bool:
        if isinstance(exc, PermissionError):
            return True
        if isinstance(exc, OSError) and getattr(exc, "winerror", None) in {32, 33}:
            return True
        return False

    def _retry_file_op(self, operation: Any, *args: Any, **kwargs: Any) -> Any:
        attempts = 20
        delay_seconds = 0.5
        last_error: BaseException | None = None
        for attempt in range(1, attempts + 1):
            try:
                return operation(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                if not self._should_retry_file_error(exc) or attempt == attempts:
                    raise
                last_error = exc
                time.sleep(delay_seconds)
        if last_error is not None:
            raise last_error
        raise RuntimeError("Retry helper reached an unexpected state.")

    def _copytree_with_retry(self, source: Path, destination: Path, *, dirs_exist_ok: bool = False) -> None:
        self._retry_file_op(
            shutil.copytree,
            source,
            destination,
            dirs_exist_ok=dirs_exist_ok,
            ignore=SQLITE_TRANSIENT_COPY_IGNORE,
        )

    def _rmtree_with_retry(self, target: Path) -> None:
        self._retry_file_op(shutil.rmtree, target)

    def _clear_directory(self, directory: Path) -> None:
        target = Path(directory).expanduser().resolve()
        if target.exists():
            self._rmtree_with_retry(target)
        target.mkdir(parents=True, exist_ok=True)

    def _sqlite_tables(self, db_path: Path) -> set[str]:
        db_uri = f"{db_path.as_uri()}?mode=ro&immutable=1"
        with sqlite3.connect(db_uri, uri=True) as conn:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        return {str(row[0] or "").strip() for row in rows}

    def _sqlite_indexes(self, db_path: Path) -> set[str]:
        db_uri = f"{db_path.as_uri()}?mode=ro&immutable=1"
        with sqlite3.connect(db_uri, uri=True) as conn:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'").fetchall()
        return {str(row[0] or "").strip() for row in rows}

    def _db_quick_check(self, db_path: Path) -> str:
        db_uri = f"{db_path.as_uri()}?mode=ro&immutable=1"
        with sqlite3.connect(db_uri, uri=True) as conn:
            return str(conn.execute("PRAGMA quick_check").fetchone()[0] or "")

    def _db_health_check(self, db_path: Path, *, manifest: Dict[str, Any]) -> Dict[str, Any]:
        storage = DGTeamStorage(db_path)
        requested_run_key = str(manifest.get("run_key") or "").strip()
        run_key = requested_run_key or storage.get_preferred_run_key()
        live_marker = storage.get_live_marker(run_key)
        summary = storage.summary(run_key)
        ok = bool(run_key) and (
            int(live_marker.get("market_snapshot_count") or 0) > 0
            or int(live_marker.get("quote_count") or 0) > 0
        )
        return {
            "ok": ok,
            "requested_run_key": requested_run_key,
            "effective_run_key": run_key,
            "live_marker": live_marker,
            "summary": {
                "task_count": int(summary.get("task_count") or 0),
                "quote_count": int(summary.get("quote_count") or 0),
                "target_quote_count": int(summary.get("target_quote_count") or 0),
            },
            "reason": "" if ok else "The staged database does not expose any market snapshots or quote rows for the manifest run key.",
        }

    def _query_ui_validation(self, release_dir: Path, *, manifest: Dict[str, Any]) -> Dict[str, Any]:
        ui_dir = release_dir / RELEASE_UI_DIRNAME
        manifest_files = manifest.get("files") if isinstance(manifest.get("files"), list) else []
        manifest_declares_ui = any(
            str(item.get("path") if isinstance(item, dict) else item).replace("\\", "/").startswith(f"{RELEASE_UI_DIRNAME}/")
            for item in manifest_files
        )
        required = bool(manifest_declares_ui or ui_dir.exists())
        missing = [name for name in REQUIRED_QUERY_UI_FILES if not (release_dir / name).is_file()]
        asset_manifest = read_ui_asset_manifest(ui_dir) if (ui_dir / "asset-manifest.json").is_file() else {}
        version = str(asset_manifest.get("version") or "").strip()
        ok = (not required) or (not missing and bool(version))
        return {
            "ok": ok,
            "required": required,
            "asset_dir": str(ui_dir),
            "version": version,
            "missing_files": missing if required else [],
            "manifest": asset_manifest,
        }

    def validate_release_dir(self, directory: Path) -> Dict[str, Any]:
        target = Path(directory).expanduser().resolve()
        if not target.exists():
            return {
                "ok": False,
                "directory": str(target),
                "missing_files": list(REQUIRED_RELEASE_FILES),
                "manifest": {},
                "db_quick_check": "missing",
                "missing_tables": list(REQUIRED_SQLITE_TABLES),
                "missing_indexes": list(REQUIRED_SQLITE_INDEXES),
                "query_ui": {
                    "ok": False,
                    "required": False,
                    "asset_dir": str(target / RELEASE_UI_DIRNAME),
                    "version": "",
                    "missing_files": list(REQUIRED_QUERY_UI_FILES),
                    "manifest": {},
                },
                "health": {"ok": False, "reason": "Release directory does not exist."},
            }

        missing = [name for name in REQUIRED_RELEASE_FILES if not (target / name).exists()]
        manifest = self._manifest_from_dir(target)
        db_path = target / "dgteam.db"
        db_check = "missing"
        existing_tables: set[str] = set()
        existing_indexes: set[str] = set()
        health: Dict[str, Any] = {"ok": False, "reason": "Database is missing."}
        if db_path.exists():
            db_check = self._db_quick_check(db_path)
            existing_tables = self._sqlite_tables(db_path)
            existing_indexes = self._sqlite_indexes(db_path)
            health = self._db_health_check(db_path, manifest=manifest)

        missing_tables = [name for name in REQUIRED_SQLITE_TABLES if name not in existing_tables]
        missing_indexes = [name for name in REQUIRED_SQLITE_INDEXES if name not in existing_indexes]
        query_ui = self._query_ui_validation(target, manifest=manifest)
        ok = (
            not missing
            and bool(manifest.get("run_key"))
            and db_check == "ok"
            and not missing_tables
            and not missing_indexes
            and bool(query_ui.get("ok"))
            and bool(health.get("ok"))
        )
        return {
            "ok": ok,
            "directory": str(target),
            "missing_files": missing,
            "manifest": manifest,
            "db_quick_check": db_check,
            "missing_tables": missing_tables,
            "missing_indexes": missing_indexes,
            "query_ui": query_ui,
            "health": health,
        }

    def import_local_release(self, source_dir: Path, *, release_id: str = "") -> Dict[str, Any]:
        source = Path(source_dir).expanduser().resolve()
        if not source.exists():
            raise FileNotFoundError(f"Release source directory does not exist: {source}")
        validation = self.validate_release_dir(source)
        if not validation["ok"]:
            raise ValueError(f"Release source failed validation: {validation}")

        resolved_release_id = release_id.strip() or source.name or f"release_{_timestamp_slug()}"
        destination = self.history_dir / resolved_release_id
        if destination.exists():
            self._rmtree_with_retry(destination)
        self._copytree_with_retry(source, destination)
        self._rewrite_release_identity(destination, release_id=resolved_release_id)
        return {
            "release_id": resolved_release_id,
            "release_dir": str(destination),
            "manifest": self._manifest_from_dir(destination),
            "validation": self.validate_release_dir(destination),
        }

    def _rewrite_release_identity(self, directory: Path, *, release_id: str) -> None:
        resolved_release_id = str(release_id or "").strip()
        if not resolved_release_id:
            return

        manifest_path = directory / "manifest.json"
        if manifest_path.exists():
            try:
                manifest = read_json_utf8(manifest_path)
            except Exception:
                manifest = {}
            if isinstance(manifest, dict):
                manifest["release_id"] = resolved_release_id
                write_json_utf8(manifest_path, manifest)

        metadata_path = directory / "release.json"
        if metadata_path.exists():
            try:
                metadata = read_json_utf8(metadata_path)
            except Exception:
                metadata = {}
            if isinstance(metadata, dict):
                metadata["release_id"] = resolved_release_id
                write_json_utf8(metadata_path, metadata)

    def import_release_archive(
        self,
        archive_bytes: bytes,
        *,
        release_id: str = "",
        expected_sha256: str = "",
    ) -> Dict[str, Any]:
        if not archive_bytes:
            raise ValueError("Release archive payload is empty.")

        actual_sha256 = hashlib.sha256(archive_bytes).hexdigest()
        if expected_sha256 and actual_sha256.lower() != expected_sha256.strip().lower():
            raise ValueError("SHA256 mismatch for uploaded release archive.")

        resolved_release_id = release_id.strip() or f"release_{_timestamp_slug()}"
        upload_path = self.uploads_dir / f"{resolved_release_id}.zip"
        upload_path.write_bytes(archive_bytes)

        extraction_dir = self.uploads_dir / f"{resolved_release_id}_unzipped"
        if extraction_dir.exists():
            self._rmtree_with_retry(extraction_dir)
        extraction_dir.mkdir(parents=True, exist_ok=True)

        try:
            with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
                for info in archive.infolist():
                    member_path = Path(info.filename)
                    if member_path.is_absolute() or ".." in member_path.parts:
                        raise ValueError(f"Unsafe archive member path: {info.filename}")
                archive.extractall(extraction_dir)

            import_root = extraction_dir
            child_entries = [item for item in extraction_dir.iterdir()]
            if len(child_entries) == 1 and child_entries[0].is_dir():
                import_root = child_entries[0]

            imported = self.import_local_release(import_root, release_id=resolved_release_id)
            imported["upload"] = {
                "archive_path": str(upload_path),
                "sha256": actual_sha256,
            }
            return imported
        finally:
            gc.collect()
            self._rmtree_with_retry(extraction_dir)

    def _restore_live_after_failure(self, *, had_live_before: bool) -> Dict[str, Any]:
        if had_live_before and self.previous_dir.exists() and any(self.previous_dir.iterdir()):
            return self.rollback()
        if self.current_dir.exists() and any(self.current_dir.iterdir()):
            self._rmtree_with_retry(self.current_dir)
            self.current_dir.mkdir(parents=True, exist_ok=True)
        return {
            "release_id": "",
            "current_dir": str(self.current_dir),
            "manifest": {},
            "validation": {},
            "mode": "cleared_current",
        }

    def deploy_release(self, release_id: str, *, deployment_id: str = "") -> Dict[str, Any]:
        resolved_release_id = str(release_id or "").strip()
        if not resolved_release_id:
            raise ValueError("release_id is required for deploy_release.")

        target = self.history_dir / resolved_release_id
        if not target.exists():
            raise FileNotFoundError(f"Release does not exist: {target}")

        resolved_deployment_id = str(deployment_id or "").strip() or deployment_id_for_release(resolved_release_id)
        journal = self._journal(resolved_deployment_id)
        journal.initialize(
            role="cloud",
            release_id=resolved_release_id,
            metadata={
                "history_dir": str(self.history_dir),
                "current_dir": str(self.current_dir),
                "previous_dir": str(self.previous_dir),
                "staging_dir": str(self._staging_workspace(resolved_deployment_id)),
            },
        )

        stage_dir = self._staging_workspace(resolved_deployment_id)
        had_live_before = self.current_dir.exists() and any(self.current_dir.iterdir())
        switched_live = False
        journal.event("deploy_started", message="Starting staged deployment.", release_id=resolved_release_id)
        journal.update(status="running", step="imported", metadata={"release_id": resolved_release_id})
        try:
            preflight = self.validate_release_dir(target)
            if not preflight["ok"]:
                raise ValueError(f"Release failed validation before staging: {preflight}")
            journal.event("preflight_validated", message="Release validated in history.", validation=preflight)

            self._clear_directory(stage_dir)
            self._copytree_with_retry(target, stage_dir, dirs_exist_ok=True)
            journal.event("staging_ready", message="Release copied into staging.", staging_dir=str(stage_dir))
            journal.update(status="running", step="staging_ready")

            staged_validation = self.validate_release_dir(stage_dir)
            if not staged_validation["ok"]:
                raise ValueError(f"Release failed validation inside staging: {staged_validation}")
            journal.event("staging_validated", message="Staging validation passed.", validation=staged_validation)
            journal.update(
                status="running",
                step="validated",
                metadata={"staged_validation": staged_validation},
            )

            gc.collect()
            if had_live_before:
                self._clear_directory(self.previous_dir)
                self._copytree_with_retry(self.current_dir, self.previous_dir, dirs_exist_ok=True)
                journal.event("previous_backed_up", message="Current live release backed up.", previous_dir=str(self.previous_dir))

            self._replace_directory(stage_dir, self.current_dir)
            switched_live = True
            journal.event("live_switched", message="Staged release atomically switched to live.")
            journal.update(status="running", step="live_switched")

            activated_validation = self.validate_release_dir(self.current_dir)
            if not activated_validation["ok"]:
                raise ValueError(f"Activated release failed post-switch validation: {activated_validation}")

            manifest = self._manifest_from_dir(self.current_dir)
            result = {
                "deployment_id": resolved_deployment_id,
                "release_id": resolved_release_id,
                "current_dir": str(self.current_dir),
                "staging_dir": str(stage_dir),
                "manifest": manifest,
                "validation": activated_validation,
                "status_files": journal.note_paths(),
            }
            journal.event("deploy_completed", message="Deployment completed successfully.", manifest=manifest)
            journal.update(
                status="completed",
                step="live",
                ok=True,
                finished=True,
                metadata={
                    "manifest": manifest,
                    "current_dir": str(self.current_dir),
                },
                validation=activated_validation,
            )
            return result
        except Exception as exc:
            rollback_payload: Dict[str, Any] = {}
            if switched_live:
                try:
                    rollback_payload = self._restore_live_after_failure(had_live_before=had_live_before)
                except Exception as rollback_exc:  # noqa: BLE001
                    rollback_payload = {
                        "ok": False,
                        "error": f"{type(rollback_exc).__name__}: {rollback_exc}",
                    }
            journal.event(
                "deploy_failed",
                level="error",
                message=str(exc),
                rollback=rollback_payload,
            )
            journal.update(
                status="failed",
                step="failed",
                ok=False,
                finished=True,
                last_error={"type": type(exc).__name__, "message": str(exc)},
                rollback=rollback_payload,
            )
            raise
        finally:
            if stage_dir.exists():
                self._rmtree_with_retry(stage_dir)

    def deploy_release_archive(
        self,
        archive_bytes: bytes,
        *,
        release_id: str = "",
        deployment_id: str = "",
        expected_sha256: str = "",
    ) -> Dict[str, Any]:
        imported = self.import_release_archive(
            archive_bytes,
            release_id=release_id,
            expected_sha256=expected_sha256,
        )
        deployed = self.deploy_release(imported["release_id"], deployment_id=deployment_id)
        return {
            "imported": imported,
            "deployed": deployed,
        }

    def deploy_local_release(self, source_dir: Path, *, release_id: str = "", deployment_id: str = "") -> Dict[str, Any]:
        imported = self.import_local_release(source_dir, release_id=release_id)
        deployed = self.deploy_release(imported["release_id"], deployment_id=deployment_id)
        return {
            "imported": imported,
            "deployed": deployed,
        }

    def activate_release(self, release_id: str) -> Dict[str, Any]:
        deployed = self.deploy_release(str(release_id or "").strip())
        return {
            "release_id": deployed["release_id"],
            "deployment_id": deployed["deployment_id"],
            "current_dir": deployed["current_dir"],
            "manifest": deployed["manifest"],
            "validation": deployed["validation"],
            "status_files": deployed["status_files"],
        }

    def rollback(self) -> Dict[str, Any]:
        if not self.previous_dir.exists() or not any(self.previous_dir.iterdir()):
            raise FileNotFoundError("No previous release is available for rollback.")

        rollback_validation = self.validate_release_dir(self.previous_dir)
        if not rollback_validation["ok"]:
            raise ValueError(f"Previous release failed validation and cannot be restored: {rollback_validation}")

        current_manifest = self._manifest_from_dir(self.current_dir)
        current_release_id = str(current_manifest.get("release_id") or "").strip()
        rollback_id = deployment_id_for_release(current_release_id or "rollback", prefix="rollback")
        journal = self._journal(rollback_id)
        journal.initialize(
            role="cloud",
            release_id=current_release_id,
            metadata={
                "operation": "rollback",
                "current_dir": str(self.current_dir),
                "previous_dir": str(self.previous_dir),
                "history_dir": str(self.history_dir),
                "current_manifest": current_manifest,
                "previous_validation": rollback_validation,
            },
        )
        journal.event(
            "rollback_started",
            message="Starting rollback from previous release.",
            current_release_id=current_release_id,
            previous_release_id=str(rollback_validation.get("manifest", {}).get("release_id") or ""),
        )

        gc.collect()
        archive_dir: Path | None = None
        if self.current_dir.exists() and any(self.current_dir.iterdir()):
            archive_dir = self.history_dir / f"rolled_back_{_timestamp_slug()}"
            self._copytree_with_retry(self.current_dir, archive_dir, dirs_exist_ok=True)
            journal.event(
                "rollback_archived_current",
                message="Archived current live release before rollback.",
                archived_current_dir=str(archive_dir),
            )

        self._replace_directory(self.previous_dir, self.current_dir)
        manifest = self._manifest_from_dir(self.current_dir)
        validation = self.validate_release_dir(self.current_dir)
        result = {
            "release_id": str(manifest.get("release_id") or ""),
            "current_dir": str(self.current_dir),
            "manifest": manifest,
            "validation": validation,
            "mode": "rollback",
            "deployment_id": rollback_id,
            "status_files": journal.note_paths(),
            "rollback_evidence": {
                "archived_current_dir": str(archive_dir) if archive_dir is not None else "",
                "restored_from_previous_dir": str(self.previous_dir),
                "current_manifest_path": str(self.current_dir / "manifest.json"),
                "current_release_path": str(self.current_dir / "release.json"),
            },
        }
        journal.event(
            "rollback_completed",
            message="Rollback completed successfully.",
            rollback_evidence=result["rollback_evidence"],
            manifest=manifest,
        )
        journal.update(
            status="completed",
            step="rollback",
            ok=True,
            finished=True,
            validation=validation,
            metadata={
                "manifest": manifest,
                "rollback_evidence": result["rollback_evidence"],
            },
        )
        return result

    def _recent_deployments(self, *, limit: int = 10) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        if not self.state_dir.exists():
            return items
        for run_dir in sorted(self.state_dir.iterdir(), key=lambda path: path.name, reverse=True):
            if not run_dir.is_dir():
                continue
            status_path = run_dir / "status.json"
            if not status_path.exists():
                continue
            try:
                payload = read_json_utf8(status_path)
            except Exception:
                continue
            if isinstance(payload, dict):
                items.append(dict(payload))
            if len(items) >= limit:
                break
        return items

    def summary(self) -> dict[str, Any]:
        history = []
        if self.history_dir.exists():
            for item in sorted(self.history_dir.iterdir(), key=lambda path: path.name, reverse=True):
                if item.is_dir():
                    history.append(
                        {
                            "release_id": item.name,
                            "manifest": self._manifest_from_dir(item),
                        }
                    )
        staging_entries = []
        if self.staging_dir.exists():
            staging_entries = [item.name for item in sorted(self.staging_dir.iterdir(), key=lambda path: path.name) if item.is_dir()]
        return {
            "root": str(self.root),
            "current": self._manifest_from_dir(self.current_dir),
            "current_validation": self.validate_release_dir(self.current_dir) if self.current_dir.exists() and any(self.current_dir.iterdir()) else {},
            "previous": self._manifest_from_dir(self.previous_dir),
            "previous_validation": self.validate_release_dir(self.previous_dir) if self.previous_dir.exists() and any(self.previous_dir.iterdir()) else {},
            "history_count": len(history),
            "history": history[:10],
            "staging_dir": str(self.staging_dir),
            "staging_entries": staging_entries,
            "deployments_dir": str(self.state_dir),
            "recent_deployments": self._recent_deployments(limit=10),
            "uploads_dir": str(self.uploads_dir),
        }
