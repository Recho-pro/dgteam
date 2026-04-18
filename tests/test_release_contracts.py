from __future__ import annotations

import json
import os
import shutil
import sqlite3
from pathlib import Path

import pytest

from dgteam.core.config import load_settings
from dgteam.core.storage import DGTeamStorage
from dgteam.core.textio import read_json_utf8, write_json_utf8
from dgteam.publish_api.release_store import ReleaseStore
from dgteam.query_api.ui_assets import package_query_ui_assets
from dgteam.release.builder import archive_release_bundle, build_release_manifest
from dgteam.release.retention import prune_cloud_runtime, prune_local_runtime


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_EVIDENCE_CANDIDATES = (
    PROJECT_ROOT / "runtime" / "local" / "automation" / "prod" / "state" / "last_run.json",
    PROJECT_ROOT / "runtime" / "local" / "automation" / "prod" / "logs" / "collect_and_sync_20260416_220002.log",
)
DEPLOYMENT_TEMPLATE_FILES = (
    PROJECT_ROOT / "README.md",
    PROJECT_ROOT / "docs" / "PRODUCTION_DEPLOYMENT.md",
    PROJECT_ROOT / "deploy" / "linux" / "systemd" / "dgteam-publish.service",
    PROJECT_ROOT / "deploy" / "linux" / "bin" / "dgteam-backup.sh",
)


def _create_valid_release_dir(
    release_dir: Path,
    *,
    run_key: str,
    market_price: int = 9600,
    ui_marker: str = "",
    include_query_ui: bool = True,
) -> None:
    release_dir.mkdir(parents=True, exist_ok=True)
    working_db = release_dir / "_working_source.db"
    storage = DGTeamStorage(working_db)
    storage.init_db()
    storage.upsert_run(
        run_key,
        source_dir=release_dir,
        summary_json=json.dumps({"run_key": run_key, "counts": {"source_rows": 1}}),
        status="completed",
        started_at="2026-04-17 09:55:00",
        finished_at="2026-04-17 10:00:00",
    )
    storage.publish_market_snapshots(
        run_key,
        [
            {
                "brand_title": "BrandA",
                "series_title": "SeriesA",
                "model_title": "ModelA",
                "group_title": "128g",
                "condition_bucket": "standard",
                "selected_gprice_label": "04-17",
                "selected_gprice_labels": "04-17",
                "latest_gprice": "04-17",
                "latest_imported_at": "2026-04-17 10:00:00",
                "source_row_count": 1,
                "source_count": 1,
                "min_price": market_price,
                "max_price": market_price,
                "market_price": market_price,
                "price_range": f"{market_price}-{market_price}",
                "trusted_status": "trusted",
                "trusted_sample_count": 1,
                "trusted_seller_count": 1,
                "confidence_score": 80,
                "confidence_label": "high",
                "reference_price": 0,
                "reference_source_name": "",
                "reference_sheet_name": "",
                "reference_fetched_at": "",
                "suspicious_low_cluster_count": 0,
                "suspicious_low_row_count": 0,
                "suspicious_high_cluster_count": 0,
                "suspicious_high_row_count": 0,
                "cluster_count": 1,
                "search_text": "BrandA SeriesA ModelA 128g",
                "search_text_normalized": "brandaseriesamodela128g",
                "model_group_normalized": "modela128g",
            }
        ],
        summary={"counts": {"source_rows": 1}},
        published_at="2026-04-17 10:00:00",
    )
    storage.export_database_snapshot(release_dir / "dgteam.db")
    write_json_utf8(release_dir / "summary.json", {"run_key": run_key})
    (release_dir / "market_v1_snapshot.csv").write_text("snapshot\n", encoding="utf-8")
    (release_dir / "market_v1_clusters.csv").write_text("cluster\n", encoding="utf-8")
    release_metadata = {
        "release_id": release_dir.name,
        "run_key": run_key,
    }
    ui_files: list[Path] = []
    if include_query_ui:
        marker = ui_marker or run_key
        ui_source_dir = release_dir / "_query_ui_source"
        ui_source_dir.mkdir(parents=True, exist_ok=True)
        (ui_source_dir / "index.html").write_text(
            (
                '<html><head><meta name="dgteam-asset-version" content="__DGTEAM_ASSET_VERSION__">'
                '<link rel="stylesheet" href="__DGTEAM_STYLES_HREF__"></head>'
                f"<body><main>{marker}</main><script src=\"__DGTEAM_APP_HREF__\"></script></body></html>"
            ),
            encoding="utf-8",
        )
        (ui_source_dir / "app.js").write_text(f"window.DGTEAM_UI_MARKER = {json.dumps(marker)};\n", encoding="utf-8")
        (ui_source_dir / "styles.css").write_text(f"body::before {{ content: {json.dumps(marker)}; }}\n", encoding="utf-8")
        ui_manifest = package_query_ui_assets(release_dir / "query_ui", source_dir=ui_source_dir)
        shutil.rmtree(ui_source_dir)
        release_metadata["query_ui"] = {
            "asset_dir": str(release_dir / "query_ui"),
            "asset_manifest": ui_manifest,
        }
        ui_files = sorted(path for path in (release_dir / "query_ui").rglob("*") if path.is_file())
    write_json_utf8(release_dir / "release.json", release_metadata)
    build_release_manifest(
        release_dir,
        run_key=run_key,
        quote_count=0,
        snapshot_count=1,
        files=(
            release_dir / "release.json",
            release_dir / "summary.json",
            release_dir / "market_v1_snapshot.csv",
            release_dir / "market_v1_clusters.csv",
            release_dir / "dgteam.db",
            *ui_files,
        ),
    )


