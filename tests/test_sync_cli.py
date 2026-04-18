import json
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from dgteam.core.config import load_settings
from dgteam.core.storage import DGTeamStorage
from dgteam.core.textio import read_json_utf8, write_json_utf8
from dgteam.publish_api.app import PublishApiHandler
from dgteam.publish_api.release_store import ReleaseStore
from dgteam.release.builder import build_release_manifest
from dgteam.release.sync_cli import sync_release


def _create_release_dir(release_dir: Path, *, run_key: str, market_price: int) -> None:
    release_dir.mkdir(parents=True, exist_ok=True)
    working_db = release_dir / "_working_source.db"
    storage = DGTeamStorage(working_db)
    storage.init_db()
    storage.upsert_run(
        run_key,
        source_dir=release_dir,
        summary_json=json.dumps({"run_key": run_key, "counts": {"source_rows": 1}}, ensure_ascii=False),
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
    write_json_utf8(
        release_dir / "release.json",
        {"release_id": release_dir.name, "run_key": run_key, "market_price": market_price},
    )
    write_json_utf8(release_dir / "summary.json", {"run_key": run_key, "market_price": market_price})
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


def _build_store(tmp_path: Path) -> tuple[ReleaseStore, object]:
    settings = load_settings(project_root=tmp_path)
    store = ReleaseStore(
        tmp_path / "cloud",
        current_dir=settings.release.current_dir,
        previous_dir=settings.release.previous_dir,
        history_dir=settings.release.history_dir,
        staging_dir=settings.release.staging_dir,
        state_dir=settings.release.state_dir,
        uploads_dir=settings.publish_api.uploads_dir,
    )
    return store, settings


def test_sync_release_uploads_existing_release_dir(tmp_path: Path):
    release_dir = tmp_path / "release_sync"
    _create_release_dir(release_dir, run_key="demo_2026-04-16", market_price=9600)

    store, settings = _build_store(tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), PublishApiHandler)
    server.settings = settings  # type: ignore[attr-defined]
    server.release_store = store  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        response = sync_release(
            server_url=f"http://127.0.0.1:{server.server_address[1]}",
            release_dir=release_dir,
            release_id="release_synced",
            project_root=tmp_path,
            skip_encoding_check=True,
            activate=True,
        )
        assert response["ok"] is True
        assert response["archive"]["temporary"] is True
        assert response["archive"]["removed_after_upload"] is True
        assert response["upload"]["ok"] is True
        assert response["upload"]["flow"] == "upload_then_activate"
        assert response["upload"]["imported"]["release_id"] == "release_synced"
        assert response["upload"]["activated"]["release_id"] == "release_synced"
        assert response["upload"]["deployed"]["release_id"] == "release_synced"
        local_status = read_json_utf8(Path(response["status_files"]["status_path"]))
        cloud_status = read_json_utf8(Path(response["upload"]["deployed"]["status_files"]["status_path"]))
        assert local_status["status"] == "completed"
        assert cloud_status["status"] == "completed"
        assert store.summary()["current"]["release_id"] == "release_synced"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        time.sleep(0.1)


def test_deploy_release_rolls_back_when_post_switch_validation_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store, _ = _build_store(tmp_path)
    old_release = tmp_path / "release_old"
    new_release = tmp_path / "release_new"
    _create_release_dir(old_release, run_key="old_2026-04-15", market_price=9500)
    _create_release_dir(new_release, run_key="new_2026-04-16", market_price=9700)

    store.import_local_release(old_release, release_id="release_old")
    store.deploy_release("release_old", deployment_id="deploy_release_old")
    store.import_local_release(new_release, release_id="release_new")

    original_validate = store.validate_release_dir

    def _flaky_validate(directory: Path):
        result = original_validate(directory)
        target = Path(directory).expanduser().resolve()
        if target == store.current_dir.resolve() and result.get("manifest", {}).get("release_id") == "release_new":
            broken = dict(result)
            broken["ok"] = False
            broken["health"] = {"ok": False, "reason": "synthetic post-switch failure"}
            return broken
        return result

    monkeypatch.setattr(store, "validate_release_dir", _flaky_validate)

    with pytest.raises(ValueError):
        store.deploy_release("release_new", deployment_id="deploy_release_new")

    current_manifest = store._manifest_from_dir(store.current_dir)
    deployment_status = read_json_utf8(store.state_dir / "deploy_release_new" / "status.json")
    assert current_manifest["release_id"] == "release_old"
    assert deployment_status["status"] == "failed"
    assert deployment_status["rollback"]["mode"] == "rollback"
