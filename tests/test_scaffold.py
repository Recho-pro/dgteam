import io
import gc
import json
import sqlite3
import threading
import time
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

from dgteam.agent.pipeline import run_pipeline
from dgteam.core.config import load_settings
from dgteam.core.storage import DGTeamStorage
from dgteam.core.textio import write_json_utf8
from dgteam.publish_api.app import PublishApiHandler
from dgteam.publish_api.release_store import ReleaseStore
from dgteam.release.builder import archive_release_bundle, build_release_manifest
from dgteam.release.upload_client import upload_release_bundle


def test_settings_project_root_exists():
    settings = load_settings()
    assert settings.project_root.exists()


def test_release_manifest_write(tmp_path: Path):
    manifest = build_release_manifest(tmp_path / "release_001", run_key="demo")
    assert manifest.run_key == "demo"
    assert (tmp_path / "release_001" / "manifest.json").exists()


def _create_valid_release_dir(release_dir: Path, *, run_key: str = "demo", market_price: int = 9600) -> None:
    release_dir.mkdir(parents=True, exist_ok=True)
    working_db = release_dir / "_working_source.db"
    storage = DGTeamStorage(working_db)
    storage.init_db()
    storage.upsert_run(
        run_key,
        source_dir=release_dir,
        summary_json=json.dumps({"run_key": run_key}, ensure_ascii=False),
        status="completed",
        started_at="2026-04-16 10:00:00",
        finished_at="2026-04-16 10:05:00",
    )
    storage.publish_market_snapshots(
        run_key,
        [
            {
                "brand_title": "苹果",
                "series_title": "iPhone 17",
                "model_title": "17 Pro Max 6.9寸 国行",
                "group_title": "256G星宇橙色",
                "condition_bucket": "apple_company_pure_sealed_target",
                "selected_gprice_label": "04-16",
                "selected_gprice_labels": "04-16",
                "latest_gprice": "04-16",
                "latest_imported_at": "2026-04-16 10:05:00",
                "source_row_count": 1,
                "source_count": 1,
                "min_price": market_price,
                "max_price": market_price,
                "market_price": market_price,
                "price_range": f"{market_price}-{market_price}",
                "trusted_status": "公司纯原封",
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
                "search_text": "苹果 iPhone 17 17 Pro Max 6.9寸 国行 256G星宇橙色",
                "search_text_normalized": "苹果iphone1717promax69寸国行256g星宇橙色",
                "model_group_normalized": "17promax69寸国行256g星宇橙色",
            }
        ],
        summary={"counts": {"source_rows": 1}},
        published_at="2026-04-16 10:05:00",
    )
    storage.export_database_snapshot(release_dir / "dgteam.db")
    write_json_utf8(release_dir / "release.json", {"release_id": release_dir.name, "run_key": run_key})
    write_json_utf8(release_dir / "summary.json", {"run_key": run_key})
    (release_dir / "market_v1_snapshot.csv").write_text("demo\n", encoding="utf-8")
    (release_dir / "market_v1_clusters.csv").write_text("demo\n", encoding="utf-8")
    build_release_manifest(
        release_dir,
        run_key=run_key,
        quote_count=1,
        snapshot_count=1,
        files=(
            release_dir / "release.json",
            release_dir / "summary.json",
            release_dir / "market_v1_snapshot.csv",
            release_dir / "market_v1_clusters.csv",
            release_dir / "dgteam.db",
        ),
    )


def test_release_store_summary():
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        store = ReleaseStore(
            root,
            current_dir=root / "current",
            previous_dir=root / "previous",
            history_dir=root / "history",
            staging_dir=root / "staging",
            state_dir=root / "deployments",
        )
        summary = store.summary()
        assert summary["history_count"] == 0


def test_release_store_import_archive(tmp_path: Path):
    release_dir = tmp_path / "release_001"
    _create_valid_release_dir(release_dir, run_key="demo_import")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for item in release_dir.iterdir():
            if item.is_file() and item.name != "_working_source.db":
                archive.write(item, arcname=item.name)
    store = ReleaseStore(
        tmp_path / "cloud",
        current_dir=tmp_path / "cloud" / "current",
        previous_dir=tmp_path / "cloud" / "previous",
        history_dir=tmp_path / "cloud" / "history",
        staging_dir=tmp_path / "cloud" / "staging",
        state_dir=tmp_path / "cloud" / "deployments",
        uploads_dir=tmp_path / "cloud" / "uploads",
    )
    imported = store.import_release_archive(buffer.getvalue(), release_id="release_imported")
    assert imported["release_id"] == "release_imported"


def test_upload_client_round_trip(tmp_path: Path):
    release_dir = tmp_path / "release_002"
    _create_valid_release_dir(release_dir, run_key="demo_upload")
    archive = archive_release_bundle(release_dir, tmp_path / "release_002.zip")

    settings = load_settings(project_root=tmp_path)
    store = ReleaseStore(
        tmp_path / "cloud",
        current_dir=tmp_path / "cloud" / "current",
        previous_dir=tmp_path / "cloud" / "previous",
        history_dir=tmp_path / "cloud" / "history",
        staging_dir=tmp_path / "cloud" / "staging",
        state_dir=tmp_path / "cloud" / "deployments",
        uploads_dir=tmp_path / "cloud" / "uploads",
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), PublishApiHandler)
    server.settings = settings  # type: ignore[attr-defined]
    server.release_store = store  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        response = upload_release_bundle(
            server_url=f"http://127.0.0.1:{server.server_address[1]}",
            archive_path=Path(archive["archive_path"]),
            release_id="release_uploaded",
            activate=True,
        )
        assert response["ok"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        server.release_store = None  # type: ignore[attr-defined]
        server.settings = None  # type: ignore[attr-defined]
        gc.collect()
        time.sleep(0.1)


def test_pipeline_dry_run_has_stage_records():
    result = run_pipeline("dry-run")
    assert isinstance(result.stages, list)