def _build_store(tmp_path: Path) -> ReleaseStore:
    settings = load_settings(project_root=tmp_path)
    return ReleaseStore(
        tmp_path / "cloud",
        current_dir=settings.release.current_dir,
        previous_dir=settings.release.previous_dir,
        history_dir=settings.release.history_dir,
        staging_dir=settings.release.staging_dir,
        state_dir=settings.release.state_dir,
        uploads_dir=settings.publish_api.uploads_dir,
    )


def _set_mtime(path: Path, timestamp: int) -> None:
    os.utime(path, (timestamp, timestamp))


def test_validate_release_dir_requires_manifest_run_key(tmp_path: Path) -> None:
    release_dir = tmp_path / "release_missing_run_key"
    _create_valid_release_dir(release_dir, run_key="manifest_2026-04-17")
    manifest = read_json_utf8(release_dir / "manifest.json")
    manifest["run_key"] = ""
    write_json_utf8(release_dir / "manifest.json", manifest)

    store = _build_store(tmp_path)
    validation = store.validate_release_dir(release_dir)

    assert validation["ok"] is False
    assert validation["manifest"]["run_key"] == ""
    assert validation["missing_files"] == []
    assert validation["db_quick_check"] == "ok"
    assert validation["health"]["ok"] is True


def test_validate_release_dir_rejects_missing_required_index(tmp_path: Path) -> None:
    release_dir = tmp_path / "release_missing_index"
    _create_valid_release_dir(release_dir, run_key="index_2026-04-17")

    with sqlite3.connect(release_dir / "dgteam.db") as conn:
        conn.execute("DROP INDEX idx_market_snapshots_run_query")

    store = _build_store(tmp_path)
    validation = store.validate_release_dir(release_dir)

    assert validation["ok"] is False
    assert "idx_market_snapshots_run_query" in validation["missing_indexes"]
    assert validation["missing_tables"] == []


def test_deploy_summary_tracks_directory_lifecycle_and_cleans_staging(tmp_path: Path) -> None:
    store = _build_store(tmp_path)
    old_release = tmp_path / "release_old"
    new_release = tmp_path / "release_new"
    _create_valid_release_dir(old_release, run_key="old_2026-04-16", market_price=9500)
    _create_valid_release_dir(new_release, run_key="new_2026-04-17", market_price=9700)

    imported_old = store.import_local_release(old_release, release_id="release_old")
    deployed_old = store.deploy_release("release_old", deployment_id="deploy_release_old")
    imported_new = store.import_local_release(new_release, release_id="release_new")
    deployed_new = store.deploy_release("release_new", deployment_id="deploy_release_new")
    summary = store.summary()
    deployment_status = read_json_utf8(store.state_dir / "deploy_release_new" / "status.json")

    assert imported_old["manifest"]["release_id"] == "release_old"
    assert deployed_old["validation"]["ok"] is True
    assert imported_new["manifest"]["release_id"] == "release_new"
    assert deployed_new["validation"]["ok"] is True
    assert summary["current"]["release_id"] == "release_new"
    assert summary["previous"]["release_id"] == "release_old"
    assert summary["current_validation"]["ok"] is True
    assert summary["previous_validation"]["ok"] is True
    assert summary["history_count"] == 2
    assert summary["staging_entries"] == []
    recent_by_id = {item["deployment_id"]: item for item in summary["recent_deployments"]}
    assert recent_by_id["deploy_release_new"]["status"] == "completed"
    assert deployment_status["status"] == "completed"
    assert deployment_status["step"] == "live"
    assert deployment_status["validation"]["ok"] is True
    assert not (store.staging_dir / "deploy_release_new").exists()


