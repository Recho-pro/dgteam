from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence
import tarfile as tar

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from dgteam.core.config import (
    AgentConfig,
    PublishApiConfig,
    QueryApiConfig,
    ReleaseConfig,
    Settings,
    StorageRetentionConfig,
    WechatClawbotConfig,
    WechatOfficialConfig,
)
from dgteam.core.console_encoding import configure_utf8_stdio
from dgteam.core.storage import DGTeamStorage
from dgteam.core.textio import read_json_utf8, write_json_utf8
from dgteam.integrations.wechat_official.models import WechatOfficialInboundMessage
from dgteam.integrations.wechat_official.service import WechatOfficialService
from dgteam.ops.real_source_db import (
    ACTIVE_RELEASE_SOURCE_DB_RELATIVE,
    LEGACY_WORKING_SOURCE_DB_RELATIVE,
    resolve_real_source_db,
)
from dgteam.ops.runtime_audit import build_runtime_audit
from dgteam.publish_api.app import PublishApiHandler
from dgteam.publish_api.release_store import ReleaseStore
from dgteam.query_api.server import QueryApp, build_handler
from dgteam.query_api.ui_assets import RELEASE_UI_DIRNAME, package_query_ui_assets, read_ui_asset_manifest
from dgteam.release.builder import archive_release_bundle, build_local_release_bundle, build_release_manifest
from dgteam.release.upload_client import rollback_release, upload_release_bundle

REAL_SAMPLE_SELECTORS: Sequence[Dict[str, str]] = (
    {
        "label": "iphone17pm",
        "brand_title": "苹果",
        "model_like": "%17 Pro Max%",
        "condition_bucket": "apple_company_pure_sealed_target",
    },
    {
        "label": "redmik80",
        "brand_title": "红米",
        "model_like": "%Redmi K80%",
        "condition_bucket": "non_apple_allowed",
    },
    {
        "label": "iqoo15",
        "brand_title": "VIVO",
        "model_like": "%iQOO 15%",
        "condition_bucket": "non_apple_allowed",
    },
)
REAL_SEARCH_QUERIES: Sequence[str] = (
    "iphone17pm",
    "pingguo17pm",
    "redmi k80",
    "hongmik80",
    "iqoo15",
)

FIXTURE_RUN_KEY = "fixture_smoke_2026-04-17"
FIXTURE_SAMPLE_SELECTORS: Sequence[Dict[str, str]] = (
    {
        "label": "iphone17promax",
        "brand_title": "Apple",
        "model_like": "%iPhone 17 Pro Max%",
        "condition_bucket": "apple_company_pure_sealed_target",
    },
    {
        "label": "redmik80",
        "brand_title": "Xiaomi",
        "model_like": "%Redmi K80%",
        "condition_bucket": "non_apple_allowed",
    },
    {
        "label": "iqoo15",
        "brand_title": "VIVO",
        "model_like": "%iQOO 15%",
        "condition_bucket": "non_apple_allowed",
    },
)
FIXTURE_SEARCH_QUERIES: Sequence[str] = (
    "iphone17promax",
    "redmi k80",
    "iqoo15",
)
GATE_CONTRACT_VERSION = "release-gate.v1"


def build_test_settings(root: Path, token: str) -> Settings:
    local_root = root / "runtime" / "local"
    cloud_root = root / "runtime" / "cloud"
    return Settings(
        env="integration-smoke",
        log_level="INFO",
        project_root=root,
        local_root=local_root,
        cloud_root=cloud_root,
        agent=AgentConfig(
            profile_dir=local_root / "browser_profile",
            runs_dir=local_root / "runs",
            releases_dir=local_root / "releases",
        ),
        query_api=QueryApiConfig(host="127.0.0.1", port=8875),
        publish_api=PublishApiConfig(
            host="127.0.0.1",
            port=8865,
            shared_token=token,
            uploads_dir=cloud_root / "uploads",
        ),
        wechat_clawbot=WechatClawbotConfig(
            enabled=False,
            host="127.0.0.1",
            port=8965,
            bridge_mode="wecom_customer_service",
            callback_path="/wecom/kf/callback",
            shared_secret="",
            corp_id="",
            corp_secret="",
            callback_token="",
            encoding_aes_key="",
            default_open_kfid="",
            api_base_url="https://qyapi.weixin.qq.com",
            inbox_dir=local_root / "wechat_clawbot" / "inbox",
            archive_dir=local_root / "wechat_clawbot" / "archive",
            state_dir=local_root / "wechat_clawbot" / "state",
        ),
        wechat_official=WechatOfficialConfig(
            enabled=False,
            host="127.0.0.1",
            port=8975,
            callback_path="/wechat/official/callback",
            app_id="",
            app_secret="",
            callback_token="",
            encoding_aes_key="",
            api_base_url="https://api.weixin.qq.com",
            state_dir=local_root / "wechat_official" / "state",
            image_worker_enabled=False,
            image_api_key="",
            image_fast_model="openai/gpt-4.1-mini",
            image_fast_timeout_seconds=4,
            image_fast_max_edge_px=768,
            image_fast_max_bytes=140000,
            image_fast_jpeg_quality=68,
            image_primary_model="openai/gpt-4.1-mini",
            image_fallback_model="qwen/qwen3-vl-32b-instruct",
            image_poll_interval_seconds=0.25,
            image_timeout_seconds=45,
            image_max_edge_px=832,
            image_max_bytes=200000,
            image_jpeg_quality=66,
        ),
        release=ReleaseConfig(
            current_dir=cloud_root / "current",
            previous_dir=cloud_root / "previous",
            history_dir=cloud_root / "releases",
            staging_dir=cloud_root / "staging",
            state_dir=cloud_root / "deployments",
        ),
        retention=StorageRetentionConfig(
            enabled=True,
            keep_local_releases=1,
            keep_local_release_archives=0,
            keep_integration_smoke_runs=1,
            keep_cloud_releases=0,
            keep_cloud_rollbacks=1,
            prune_cloud_uploads=True,
        ),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the DGTEAM release -> deploy -> query -> rollback smoke chain.")
    parser.add_argument("--project-root", default=str(PROJECT_ROOT))
    parser.add_argument("--source-db", default="", help="Override the source SQLite database used for sample extraction.")
    parser.add_argument(
        "--mode",
        choices=("fixture", "real-source"),
        default="real-source",
        help="fixture uses a synthetic source DB; real-source reads a real SQLite snapshot.",
    )
    parser.add_argument(
        "--fixture",
        action="store_true",
        help="Use a synthetic fixture database so the rehearsal can run on CI or self-hosted nightly runners without a live source DB.",
    )
    parser.add_argument(
        "--report-path",
        default="",
        help="Optional explicit output path for the full smoke report JSON.",
    )
    return parser.parse_args()


def json_get(url: str) -> dict:
    response = http_get(url)
    if not response["ok"]:
        raise RuntimeError(f"HTTP GET failed status={response['status']} url={url} body={response['body_text']}")
    return json.loads(response["body_text"])


def http_get(url: str) -> Dict[str, Any]:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request) as response:
            body = response.read()
            return {
                "ok": True,
                "status": int(getattr(response, "status", 200) or 200),
                "url": response.geturl(),
                "headers": {str(key): str(value) for key, value in response.headers.items()},
                "body_text": body.decode("utf-8", errors="replace"),
            }
    except urllib.error.HTTPError as exc:
        body = exc.read()
        return {
            "ok": False,
            "status": int(exc.code or 500),
            "url": url,
            "headers": {str(key): str(value) for key, value in exc.headers.items()},
            "body_text": body.decode("utf-8", errors="replace"),
        }


