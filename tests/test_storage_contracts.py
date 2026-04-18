from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from dgteam.core import storage_publish, storage_query, storage_schema, storage_state
from dgteam.core.storage import DGTeamStorage


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DB_PATHS = (
    PROJECT_ROOT / "runtime" / "local" / "data" / "dgteam.db",
    PROJECT_ROOT / "runtime" / "cloud" / "current" / "dgteam.db",
)


def _table_columns(db_path: Path, table_name: str) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}


def _index_names(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'").fetchall()
    return {str(row[0]) for row in rows}


def _index_columns(db_path: Path, index_name: str) -> list[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(f"PRAGMA index_info({index_name})").fetchall()
    return [str(row[2]) for row in rows]


def _sample_task(task_key: str, *, status: str = "pending", row_count: int = 0) -> dict[str, object]:
    return {
        "key": task_key,
        "time": "2026-04-17 10:00:00",
        "brand_title": "BrandA",
        "series_title": "SeriesA",
        "model_title": "ModelA",
        "city_title": "Shanghai",
        "status": status,
        "code": "0",
        "msg": "ok",
        "error": "",
        "total_rows_seen": row_count,
        "row_count": row_count,
        "drop_by_date": 0,
        "drop_out_of_stock": 0,
        "drop_by_apple_dstatus": 0,
        "drop_by_non_apple_dstatus": 0,
        "drop_invalid_price": 0,
    }


def _sample_quote_row(*, model_id: str, city_id: str, price_text: str, task_key: str = "") -> dict[str, object]:
    return {
        "task_key": task_key,
        "brand_id": "brand-a",
        "brand_title": "BrandA",
        "series_id": "series-a",
        "series_title": "SeriesA",
        "model_id": model_id,
        "model_title": "ModelA",
        "city_id": city_id,
        "city_title": "Shanghai",
        "group_title": None,
        "GID": "",
        "SID": "",
        "CID": "",
        "SNo": "SellerA",
        "SName": "SellerA",
        "cityName": "Shanghai",
        "activation": "",
        "dstatus": "onsale",
        "GPrice": "04-17",
        "GPriceTwo": "",
        "price_text": price_text,
        "price_image_file": "",
        "clean_scope": "core",
        "condition_bucket": None,
        "is_target_price": 1,
        "needs_review": 0,
        "matched_positive_tags": "",
        "matched_negative_tags": "",
        "matched_sale_tags": "",
        "exclude_reason": "",
        "rule_note": "",
    }


def _sample_snapshot(*, group_title: str, market_price: int, published_at: str) -> dict[str, object]:
    return {
        "brand_title": "BrandA",
        "series_title": "SeriesA",
        "model_title": "ModelA",
        "group_title": group_title,
        "condition_bucket": "standard",
        "selected_gprice_label": "04-17",
        "selected_gprice_labels": "04-17",
        "latest_gprice": "04-17",
        "latest_imported_at": published_at,
        "source_row_count": 2,
        "source_count": 2,
        "min_price": market_price,
        "max_price": market_price,
        "market_price": market_price,
        "price_range": f"{market_price}-{market_price}",
        "trusted_status": "trusted",
        "trusted_sample_count": 2,
        "trusted_seller_count": 2,
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
        "search_text": f"BrandA SeriesA ModelA {group_title}".strip(),
        "search_text_normalized": f"brandaseriesamodela{group_title}".lower(),
        "model_group_normalized": f"modela{group_title}".lower(),
    }


def test_init_db_exposes_core_schema_and_hot_query_indexes(tmp_path: Path) -> None:
    db_path = tmp_path / "contract.db"
    storage = DGTeamStorage(db_path)
    storage.init_db()

    for table_name, expected_columns in storage_schema.CORE_TABLE_COLUMNS.items():
        assert expected_columns.issubset(_table_columns(db_path, table_name))

    index_names = _index_names(db_path)
    for index_name, expected_columns in storage_schema.HOT_QUERY_INDEX_COLUMNS.items():
        assert index_name in index_names
        assert _index_columns(db_path, index_name)[: len(expected_columns)] == expected_columns


def test_run_task_and_quote_row_contracts_support_hot_query_and_replacement(tmp_path: Path) -> None:
    db_path = tmp_path / "storage.db"
    storage = DGTeamStorage(db_path)
    storage.init_db()
    run_key = "contract_2026-04-17"
    task_key = "model-a__city-a__2026-04-17"

    storage.upsert_run(
        run_key,
        tmp_path,
        summary_json=json.dumps({"version": 1}),
        status="running",
        started_at="2026-04-17 10:00:00",
        finished_at="",
    )
    storage.upsert_run(
        run_key,
        tmp_path / "second",
        summary_json=json.dumps({"version": 2}),
        status="completed",
        started_at="2026-04-17 10:00:00",
        finished_at="2026-04-17 10:05:00",
    )
    storage.upsert_task(run_key, _sample_task(task_key, status="running", row_count=1), '{"phase": "first"}')
    storage.upsert_task(run_key, _sample_task(task_key, status="completed", row_count=2), '{"phase": "second"}')

    seeded_task_key = "seeded-model"
    inserted_or_updated = storage.ensure_run_tasks(run_key, [_sample_task(seeded_task_key, status="pending", row_count=1)])
    assert inserted_or_updated == 1
    storage.ensure_run_tasks(run_key, [_sample_task(seeded_task_key, status="completed", row_count=99)])

    inserted_rows = storage.insert_quote_rows(
        run_key,
        [
            _sample_quote_row(model_id="model-a", city_id="city-a", price_text="9600"),
            _sample_quote_row(model_id="model-a", city_id="city-a", price_text="9700"),
        ],
    )
    assert inserted_rows == 2

    with sqlite3.connect(db_path) as conn:
        run_row = conn.execute(
            "SELECT source_dir, status, summary_json FROM runs WHERE run_key = ?",
            (run_key,),
        ).fetchone()
        task_row = conn.execute(
            "SELECT status, row_count, payload_json FROM tasks WHERE run_key = ? AND task_key = ?",
            (run_key, task_key),
        ).fetchone()
        seeded_row = conn.execute(
            "SELECT status, row_count, payload_json FROM tasks WHERE run_key = ? AND task_key = ?",
            (run_key, seeded_task_key),
        ).fetchone()
        quote_rows = conn.execute(
            """
            SELECT task_key, COALESCE(group_title, ''), COALESCE(condition_bucket, ''), price_text
            FROM quote_rows
            WHERE run_key = ?
            ORDER BY id
            """,
            (run_key,),
        ).fetchall()

    assert run_row == (
        str((tmp_path / "second").resolve()),
        "completed",
        json.dumps({"version": 2}),
    )
    assert task_row == ("completed", 2, '{"phase": "second"}')
    assert seeded_row == ("pending", 1, json.dumps(_sample_task(seeded_task_key, status="pending", row_count=1)))
    assert [row[0] for row in quote_rows] == ["model-a__city-a__2026-04-17", "model-a__city-a__2026-04-17"]
    assert [row[1] for row in quote_rows] == ["", ""]
    assert [row[2] for row in quote_rows] == ["", ""]

    candidates = storage.list_sku_candidates(run_key)
    candidate = next(
        item
        for item in candidates
        if item["brand_title"] == "BrandA"
        and item["series_title"] == "SeriesA"
        and item["model_title"] == "ModelA"
        and item["group_title"] == ""
        and item["condition_bucket"] == ""
    )
    assert candidate["row_count"] == 2
    assert candidate["min_price"] == 9600
    assert candidate["max_price"] == 9700

    hot_query_rows = storage.get_sku_rows(
        run_key=run_key,
        brand_title="BrandA",
        series_title="SeriesA",
        model_title="ModelA",
        group_title="",
        condition_bucket="",
    )
    assert len(hot_query_rows) == 2

    replaced_count = storage.replace_quote_rows_for_task(
        run_key,
        "model-a__city-a__2026-04-17",
        [_sample_quote_row(model_id="model-a", city_id="city-a", price_text="9800")],
    )
    assert replaced_count == 1

    replacement_rows = storage.get_sku_rows(
        run_key=run_key,
        brand_title="BrandA",
        series_title="SeriesA",
        model_title="ModelA",
        group_title="",
        condition_bucket="",
    )
    assert [row["price_text"] for row in replacement_rows] == ["9800"]


def test_publish_market_snapshots_updates_live_state_and_replaces_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "publish.db"
    storage = DGTeamStorage(db_path)
    storage.init_db()
    run_key = "publish_2026-04-17"

    storage.publish_market_snapshots(
        run_key,
        [_sample_snapshot(group_title="128g", market_price=9600, published_at="2026-04-17 11:00:00")],
        summary={"counts": {"source_rows": 2}},
        published_at="2026-04-17 11:00:00",
    )
    storage.publish_market_snapshots(
        run_key,
        [_sample_snapshot(group_title="256g", market_price=9800, published_at="2026-04-17 11:30:00")],
        summary={"counts": {"source_rows": 1}},
        published_at="2026-04-17 11:30:00",
    )

    live_state = storage.get_live_market_state()
    live_marker = storage.get_live_marker(run_key)
    with sqlite3.connect(db_path) as conn:
        snapshot_rows = conn.execute(
            """
            SELECT group_title, market_price, published_at
            FROM market_snapshots
            WHERE run_key = ?
            ORDER BY group_title
            """,
            (run_key,),
        ).fetchall()
        app_state_rows = conn.execute(
            "SELECT state_key, state_value FROM app_state ORDER BY state_key"
        ).fetchall()

    assert snapshot_rows == [("256g", 9800, "2026-04-17 11:30:00")]
    assert live_state["run_key"] == run_key
    assert live_state["published_at"] == "2026-04-17 11:30:00"
    assert live_state["summary"]["counts"]["source_rows"] == 1
    assert live_state["summary"]["snapshot_count"] == 1
    assert live_marker["market_snapshot_count"] == 1
    assert live_marker["published_at"] == "2026-04-17 11:30:00"
    assert live_marker["live_source"] == "market_snapshots"
    assert dict(app_state_rows)["live_market_run_key"] == run_key


def test_phase1_storage_shims_match_extracted_modules(tmp_path: Path) -> None:
    db_path = tmp_path / "phase1.db"
    storage = DGTeamStorage(db_path)
    storage.init_db()
    run_key = "phase1_2026-04-17"

    storage.upsert_run(
        run_key,
        tmp_path,
        summary_json=json.dumps({"phase": 1}),
        status="completed",
        started_at="2026-04-17 12:00:00",
        finished_at="2026-04-17 12:05:00",
    )
    storage.insert_quote_rows(
        run_key,
        [_sample_quote_row(model_id="model-a", city_id="city-a", price_text="9600")],
    )
    storage.publish_market_snapshots(
        run_key,
        [_sample_snapshot(group_title="128g", market_price=9600, published_at="2026-04-17 12:05:00")],
        summary={"counts": {"source_rows": 1}},
        published_at="2026-04-17 12:05:00",
    )

    assert storage_state.get_live_market_state(storage) == storage.get_live_market_state()
    assert storage_publish.get_market_snapshot_count(storage, run_key) == storage.get_market_snapshot_count(run_key)
    assert storage_publish.get_live_market_run_key(storage) == storage.get_live_market_run_key()
    assert storage_publish.list_market_snapshot_candidates(storage, run_key) == storage.list_market_snapshot_candidates(run_key)
    assert storage_publish.list_live_sku_candidates(storage, run_key) == storage.list_live_sku_candidates(run_key)
    assert storage_publish.get_market_snapshot_row(
        storage,
        run_key=run_key,
        brand_title="BrandA",
        series_title="SeriesA",
        model_title="ModelA",
        group_title="128g",
        condition_bucket="standard",
    ) == storage.get_market_snapshot_row(
        run_key=run_key,
        brand_title="BrandA",
        series_title="SeriesA",
        model_title="ModelA",
        group_title="128g",
        condition_bucket="standard",
    )
    assert storage_query.get_live_marker(storage, run_key) == storage.get_live_marker(run_key)
    assert storage_query.list_sku_candidates(storage, run_key) == storage.list_sku_candidates(run_key)
    assert storage_query.get_sku_rows(
        storage,
        run_key=run_key,
        brand_title="BrandA",
        series_title="SeriesA",
        model_title="ModelA",
        group_title="",
        condition_bucket="",
    ) == storage.get_sku_rows(
        run_key=run_key,
        brand_title="BrandA",
        series_title="SeriesA",
        model_title="ModelA",
        group_title="",
        condition_bucket="",
    )
    assert storage_query.get_run_quote_rows(storage, run_key) == storage.get_run_quote_rows(run_key)
    assert storage_query.get_model_family_rows(
        storage,
        run_key=run_key,
        brand_title="BrandA",
        series_title="SeriesA",
        model_title="ModelA",
        condition_bucket="",
    ) == storage.get_model_family_rows(
        run_key=run_key,
        brand_title="BrandA",
        series_title="SeriesA",
        model_title="ModelA",
        condition_bucket="",
    )


def test_runtime_snapshot_databases_match_core_contract_if_present() -> None:
    available_paths = [path for path in RUNTIME_DB_PATHS if path.exists()]
    if not available_paths:
        pytest.skip("Runtime snapshot databases are not available in this checkout.")

    for db_path in available_paths:
        for table_name, expected_columns in storage_schema.CORE_TABLE_COLUMNS.items():
            assert expected_columns.issubset(_table_columns(db_path, table_name))
        index_names = _index_names(db_path)
        for index_name, expected_columns in storage_schema.HOT_QUERY_INDEX_COLUMNS.items():
            assert index_name in index_names
            assert _index_columns(db_path, index_name)[: len(expected_columns)] == expected_columns