def test_release_archive_import_cleans_extraction_workspace_and_direct_deploys(tmp_path: Path) -> None:
    store = _build_store(tmp_path)
    release_dir = tmp_path / "release_archive"
    _create_valid_release_dir(release_dir, run_key="archive_2026-04-17", market_price=9600)
    archive = archive_release_bundle(release_dir, tmp_path / "release_archive.zip")

    imported = store.import_release_archive(
        Path(archive["archive_path"]).read_bytes(),
        release_id="release_archive",
    )
    deployed = store.deploy_release("release_archive", deployment_id="deploy_release_archive")

    assert imported["release_id"] == "release_archive"
    assert (store.uploads_dir / "release_archive.zip").is_file()
    assert not (store.uploads_dir / "release_archive_unzipped").exists()
    assert deployed["validation"]["ok"] is True
    assert not (store.staging_dir / "deploy_release_archive").exists()

    direct_release_dir = tmp_path / "release_direct"
    _create_valid_release_dir(direct_release_dir, run_key="direct_2026-04-17", market_price=9700)
    direct_archive = archive_release_bundle(direct_release_dir, tmp_path / "release_direct.zip")
    direct = store.deploy_release_archive(
        Path(direct_archive["archive_path"]).read_bytes(),
        release_id="release_direct",
        deployment_id="deploy_release_direct",
    )
    summary = store.summary()

    assert direct["deployed"]["validation"]["ok"] is True
    assert direct["deployed"]["release_id"] == "release_direct"
    assert summary["current"]["release_id"] == "release_direct"
    assert summary["previous"]["release_id"] == "release_archive"
    assert not (store.uploads_dir / "release_direct_unzipped").exists()


def test_rollback_restores_previous_and_archives_current_release(tmp_path: Path) -> None:
    store = _build_store(tmp_path)
    old_release = tmp_path / "release_old"
    new_release = tmp_path / "release_new"
    _create_valid_release_dir(old_release, run_key="old_2026-04-16", market_price=9500)
    _create_valid_release_dir(new_release, run_key="new_2026-04-17", market_price=9700)

    store.import_local_release(old_release, release_id="release_old")
    store.deploy_release("release_old", deployment_id="deploy_release_old")
    store.import_local_release(new_release, release_id="release_new")
    store.deploy_release("release_new", deployment_id="deploy_release_new")

    rolled_back = store.rollback()
    summary = store.summary()
    rolled_back_dirs = [item for item in store.history_dir.iterdir() if item.is_dir() and item.name.startswith("rolled_back_")]
    assert len(rolled_back_dirs) == 1

    archived_manifest = read_json_utf8(rolled_back_dirs[0] / "manifest.json")
    assert rolled_back["mode"] == "rollback"
    assert rolled_back["release_id"] == "release_old"
    assert rolled_back["validation"]["ok"] is True
    assert rolled_back["deployment_id"].startswith("rollback_")
    assert Path(rolled_back["status_files"]["status_path"]).is_file()
    assert Path(rolled_back["status_files"]["events_path"]).is_file()
    assert Path(rolled_back["rollback_evidence"]["archived_current_dir"]).is_dir()
    assert rolled_back["rollback_evidence"]["restored_from_previous_dir"] == str(store.previous_dir)
    assert archived_manifest["release_id"] == "release_new"
    assert summary["current"]["release_id"] == "release_old"
    assert summary["history_count"] == 3