def header_value(headers: Dict[str, Any], name: str) -> str:
    expected = str(name or "").strip().lower()
    for key, value in dict(headers or {}).items():
        if str(key or "").strip().lower() == expected:
            return str(value or "")
    return ""


def prune_old_smoke_runs(root: Path, *, keep: int = 3) -> None:
    root.mkdir(parents=True, exist_ok=True)
    runs = sorted((item for item in root.iterdir() if item.is_dir()), key=lambda path: path.stat().st_mtime, reverse=True)
    for item in runs[keep:]:
        shutil.rmtree(item, ignore_errors=True)


def family_predicate(alias: str, families: Sequence[Dict[str, Any]]) -> tuple[str, List[Any]]:
    prefix = f"{alias}." if alias else ""
    clauses: List[str] = []
    params: List[Any] = []
    for family in families:
        clauses.append(
            f"({prefix}brand_title = ? AND {prefix}series_title = ? AND {prefix}model_title = ? AND {prefix}condition_bucket = ?)"
        )
        params.extend(
            [
                family["brand_title"],
                family["series_title"],
                family["model_title"],
                family["condition_bucket"],
            ]
        )
    return " OR ".join(clauses), params


def copy_rows_by_query(
    conn: sqlite3.Connection,
    *,
    target_table: str,
    source_table: str,
    where_clause: str,
    params: Iterable[Any],
) -> int:
    cursor = conn.execute(f"PRAGMA table_info({target_table})")
    columns = [str(row[1]) for row in cursor.fetchall()]
    if not columns:
        return 0
    column_list = ", ".join(columns)
    result = conn.execute(
        f"INSERT OR REPLACE INTO {target_table} ({column_list}) SELECT {column_list} FROM src.{source_table} WHERE {where_clause}",
        tuple(params),
    )
    return int(result.rowcount or 0)


def select_smoke_families(source_db: Path, run_key: str, selectors: Sequence[Dict[str, str]]) -> List[Dict[str, Any]]:
    families: List[Dict[str, Any]] = []
    with sqlite3.connect(source_db) as conn:
        conn.row_factory = sqlite3.Row
        for spec in selectors:
            row = conn.execute(
                """
                SELECT
                    brand_title,
                    series_title,
                    model_title,
                    condition_bucket,
                    SUM(source_row_count) AS source_row_count,
                    COUNT(*) AS snapshot_row_count
                FROM market_snapshots
                WHERE run_key = ?
                  AND brand_title = ?
                  AND model_title LIKE ?
                  AND condition_bucket = ?
                GROUP BY brand_title, series_title, model_title, condition_bucket
                ORDER BY source_row_count DESC, snapshot_row_count DESC, model_title ASC
                LIMIT 1
                """,
                (
                    run_key,
                    spec["brand_title"],
                    spec["model_like"],
                    spec["condition_bucket"],
                ),
            ).fetchone()
            if row is None:
                raise RuntimeError(f"Smoke selector did not match any family: {spec}")
            families.append(
                {
                    "label": spec["label"],
                    "brand_title": str(row["brand_title"] or ""),
                    "series_title": str(row["series_title"] or ""),
                    "model_title": str(row["model_title"] or ""),
                    "condition_bucket": str(row["condition_bucket"] or ""),
                    "source_row_count": int(row["source_row_count"] or 0),
                    "snapshot_row_count": int(row["snapshot_row_count"] or 0),
                }
            )
    return families


def count_selector_matches(conn: sqlite3.Connection, run_key: str, selectors: Sequence[Dict[str, str]]) -> int:
    matched = 0
    for spec in selectors:
        row = conn.execute(
            """
            SELECT 1
            FROM market_snapshots
            WHERE run_key = ?
              AND brand_title = ?
              AND model_title LIKE ?
              AND condition_bucket = ?
            LIMIT 1
            """,
            (
                run_key,
                spec["brand_title"],
                spec["model_like"],
                spec["condition_bucket"],
            ),
        ).fetchone()
        if row is not None:
            matched += 1
    return matched


