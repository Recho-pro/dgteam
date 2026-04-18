from __future__ import annotations

from pathlib import Path

import pytest

from dgteam.core.storage import DGTeamStorage
from dgteam.core.textio import read_json_utf8
from dgteam.release import builder


def _snapshot_row(price: int = 9600) -> dict[str, object]:
    return {
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
        "min_price": price,
        "max_price": price,
        "market_price": price,
        "price_range": f"{price}-{price}",
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


def _payload(run_key: str) -> dict[str, object]:
    return {
        "run_key": run_key,
        "built_at": "2026-04-17 10:00:00",
        "snapshot_rows": [_snapshot_row()],
        "cluster_rows": [],
        "summary": {"run_key": run_key, "counts": {"source_rows": 1}},
    }


def test_build_local_release_bundle_publishes_only_snapshot_copy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source_storage = DGTeamStorage(tmp_path / "source.db")
    source_storage.init_db()
    target = tmp_path / "release_current"

    monkeypatch.setattr(builder, "build_live_market_payload", lambda storage, run_key="": _payload(run_key or "atomic_run"))

    result = builder.build_local_release_bundle(source_storage, target, run_key="atomic_run")
    release_storage = DGTeamStorage(target / "dgteam.db")
    manifest = read_json_utf8(target / "manifest.json")
    release = read_json_utf8(target / "release.json")
    summary = read_json_utf8(target / "summary.json")
    asset_manifest = read_json_utf8(target / "query_ui" / "asset-manifest.json")

    assert result["release_id"] == "release_current"
    assert manifest["release_id"] == "release_current"
    assert source_storage.get_live_market_state()["run_key"] == ""
    assert release_storage.get_live_market_state()["run_key"] == "atomic_run"
    assert release["database"] == str((target / "dgteam.db").resolve())
    assert release["query_ui"]["asset_dir"] == str((target / "query_ui").resolve())
    assert release["query_ui"]["asset_manifest"]["version"] == asset_manifest["version"]
    assert asset_manifest["contract_version"] == "dgteam-query-ui-assets.v1"
    assert asset_manifest["release_lifecycle"] == "current_previous_rollback"
    assert (target / "query_ui" / "index.html").is_file()
    assert (target / "query_ui" / "app.js").is_file()
    assert (target / "query_ui" / "styles.css").is_file()
    manifest_files = {item["path"].replace("\\", "/") for item in manifest["files"]}
    assert {
        "query_ui/index.html",
        "query_ui/app.js",
        "query_ui/styles.css",
        "query_ui/asset-manifest.json",
    }.issubset(manifest_files)
    assert summary["outputs"]["snapshot_csv"] == str((target / "market_v1_snapshot.csv").resolve())
    assert not list(tmp_path.glob(".release_current.building_*"))


def test_build_local_release_bundle_preserves_existing_target_when_build_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_storage = DGTeamStorage(tmp_path / "source.db")
    source_storage.init_db()
    target = tmp_path / "release_current"
    target.mkdir()
    (target / "marker.txt").write_text("old release", encoding="utf-8")

    monkeypatch.setattr(builder, "build_live_market_payload", lambda storage, run_key="": _payload(run_key or "atomic_run"))

    def fail_export(payload: dict[str, object], outdir: Path, *, public_outdir: Path | None = None) -> dict[str, object]:
        Path(outdir, "partial.txt").write_text("partial", encoding="utf-8")
        raise RuntimeError("simulated export failure")

    monkeypatch.setattr(builder, "export_live_market_payload", fail_export)

    with pytest.raises(RuntimeError, match="simulated export failure"):
        builder.build_local_release_bundle(source_storage, target, run_key="atomic_run")

    assert (target / "marker.txt").read_text(encoding="utf-8") == "old release"
    assert not (target / "partial.txt").exists()
    assert not list(tmp_path.glob(".release_current.building_*"))