def test_deploy_and_rollback_switch_query_ui_assets_with_release(tmp_path: Path) -> None:
    store = _build_store(tmp_path)
    old_release = tmp_path / "release_old"
    new_release = tmp_path / "release_new"
    _create_valid_release_dir(old_release, run_key="old_2026-04-16", market_price=9500, ui_marker="old-ui")
    _create_valid_release_dir(new_release, run_key="new_2026-04-17", market_price=9700, ui_marker="new-ui")

    store.import_local_release(old_release, release_id="release_old")
    store.deploy_release("release_old", deployment_id="deploy_release_old")
    old_version = read_json_utf8(store.current_dir / "query_ui" / "asset-manifest.json")["version"]

    store.import_local_release(new_release, release_id="release_new")
    deployed_new = store.deploy_release("release_new", deployment_id="deploy_release_new")
    new_version = read_json_utf8(store.current_dir / "query_ui" / "asset-manifest.json")["version"]

    assert deployed_new["validation"]["query_ui"]["ok"] is True
    assert deployed_new["validation"]["query_ui"]["required"] is True
    assert new_version != old_version
    assert "new-ui" in (store.current_dir / "query_ui" / "app.js").read_text(encoding="utf-8")
    assert "old-ui" in (store.previous_dir / "query_ui" / "app.js").read_text(encoding="utf-8")

    rolled_back = store.rollback()
    archived_current = Path(rolled_back["rollback_evidence"]["archived_current_dir"])

    assert rolled_back["validation"]["query_ui"]["ok"] is True
    assert read_json_utf8(store.current_dir / "query_ui" / "asset-manifest.json")["version"] == old_version
    assert "old-ui" in (store.current_dir / "query_ui" / "app.js").read_text(encoding="utf-8")
    assert "new-ui" in (archived_current / "query_ui" / "app.js").read_text(encoding="utf-8")


def test_standard_backfill_promotes_legacy_current_into_standard_current_previous_and_rollback(tmp_path: Path) -> None:
    store = _build_store(tmp_path)
    legacy_release = tmp_path / "release_legacy"
    standard_a = tmp_path / "release_standard_a"
    standard_b = tmp_path / "release_standard_b"
    _create_valid_release_dir(legacy_release, run_key="legacy_2026-04-16", market_price=9500, include_query_ui=False)
    _create_valid_release_dir(standard_a, run_key="legacy_2026-04-16", market_price=9500, ui_marker="standard-a")
    _create_valid_release_dir(standard_b, run_key="legacy_2026-04-16", market_price=9500, ui_marker="standard-b")

    store.import_local_release(legacy_release, release_id="release_legacy")
    store.deploy_release("release_legacy", deployment_id="deploy_release_legacy")
    legacy_summary = store.summary()

    assert legacy_summary["current"]["release_id"] == "release_legacy"
    assert legacy_summary["current_validation"]["query_ui"]["required"] is False

    store.import_local_release(standard_a, release_id="release_standard_a")
    store.deploy_release("release_standard_a", deployment_id="deploy_release_standard_a")
    first_standard_summary = store.summary()

    assert first_standard_summary["current"]["release_id"] == "release_standard_a"
    assert first_standard_summary["current_validation"]["query_ui"]["ok"] is True
    assert first_standard_summary["previous"]["release_id"] == "release_legacy"
    assert first_standard_summary["previous_validation"]["query_ui"]["required"] is False

    store.import_local_release(standard_b, release_id="release_standard_b")
    store.deploy_release("release_standard_b", deployment_id="deploy_release_standard_b")
    second_standard_summary = store.summary()

    assert second_standard_summary["current"]["release_id"] == "release_standard_b"
    assert second_standard_summary["current_validation"]["query_ui"]["ok"] is True
    assert second_standard_summary["previous"]["release_id"] == "release_standard_a"
    assert second_standard_summary["previous_validation"]["query_ui"]["ok"] is True

    rolled_back = store.rollback()
    rollback_summary = store.summary()
    archived_current = Path(rolled_back["rollback_evidence"]["archived_current_dir"])

    assert rolled_back["release_id"] == "release_standard_a"
    assert rolled_back["validation"]["query_ui"]["ok"] is True
    assert rollback_summary["current"]["release_id"] == "release_standard_a"
    assert rollback_summary["current_validation"]["query_ui"]["ok"] is True
    assert rollback_summary["previous"]["release_id"] == "release_standard_a"
    assert rollback_summary["previous_validation"]["query_ui"]["ok"] is True
    assert read_json_utf8(rollback_summary["current_validation"]["query_ui"]["asset_dir"] + "/asset-manifest.json")["version"]
    assert "standard-b" in (archived_current / "query_ui" / "app.js").read_text(encoding="utf-8")


def test_deployment_templates_and_runtime_evidence_are_aligned_to_srv() -> None:
    template_texts = [path.read_text(encoding="utf-8") for path in DEPLOYMENT_TEMPLATE_FILES]
    assert all("/srv/dgteam" in text for text in template_texts)
    assert all("/opt/dgteam/current" not in text for text in template_texts)
    assert any("/srv/dgteam/.env" in text for text in template_texts)
    production_doc = (PROJECT_ROOT / "docs" / "PRODUCTION_DEPLOYMENT.md").read_text(encoding="utf-8")
    assert "dgteam-query.service" in production_doc
    assert "dgteam-publish.service" in production_doc
    assert "dgteam-query-api.service" not in production_doc
    assert "dgteam-publish-api.service" not in production_doc

    runtime_evidence_path = next((path for path in RUNTIME_EVIDENCE_CANDIDATES if path.exists()), None)
    if runtime_evidence_path is None:
        pytest.skip("Runtime production evidence is not available in this checkout.")

    runtime_evidence = runtime_evidence_path.read_text(encoding="utf-8")
    assert "/srv/dgteam/runtime/cloud/current" in runtime_evidence
    assert "/srv/dgteam/runtime/cloud/deployments" in runtime_evidence