def resolve_smoke_source_run_key(source_db: Path, preferred_run_key: str, selectors: Sequence[Dict[str, str]]) -> str:
    with sqlite3.connect(source_db) as conn:
        candidate_rows = conn.execute(
            """
            SELECT run_key, MAX(published_at) AS published_at
            FROM market_snapshots
            GROUP BY run_key
            ORDER BY published_at DESC, run_key DESC
            """
        ).fetchall()
        candidates = [str(row[0] or "").strip() for row in candidate_rows if str(row[0] or "").strip()]
        if preferred_run_key:
            candidates = [preferred_run_key, *[item for item in candidates if item != preferred_run_key]]

        best_run_key = ""
        best_match_count = -1
        for run_key in candidates:
            match_count = count_selector_matches(conn, run_key, selectors)
            if match_count == len(selectors):
                return run_key
            if match_count > best_match_count:
                best_run_key = run_key
                best_match_count = match_count

    if best_run_key:
        return best_run_key
    raise RuntimeError("No completed run contains any of the configured smoke sample selectors.")


def build_smoke_source_storage_from_db(smoke_root: Path, *, source_db: Path, run_key: str, selectors: Sequence[Dict[str, str]]) -> Dict[str, Any]:
    if not source_db.exists():
        raise FileNotFoundError(f"Source database does not exist: {source_db}")

    smoke_source_dir = smoke_root / "sample_source"
    smoke_source_dir.mkdir(parents=True, exist_ok=True)
    smoke_db = smoke_source_dir / "dgteam.db"
    if smoke_db.exists():
        smoke_db.unlink()

    smoke_storage = DGTeamStorage(smoke_db)
    smoke_storage.init_db()
    families = select_smoke_families(source_db, run_key, selectors)
    family_where, family_params = family_predicate("", families)

    with sqlite3.connect(smoke_db) as target_conn:
        target_conn.execute("ATTACH DATABASE ? AS src", (str(source_db),))
        copied_runs = copy_rows_by_query(
            target_conn,
            target_table="runs",
            source_table="runs",
            where_clause="run_key = ?",
            params=(run_key,),
        )
        copied_quotes = copy_rows_by_query(
            target_conn,
            target_table="quote_rows",
            source_table="quote_rows",
            where_clause=f"run_key = ? AND ({family_where})",
            params=(run_key, *family_params),
        )
        task_keys = [
            str(row[0] or "").strip()
            for row in target_conn.execute(
                "SELECT DISTINCT task_key FROM quote_rows WHERE run_key = ? AND TRIM(COALESCE(task_key, '')) <> ''",
                (run_key,),
            ).fetchall()
        ]
        copied_tasks = 0
        copied_events = 0
        if task_keys:
            placeholders = ", ".join("?" for _ in task_keys)
            copied_tasks = copy_rows_by_query(
                target_conn,
                target_table="tasks",
                source_table="tasks",
                where_clause=f"run_key = ? AND task_key IN ({placeholders})",
                params=(run_key, *task_keys),
            )
            run_event_columns = (
                "run_key, event_key, event_time, event_type, task_key, brand_title, series_title, model_title, city_title, details_json"
            )
            result = target_conn.execute(
                f"""
                INSERT INTO run_events ({run_event_columns})
                SELECT {run_event_columns}
                FROM src.run_events
                WHERE run_key = ?
                  AND (task_key IN ({placeholders}) OR TRIM(COALESCE(task_key, '')) = '')
                ORDER BY event_time DESC, id DESC
                LIMIT 200
                """,
                (run_key, *task_keys),
            )
            copied_events = int(result.rowcount or 0)
        target_conn.commit()

    smoke_db_total_bytes = sum(
        int(path.stat().st_size)
        for path in smoke_db.parent.glob(f"{smoke_db.name}*")
        if path.is_file()
    )
    return {
        "storage": smoke_storage,
        "run_key": run_key,
        "source_db": str(source_db),
        "smoke_db": str(smoke_db),
        "families": families,
        "copied_runs": copied_runs,
        "copied_quotes": copied_quotes,
        "copied_tasks": copied_tasks,
        "copied_events": copied_events,
        "smoke_db_size_bytes": int(smoke_db.stat().st_size),
        "smoke_db_total_bytes": smoke_db_total_bytes,
        "mode": "source_db",
    }