def test_publish_api_release_endpoints_are_documented() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    production_doc = (PROJECT_ROOT / "docs" / "PRODUCTION_DEPLOYMENT.md").read_text(encoding="utf-8")

    for endpoint in (
        "/api/releases/deploy",
        "/api/releases/upload",
        "/api/releases/activate",
        "/api/deployments/status",
    ):
        assert endpoint in readme
        assert endpoint in production_doc
    assert "your-server:8865" not in readme
    assert "127.0.0.1:9865" in readme


def test_prune_cloud_runtime_keeps_latest_release_and_rollback_skeleton(tmp_path: Path) -> None:
    history_dir = tmp_path / "history"
    uploads_dir = tmp_path / "uploads"
    history_dir.mkdir(parents=True, exist_ok=True)
    uploads_dir.mkdir(parents=True, exist_ok=True)

    release_old = history_dir / "release_old"
    release_new = history_dir / "release_new"
    rollback_old = history_dir / "rolled_back_2026-04-16T22-00-00"
    rollback_new = history_dir / "rolled_back_2026-04-17T22-00-00"
    for target in (release_old, release_new, rollback_old, rollback_new):
        target.mkdir(parents=True, exist_ok=True)
        (target / "marker.txt").write_text(target.name, encoding="utf-8")

    upload_zip = uploads_dir / "release_new.zip"
    upload_zip.write_text("zip", encoding="utf-8")

    _set_mtime(release_old, 100)
    _set_mtime(release_new, 200)
    _set_mtime(rollback_old, 300)
    _set_mtime(rollback_new, 400)
    _set_mtime(upload_zip, 500)

    summary = prune_cloud_runtime(
        history_dir=history_dir,
        uploads_dir=uploads_dir,
        keep_release_dirs=1,
        keep_rollback_dirs=1,
        clear_uploads=True,
    )

    removed_paths = {Path(item["path"]).name for item in summary["removed"]}
    assert summary["scope"] == "cloud"
    assert "release_old" in removed_paths
    assert "rolled_back_2026-04-16T22-00-00" in removed_paths
    assert "release_new.zip" in removed_paths
    assert release_new.exists()
    assert rollback_new.exists()


def test_prune_local_runtime_keeps_latest_release_archive_and_smoke_run_skeleton(tmp_path: Path) -> None:
    releases_dir = tmp_path / "releases"
    smoke_dir = tmp_path / "integration_smoke"
    releases_dir.mkdir(parents=True, exist_ok=True)
    smoke_dir.mkdir(parents=True, exist_ok=True)

    release_old = releases_dir / "release_old"
    release_new = releases_dir / "release_new"
    archive_old = releases_dir / "release_old.zip"
    archive_new = releases_dir / "release_new.zip"
    smoke_old = smoke_dir / "run_old"
    smoke_new = smoke_dir / "run_new"
    for target in (release_old, release_new, smoke_old, smoke_new):
        target.mkdir(parents=True, exist_ok=True)
        (target / "marker.txt").write_text(target.name, encoding="utf-8")
    for target in (archive_old, archive_new):
        target.write_text(target.name, encoding="utf-8")

    _set_mtime(release_old, 100)
    _set_mtime(release_new, 200)
    _set_mtime(archive_old, 300)
    _set_mtime(archive_new, 400)
    _set_mtime(smoke_old, 500)
    _set_mtime(smoke_new, 600)

    summary = prune_local_runtime(
        releases_dir=releases_dir,
        integration_smoke_dir=smoke_dir,
        keep_release_dirs=1,
        keep_release_archives=1,
        keep_integration_smoke_runs=1,
    )

    removed_paths = {Path(item["path"]).name for item in summary["removed"]}
    assert summary["scope"] == "local"
    assert "release_old" in removed_paths
    assert "release_old.zip" in removed_paths
    assert "run_old" in removed_paths
    assert release_new.exists()
    assert archive_new.exists()
    assert smoke_new.exists()