def _fixture_snapshot_row(
    *,
    brand_title: str,
    series_title: str,
    model_title: str,
    group_title: str,
    condition_bucket: str,
    market_price: int,
    source_row_count: int,
) -> dict[str, Any]:
    return {
        "brand_title": brand_title,
        "series_title": series_title,
        "model_title": model_title,
        "group_title": group_title,
        "condition_bucket": condition_bucket,
        "selected_gprice_label": "04-17",
        "selected_gprice_labels": "04-17",
        "latest_gprice": "04-17",
        "latest_imported_at": "2026-04-17 10:00:00",
        "source_row_count": source_row_count,
        "source_count": max(1, source_row_count // 2),
        "min_price": market_price,
        "max_price": market_price + 100,
        "market_price": market_price,
        "price_range": f"{market_price}-{market_price + 100}",
        "trusted_status": "trusted",
        "trusted_sample_count": max(1, source_row_count // 2),
        "trusted_seller_count": max(1, source_row_count // 3),
        "confidence_score": 85,
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
        "search_text": f"{brand_title} {series_title} {model_title} {group_title}",
        "search_text_normalized": f"{brand_title}{series_title}{model_title}{group_title}".replace(" ", "").lower(),
        "model_group_normalized": f"{model_title}{group_title}".replace(" ", "").lower(),
    }


def build_fixture_source_storage(smoke_root: Path) -> Dict[str, Any]:
    smoke_source_dir = smoke_root / "sample_source"
    smoke_source_dir.mkdir(parents=True, exist_ok=True)
    smoke_db = smoke_source_dir / "dgteam.db"
    if smoke_db.exists():
        smoke_db.unlink()

    smoke_storage = DGTeamStorage(smoke_db)
    smoke_storage.init_db()
    smoke_storage.upsert_run(
        FIXTURE_RUN_KEY,
        source_dir=smoke_source_dir,
        summary_json=json.dumps({"run_key": FIXTURE_RUN_KEY, "counts": {"source_rows": 5}}),
        status="completed",
        started_at="2026-04-17 09:55:00",
        finished_at="2026-04-17 10:00:00",
    )
    snapshots = [
        _fixture_snapshot_row(
            brand_title="Apple",
            series_title="iPhone 17",
            model_title="iPhone 17 Pro Max",
            group_title="256G",
            condition_bucket="apple_company_pure_sealed_target",
            market_price=9800,
            source_row_count=18,
        ),
        _fixture_snapshot_row(
            brand_title="Apple",
            series_title="iPhone 17",
            model_title="iPhone 17 Pro Max",
            group_title="512G",
            condition_bucket="apple_company_pure_sealed_target",
            market_price=10600,
            source_row_count=12,
        ),
        _fixture_snapshot_row(
            brand_title="Xiaomi",
            series_title="Redmi K Series",
            model_title="Redmi K80",
            group_title="12+256G",
            condition_bucket="non_apple_allowed",
            market_price=2899,
            source_row_count=20,
        ),
        _fixture_snapshot_row(
            brand_title="VIVO",
            series_title="iQOO Series",
            model_title="iQOO 15",
            group_title="12+256G",
            condition_bucket="non_apple_allowed",
            market_price=3999,
            source_row_count=16,
        ),
    ]
    smoke_storage.publish_market_snapshots(
        FIXTURE_RUN_KEY,
        snapshots,
        summary={"counts": {"source_rows": len(snapshots)}},
        published_at="2026-04-17 10:00:00",
    )

    families = []
    grouped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in snapshots:
        key = (
            str(row["brand_title"]),
            str(row["series_title"]),
            str(row["model_title"]),
            str(row["condition_bucket"]),
        )
        grouped.setdefault(
            key,
            {
                "brand_title": key[0],
                "series_title": key[1],
                "model_title": key[2],
                "condition_bucket": key[3],
                "source_row_count": 0,
                "snapshot_row_count": 0,
            },
        )
        grouped[key]["source_row_count"] += int(row["source_row_count"])
        grouped[key]["snapshot_row_count"] += 1
    for item in grouped.values():
        families.append(item)

    smoke_db_total_bytes = sum(
        int(path.stat().st_size)
        for path in smoke_db.parent.glob(f"{smoke_db.name}*")
        if path.is_file()
    )
    return {
        "storage": smoke_storage,
        "run_key": FIXTURE_RUN_KEY,
        "source_db": str(smoke_db),
        "smoke_db": str(smoke_db),
        "families": families,
        "snapshot_count": len(snapshots),
        "copied_runs": 1,
        "copied_quotes": 0,
        "copied_tasks": 0,
        "copied_events": 0,
        "smoke_db_size_bytes": int(smoke_db.stat().st_size),
        "smoke_db_total_bytes": smoke_db_total_bytes,
        "mode": "fixture",
    }


def build_fixture_release_bundle(
    storage: DGTeamStorage,
    target_dir: Path,
    *,
    run_key: str,
    release_id: str,
    snapshot_count: int,
) -> Dict[str, Any]:
    target = Path(target_dir).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    db_path = target / "dgteam.db"
    storage.export_database_snapshot(db_path)
    snapshot_csv = target / "market_v1_snapshot.csv"
    clusters_csv = target / "market_v1_clusters.csv"
    summary_path = target / "summary.json"
    release_path = target / "release.json"
    snapshot_csv.write_text("", encoding="utf-8")
    clusters_csv.write_text("", encoding="utf-8")
    write_json_utf8(
        summary_path,
        {
            "run_key": run_key,
            "counts": {
                "source_rows": snapshot_count,
                "published_snapshots": snapshot_count,
                "cluster_rows": 0,
            },
            "outputs": {
                "snapshot_csv": str(snapshot_csv),
                "clusters_csv": str(clusters_csv),
                "summary_json": str(summary_path),
            },
        },
    )
    ui_asset_dir = target / RELEASE_UI_DIRNAME
    ui_asset_manifest = package_query_ui_assets(ui_asset_dir)
    ui_asset_files = sorted(path for path in ui_asset_dir.rglob("*") if path.is_file())
    write_json_utf8(
        release_path,
        {
            "release_id": release_id,
            "run_key": run_key,
            "built_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "snapshot_count": snapshot_count,
            "cluster_row_count": 0,
            "summary": {"counts": {"source_rows": snapshot_count}},
            "database": str(db_path),
            "query_ui": {
                "asset_dir": str(ui_asset_dir),
                "asset_manifest": ui_asset_manifest,
            },
        },
    )
    manifest = build_release_manifest(
        target,
        release_id=release_id,
        run_key=run_key,
        quote_count=snapshot_count,
        snapshot_count=snapshot_count,
        files=(snapshot_csv, clusters_csv, summary_path, release_path, db_path, *ui_asset_files),
        build_version="dgteam-build.v2",
    )
    return {
        "release_id": manifest.release_id,
        "release_dir": str(target),
        "run_key": manifest.run_key,
        "manifest": manifest.to_dict(),
    }


def summarize_search_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    results = list(payload.get("results") or [])
    first = dict(results[0] or {}) if results else {}
    return {
        "ok": bool(payload.get("ok")),
        "result_count": len(results),
        "first_label": str(first.get("label") or ""),
        "first_detail_key": str(first.get("detail_key") or ""),
        "first_meta": str(first.get("meta") or ""),
    }


def build_smoke_report_query_results(query_base_url: str, *, search_queries: Sequence[str]) -> Dict[str, Any]:
    searches: Dict[str, Any] = {}
    selected_detail_key = ""
    selected_label = ""
    selected_query = ""
    for query in search_queries:
        payload = json_get(f"{query_base_url}/api/search?q={urllib.parse.quote(query, safe='')}&limit=3")
        summary = summarize_search_payload(payload)
        searches[query] = summary
        if not summary["ok"] or int(summary["result_count"]) <= 0:
            raise RuntimeError(f"Smoke search returned no result for query: {query}")
        if not selected_detail_key and summary["first_detail_key"]:
            selected_detail_key = summary["first_detail_key"]
            selected_label = summary["first_label"]
            selected_query = query

    if not selected_detail_key:
        raise RuntimeError("Smoke search did not return a usable detail key.")

    detail_payload = json_get(
        f"{query_base_url}/api/sku?detail_key={urllib.parse.quote(selected_detail_key, safe='')}"
    )
    if not detail_payload.get("ok"):
        raise RuntimeError(f"Smoke detail query failed: {detail_payload}")
    branches = list(detail_payload.get("branches") or [])
    if not branches:
        raise RuntimeError(f"Smoke detail query returned no branches: {detail_payload}")
    first_branch = dict(branches[0] or {})
    capacity_groups = list(first_branch.get("capacity_groups") or [])
    if not capacity_groups:
        raise RuntimeError(f"Smoke detail query returned no capacity groups: {detail_payload}")
    capacity_labels = [
        str(item.get("capacity_label") or item.get("group_title") or "").strip()
        for item in capacity_groups
        if str(item.get("capacity_label") or item.get("group_title") or "").strip()
    ]

    return {
        "searches": searches,
        "selected_query": selected_query,
        "selected_label": selected_label,
        "detail_key": selected_detail_key,
        "detail": {
            "ok": bool(detail_payload.get("ok")),
            "header_title": str((detail_payload.get("header") or {}).get("title") or ""),
            "branch_count": len(branches),
            "first_branch_title": str(first_branch.get("branch_title") or ""),
            "capacity_group_count": len(capacity_groups),
            "default_capacity_label": str((detail_payload.get("default_capacity") or {}).get("capacity_label") or ""),
            "capacity_labels": capacity_labels,
            "market_price": int(((detail_payload.get("hero") or {}).get("market_price") or 0)),
        },
    }


def build_query_ui_acceptance(query_base_url: str, *, current_dir: Path) -> Dict[str, Any]:
    asset_dir = current_dir / RELEASE_UI_DIRNAME
    asset_manifest = read_ui_asset_manifest(asset_dir)
    version = str(asset_manifest.get("version") or "").strip()
    expected_styles_href = f"./styles.css?v={version}" if version else ""
    expected_app_href = f"./app.js?v={version}" if version else ""

    index_response = http_get(f"{query_base_url}/")
    styles_response = http_get(
        f"{query_base_url}/styles.css?v={urllib.parse.quote(version, safe='')}" if version else f"{query_base_url}/styles.css"
    )
    app_response = http_get(
        f"{query_base_url}/app.js?v={urllib.parse.quote(version, safe='')}" if version else f"{query_base_url}/app.js"
    )
    manifest_response = http_get(f"{query_base_url}/asset-manifest.json")

    index_cache = header_value(index_response["headers"], "Cache-Control")
    styles_cache = header_value(styles_response["headers"], "Cache-Control")
    app_cache = header_value(app_response["headers"], "Cache-Control")
    index_content_type = header_value(index_response["headers"], "Content-Type")
    styles_content_type = header_value(styles_response["headers"], "Content-Type")
    app_content_type = header_value(app_response["headers"], "Content-Type")

    ok = (
        index_response["status"] == 200
        and styles_response["status"] == 200
        and app_response["status"] == 200
        and manifest_response["status"] == 200
        and bool(version)
        and expected_styles_href in index_response["body_text"]
        and expected_app_href in index_response["body_text"]
        and "no-store" in index_cache.lower()
        and "immutable" in styles_cache.lower()
        and "immutable" in app_cache.lower()
        and "text/html" in index_content_type.lower()
        and "text/css" in styles_content_type.lower()
        and "javascript" in app_content_type.lower()
    )
    return {
        "ok": ok,
        "asset_dir": str(asset_dir),
        "version": version,
        "index_status": int(index_response["status"]),
        "styles_status": int(styles_response["status"]),
        "app_status": int(app_response["status"]),
        "manifest_status": int(manifest_response["status"]),
        "index_cache_control": index_cache,
        "styles_cache_control": styles_cache,
        "app_cache_control": app_cache,
        "index_content_type": index_content_type,
        "styles_content_type": styles_content_type,
        "app_content_type": app_content_type,
        "expected_styles_href": expected_styles_href,
        "expected_app_href": expected_app_href,
    }


def build_unknown_api_acceptance(query_base_url: str) -> Dict[str, Any]:
    response = http_get(f"{query_base_url}/api/acceptance-not-found")
    payload = json.loads(response["body_text"]) if response["body_text"].strip() else {}
    ok = (
        int(response["status"]) == 404
        and payload.get("ok") is False
        and str(payload.get("error_code") or "").strip() == "unknown_api_endpoint"
        and str(((payload.get("details") or {}).get("path") or "")).strip() == "/api/acceptance-not-found"
    )
    return {
        "ok": ok,
        "status": int(response["status"]),
        "payload": payload,
    }


def _wechat_message(*, open_id: str, content: str, msg_id: str) -> WechatOfficialInboundMessage:
    return WechatOfficialInboundMessage(
        msg_type="text",
        event="",
        event_key="",
        from_user=open_id,
        to_user="dgteam-smoke",
        content=content,
        media_id="",
        pic_url="",
        msg_id=msg_id,
        raw_payload={"smoke": True},
    )


def build_wechat_acceptance(
    *,
    settings: Settings,
    db_path: Path,
    primary_query: str,
    refinement_hint: str = "",
    open_id: str,
) -> Dict[str, Any]:
    service = WechatOfficialService(config=settings.wechat_official, db_path=db_path)
    initial_reply = service.workflow.handle_message(
        _wechat_message(open_id=open_id, content=primary_query, msg_id=f"{open_id}-initial")
    )
    initial_session = service.workflow.session_store.load(open_id).to_dict()

    numeric_reply = ""
    if list(initial_session.get("pending_candidates") or []):
        numeric_reply = service.workflow.handle_message(
            _wechat_message(open_id=open_id, content="1", msg_id=f"{open_id}-select")
        )

    selection_session = service.workflow.session_store.load(open_id).to_dict()

    refinement_reply = ""
    if refinement_hint:
        refinement_reply = service.workflow.handle_message(
            _wechat_message(open_id=open_id, content=refinement_hint, msg_id=f"{open_id}-refine")
        )

    final_session = service.workflow.session_store.load(open_id).to_dict()
    ok = bool(initial_reply) and bool(
        final_session.get("last_candidate")
        or final_session.get("last_result_title")
        or final_session.get("pending_candidates")
    )
    if refinement_hint:
        ok = ok and bool(refinement_reply)

    return {
        "ok": ok,
        "health": service.health_payload(),
        "primary_query": primary_query,
        "refinement_hint": refinement_hint,
        "initial_reply_preview": initial_reply[:200],
        "numeric_reply_preview": numeric_reply[:200],
        "refinement_reply_preview": refinement_reply[:200],
        "initial_session": initial_session,
        "selection_session": selection_session,
        "final_session": final_session,
    }


def create_backup_archive(source_dir: Path, *, backup_root: Path, label: str) -> str:
    backup_root.mkdir(parents=True, exist_ok=True)
    archive_path = backup_root / f"backup_{label}.tar.gz"
    if archive_path.exists():
        archive_path.unlink()
    with tar.open(str(archive_path), "w:gz") as archive:
        archive.add(source_dir, arcname=source_dir.name)
    return str(archive_path)


def start_query_server(db_path: Path) -> tuple[ThreadingHTTPServer, threading.Thread, str]:
    query_app = QueryApp(db_path)
    query_server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(query_app))
    query_thread = threading.Thread(target=query_server.serve_forever, daemon=True)
    query_thread.start()
    query_base_url = f"http://127.0.0.1:{query_server.server_address[1]}"
    return query_server, query_thread, query_base_url


def stop_query_server(query_server: ThreadingHTTPServer | None, query_thread: threading.Thread | None) -> None:
    if query_server is not None:
        query_server.shutdown()
        query_server.server_close()
    if query_thread is not None:
        query_thread.join(timeout=5)


def gate_contract(*, use_fixture: bool) -> Dict[str, Any]:
    mode = "fixture" if use_fixture else "real-source"
    entrypoint = "python scripts/smoke_linked_chain.py --fixture" if use_fixture else "python scripts/smoke_linked_chain.py --mode real-source"
    trigger_conditions = [
        "Run the fixture gate on hosted CI or manual checks when a live source DB is not available."
        if use_fixture
        else "Run the real-source gate on a trusted local or self-hosted machine that has a readable source DB snapshot."
    ]
    if not use_fixture:
        trigger_conditions.append(
            (
                "The default real-source DB is the active release snapshot at "
                f"{ACTIVE_RELEASE_SOURCE_DB_RELATIVE}; use DGTEAM_SMOKE_SOURCE_DB or --source-db only when you intentionally override it, "
                f"for example to point at {LEGACY_WORKING_SOURCE_DB_RELATIVE}."
            )
        )
    return {
        "contract_version": GATE_CONTRACT_VERSION,
        "name": "candidate",
        "mode": mode,
        "entrypoint": entrypoint,
        "trigger_conditions": trigger_conditions,
        "blocking_conditions": [
            "release import or activation fails",
            "query status is not ok after activation",
            "any required smoke search returns zero results",
            "detail query returns no branches or capacity groups",
            "rollback validation is not ok",
        ],
        "rollback_conditions": [
            "bundle_b activation succeeds but query checks fail",
            "bundle_b activation succeeds but rollback validation fails",
            "post-switch validation or public query health regresses after the second activation",
        ],
    }


def _journal_snapshot(status_files: Dict[str, Any]) -> Dict[str, Any]:
    payload: Dict[str, Any] = dict(status_files or {})
    status_path_text = str(payload.get("status_path") or "").strip()
    events_path_text = str(payload.get("events_path") or "").strip()
    status_path = Path(status_path_text).expanduser() if status_path_text else None
    events_path = Path(events_path_text).expanduser() if events_path_text else None
    if status_path is not None and status_path.is_file():
        payload["status"] = read_json_utf8(status_path)
    if events_path is not None and events_path.is_file():
        event_lines = [line for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        payload["event_count"] = len(event_lines)
        payload["last_event"] = json.loads(event_lines[-1]) if event_lines else {}
    return payload


def run_smoke(
    *,
    project_root: Path,
    source_db: Path | None = None,
    use_fixture: bool = False,
    report_path: Path | None = None,
) -> dict:
    source_db_contract = resolve_real_source_db(project_root, explicit_source_db=source_db)
    source_db_path = Path(source_db_contract["resolved_path"])

    smoke_root_base = project_root / "runtime" / "local" / "integration_smoke"
    prune_old_smoke_runs(smoke_root_base, keep=2)
    stamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    smoke_root = smoke_root_base / stamp
    if smoke_root.exists():
        shutil.rmtree(smoke_root, ignore_errors=True)
    smoke_root.mkdir(parents=True, exist_ok=True)

    if use_fixture:
        smoke_source = build_fixture_source_storage(smoke_root)
        search_queries = FIXTURE_SEARCH_QUERIES
    else:
        source_storage = DGTeamStorage(source_db_path)
        source_storage.init_db()
        preferred_run_key = str(source_storage.get_preferred_run_key() or "").strip()
        if not preferred_run_key:
            raise RuntimeError(f"No completed run is available in source database: {source_db_path}")
        run_key = resolve_smoke_source_run_key(source_db_path, preferred_run_key, REAL_SAMPLE_SELECTORS)
        smoke_source = build_smoke_source_storage_from_db(
            smoke_root,
            source_db=source_db_path,
            run_key=run_key,
            selectors=REAL_SAMPLE_SELECTORS,
        )
        search_queries = REAL_SEARCH_QUERIES

    smoke_storage = smoke_source["storage"]
    run_key = str(smoke_source["run_key"])
    bundle_a_dir = smoke_root / "source_releases" / "bundle_a"
    bundle_b_dir = smoke_root / "source_releases" / "bundle_b"
    if use_fixture:
        snapshot_count = int(smoke_source.get("snapshot_count") or 0)
        bundle_a = build_fixture_release_bundle(
            smoke_storage,
            bundle_a_dir,
            run_key=run_key,
            release_id="bundle_a",
            snapshot_count=snapshot_count,
        )
        bundle_b = build_fixture_release_bundle(
            smoke_storage,
            bundle_b_dir,
            run_key=run_key,
            release_id="bundle_b",
            snapshot_count=snapshot_count,
        )
    else:
        bundle_a = build_local_release_bundle(smoke_storage, bundle_a_dir, run_key=run_key)
        shutil.rmtree(bundle_b_dir, ignore_errors=True)
        shutil.copytree(bundle_a_dir, bundle_b_dir)

    manifest_b_path = bundle_b_dir / "manifest.json"
    release_b_path = bundle_b_dir / "release.json"
    manifest_b = read_json_utf8(manifest_b_path)
    manifest_b["release_id"] = "bundle_b"
    manifest_b["published_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    write_json_utf8(manifest_b_path, manifest_b)
    release_b = read_json_utf8(release_b_path)
    release_b["release_id"] = "bundle_b"
    write_json_utf8(release_b_path, release_b)

    archive_a = archive_release_bundle(bundle_a_dir, smoke_root / "bundle_a.zip")
    archive_b = archive_release_bundle(bundle_b_dir, smoke_root / "bundle_b.zip")

    token = "integration-secret"
    settings = build_test_settings(smoke_root, token)
    store = ReleaseStore(
        settings.cloud_root,
        current_dir=settings.release.current_dir,
        previous_dir=settings.release.previous_dir,
        history_dir=settings.release.history_dir,
        staging_dir=settings.release.staging_dir,
        state_dir=settings.release.state_dir,
        uploads_dir=settings.publish_api.uploads_dir,
    )

    publish_server = ThreadingHTTPServer((settings.publish_api.host, 0), PublishApiHandler)
    publish_server.settings = settings  # type: ignore[attr-defined]
    publish_server.release_store = store  # type: ignore[attr-defined]
    publish_thread = threading.Thread(target=publish_server.serve_forever, daemon=True)
    publish_thread.start()
    publish_base_url = f"http://127.0.0.1:{publish_server.server_address[1]}"

    query_server = None
    query_thread = None
    try:
        publish_status_before = json_get(f"{publish_base_url}/api/status")
        upload_first = upload_release_bundle(
            server_url=publish_base_url,
            archive_path=Path(archive_a["archive_path"]),
            token=token,
            release_id="bundle_a",
            activate=True,
        )
        upload_second = upload_release_bundle(
            server_url=publish_base_url,
            archive_path=Path(archive_b["archive_path"]),
            token=token,
            release_id="bundle_b",
            activate=True,
        )
        activated_current_dir = Path(
            str((upload_second.get("activated") or {}).get("current_dir") or settings.release.current_dir)
        ).resolve()
        query_server, query_thread, query_base_url = start_query_server(activated_current_dir / "dgteam.db")
        activated_status_payload = json_get(f"{query_base_url}/api/status")
        if not activated_status_payload.get("ok"):
            raise RuntimeError(f"Smoke status endpoint failed after activation: {activated_status_payload}")
        activated_query_results = build_smoke_report_query_results(query_base_url, search_queries=search_queries)
        activated_query_ui = build_query_ui_acceptance(query_base_url, current_dir=activated_current_dir)
        activated_unknown_api = build_unknown_api_acceptance(query_base_url)
        wechat_query = str(
            activated_query_results["detail"].get("header_title")
            or activated_query_results.get("selected_label")
            or activated_query_results.get("selected_query")
            or search_queries[0]
        ).strip()
        refinement_hint = str(
            activated_query_results["detail"].get("default_capacity_label")
            or (activated_query_results["detail"].get("capacity_labels") or [""])[0]
        ).strip()
        activated_wechat = build_wechat_acceptance(
            settings=settings,
            db_path=activated_current_dir / "dgteam.db",
            primary_query=wechat_query,
            refinement_hint=refinement_hint,
            open_id="smoke-live-user",
        )
        stop_query_server(query_server, query_thread)
        query_server = None
        query_thread = None

        rollback_result = rollback_release(server_url=publish_base_url, token=token)
        publish_status_after = json_get(f"{publish_base_url}/api/status")

        current_dir = Path(rollback_result["rolled_back"]["current_dir"]).resolve()
        query_server, query_thread, query_base_url = start_query_server(current_dir / "dgteam.db")
        rollback_status_payload = json_get(f"{query_base_url}/api/status")
        if not rollback_status_payload.get("ok"):
            raise RuntimeError(f"Smoke status endpoint failed after rollback: {rollback_status_payload}")
        rollback_query_results = build_smoke_report_query_results(query_base_url, search_queries=search_queries)
        rollback_query_ui = build_query_ui_acceptance(query_base_url, current_dir=current_dir)
        rollback_unknown_api = build_unknown_api_acceptance(query_base_url)

        backup_root = smoke_root / "ops_backups"
        backup_archive_path = create_backup_archive(current_dir, backup_root=backup_root, label="acceptance")
        ops_audit = build_runtime_audit(project_root=smoke_root, backup_root=backup_root)
        smoke_report_path = smoke_root / "smoke_report.json"
        gate_result_path = smoke_root / "gate_result.json"
        rollback_status_files = dict((rollback_result.get("rolled_back") or {}).get("status_files") or {})
        audit_chain = {
            "smoke_report_path": str(smoke_report_path),
            "gate_result_path": str(gate_result_path),
            "publish_import_activate_journals": {
                "bundle_a": _journal_snapshot((upload_first.get("activated") or {}).get("status_files") or {}),
                "bundle_b": _journal_snapshot((upload_second.get("activated") or {}).get("status_files") or {}),
            },
            "rollback": _journal_snapshot(rollback_status_files),
            "rollback_evidence": dict((rollback_result.get("rolled_back") or {}).get("rollback_evidence") or {}),
            "ops_backup_archive": backup_archive_path,
            "ops_audit": ops_audit,
        }
        acceptance_ok = all(
            [
                bool(activated_status_payload.get("ok")),
                bool(activated_query_ui.get("ok")),
                bool(activated_unknown_api.get("ok")),
                bool(activated_wechat.get("ok")),
                bool(rollback_status_payload.get("ok")),
                bool(rollback_query_ui.get("ok")),
                bool(rollback_unknown_api.get("ok")),
                bool(((rollback_result.get("rolled_back") or {}).get("validation") or {}).get("ok")),
            ]
        )

        report = {
            "ok": acceptance_ok,
            "mode": "fixture" if use_fixture else "real-source",
            "gate": gate_contract(use_fixture=use_fixture),
            "run_key": run_key,
            "source_db": str(source_db_path if not use_fixture else smoke_source["source_db"]),
            "source_db_contract": (
                source_db_contract
                if not use_fixture
                else {
                    "contract_version": source_db_contract["contract_version"],
                    "selection": "fixture",
                    "resolved_path": str(smoke_source["source_db"]),
                    "default_relative_path": source_db_contract["default_relative_path"],
                    "default_path": source_db_contract["default_path"],
                    "legacy_override_relative_path": source_db_contract["legacy_override_relative_path"],
                    "legacy_override_path": source_db_contract["legacy_override_path"],
                    "notes": "Fixture mode bypasses the real-source DB contract and builds a synthetic source DB.",
                }
            ),
            "smoke_root": str(smoke_root),
            "sample_source": {
                key: value
                for key, value in smoke_source.items()
                if key != "storage"
            },
            "bundle_a": bundle_a,
            "bundle_a_archive": archive_a,
            "bundle_b_archive": archive_b,
            "publish_status_before": publish_status_before,
            "upload_first": upload_first,
            "upload_second": upload_second,
            "rollback": rollback_result,
            "publish_status_after": publish_status_after,
            "query_status": rollback_status_payload,
            "query_checks": rollback_query_results,
            "activation_checks": {
                "current_dir": str(activated_current_dir),
                "query_status": activated_status_payload,
                "query_checks": activated_query_results,
                "query_ui": activated_query_ui,
                "unknown_api": activated_unknown_api,
                "wechat": activated_wechat,
            },
            "rollback_checks": {
                "current_dir": str(current_dir),
                "query_status": rollback_status_payload,
                "query_checks": rollback_query_results,
                "query_ui": rollback_query_ui,
                "unknown_api": rollback_unknown_api,
            },
            "audit_chain": audit_chain,
        }
        gate_result = {
            "ok": acceptance_ok,
            "contract_version": GATE_CONTRACT_VERSION,
            "gate": report["gate"],
            "run_key": run_key,
            "mode": report["mode"],
            "source_db": report["source_db"],
            "smoke_root": report["smoke_root"],
            "smoke_report_path": str(smoke_report_path),
            "audit_chain": audit_chain,
            "query_checks": report["query_checks"],
            "activation_checks": report["activation_checks"],
            "rollback_checks": report["rollback_checks"],
        }
        write_json_utf8(smoke_report_path, report)
        write_json_utf8(gate_result_path, gate_result)
        if report_path is not None:
            write_json_utf8(report_path, report)
        return report
    finally:
        if query_server is not None:
            query_server.shutdown()
            query_server.server_close()
        if query_thread is not None:
            query_thread.join(timeout=5)
        publish_server.shutdown()
        publish_server.server_close()
        publish_thread.join(timeout=5)


def main() -> None:
    configure_utf8_stdio()
    args = parse_args()
    use_fixture = bool(args.fixture) or str(args.mode or "").strip() == "fixture"
    report = run_smoke(
        project_root=Path(args.project_root).expanduser().resolve(),
        source_db=Path(args.source_db).expanduser().resolve() if str(args.source_db).strip() else None,
        use_fixture=use_fixture,
        report_path=Path(args.report_path).expanduser().resolve() if str(args.report_path).strip() else None,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
