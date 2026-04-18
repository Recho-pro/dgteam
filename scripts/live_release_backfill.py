from __future__ import annotations

import argparse
import json
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Sequence

from dgteam.core.config import Settings, load_settings
from dgteam.core.console_encoding import configure_utf8_stdio
from dgteam.core.storage import DGTeamStorage
from dgteam.core.textio import read_json_utf8, write_json_utf8
from dgteam.integrations.wechat_official.models import WechatOfficialInboundMessage
from dgteam.integrations.wechat_official.service import WechatOfficialService
from dgteam.ops.real_source_db import resolve_real_source_db
from dgteam.publish_api.release_store import ReleaseStore
from dgteam.query_api.ui_assets import RELEASE_UI_DIRNAME, read_ui_asset_manifest
from dgteam.release.builder import archive_release_bundle, build_local_release_bundle
from dgteam.release.upload_client import activate_release, rollback_release, upload_release_bundle


REAL_SEARCH_QUERIES: Sequence[str] = (
    "iphone17pm",
    "pingguo17pm",
    "redmi k80",
    "hongmik80",
    "iqoo15",
)


def _default_project_root() -> Path:
    cwd = Path.cwd().resolve()
    if (cwd / "src" / "dgteam").exists():
        return cwd
    return Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill a legacy live release into the standard query_ui-aware lifecycle.")
    parser.add_argument("--project-root", default=str(_default_project_root()))
    parser.add_argument("--source-db", default="", help="Optional explicit source DB override. Defaults to the active release DB truth.")
    parser.add_argument("--server-url", default="http://127.0.0.1:9865")
    parser.add_argument("--query-base-url", default="http://127.0.0.1:9765")
    parser.add_argument("--public-base-url", default="https://dgtdnb.com")
    parser.add_argument("--token", default="", help="Publish API token. Defaults to the loaded project settings token.")
    parser.add_argument("--release-prefix", default="standard_backfill")
    parser.add_argument("--report-path", default="")
    parser.add_argument("--activation-wait-seconds", type=float, default=2.0)
    parser.add_argument("--keep-local-builds", action="store_true")
    parser.add_argument("--skip-final-reactivate", action="store_true")
    return parser.parse_args()


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


def json_get(url: str) -> Dict[str, Any]:
    response = http_get(url)
    if not response["ok"]:
        raise RuntimeError(f"HTTP GET failed status={response['status']} url={url} body={response['body_text']}")
    return json.loads(response["body_text"])


def header_value(headers: Dict[str, Any], name: str) -> str:
    expected = str(name or "").strip().lower()
    for key, value in dict(headers or {}).items():
        if str(key or "").strip().lower() == expected:
            return str(value or "")
    return ""


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


def query_result_acceptance(query_base_url: str, *, search_queries: Sequence[str]) -> Dict[str, Any]:
    searches: Dict[str, Any] = {}
    selected_detail_key = ""
    selected_label = ""
    selected_query = ""
    for query in search_queries:
        payload = json_get(f"{query_base_url.rstrip('/')}/api/search?q={urllib.parse.quote(query, safe='')}&limit=3")
        summary = summarize_search_payload(payload)
        searches[query] = summary
        if summary["ok"] and int(summary["result_count"]) > 0 and not selected_detail_key:
            selected_detail_key = summary["first_detail_key"]
            selected_label = summary["first_label"]
            selected_query = query

    if not selected_detail_key:
        raise RuntimeError("No smoke search query returned a usable detail key.")

    detail_payload = json_get(
        f"{query_base_url.rstrip('/')}/api/sku?detail_key={urllib.parse.quote(selected_detail_key, safe='')}"
    )
    if not detail_payload.get("ok"):
        raise RuntimeError(f"Detail query failed: {detail_payload}")
    branches = list(detail_payload.get("branches") or [])
    if not branches:
        raise RuntimeError(f"Detail query returned no branches: {detail_payload}")
    first_branch = dict(branches[0] or {})
    capacity_groups = list(first_branch.get("capacity_groups") or [])
    if not capacity_groups:
        raise RuntimeError(f"Detail query returned no capacity groups: {detail_payload}")
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


def query_ui_acceptance(base_url: str, *, current_dir: Path) -> Dict[str, Any]:
    asset_dir = current_dir / RELEASE_UI_DIRNAME
    asset_manifest = read_ui_asset_manifest(asset_dir)
    version = str(asset_manifest.get("version") or "").strip()
    expected_styles_href = f"./styles.css?v={version}" if version else ""
    expected_app_href = f"./app.js?v={version}" if version else ""

    index_response = http_get(f"{base_url.rstrip('/')}/")
    styles_response = http_get(
        f"{base_url.rstrip('/')}/styles.css?v={urllib.parse.quote(version, safe='')}" if version else f"{base_url.rstrip('/')}/styles.css"
    )
    app_response = http_get(
        f"{base_url.rstrip('/')}/app.js?v={urllib.parse.quote(version, safe='')}" if version else f"{base_url.rstrip('/')}/app.js"
    )
    manifest_response = http_get(f"{base_url.rstrip('/')}/asset-manifest.json")

    index_cache = header_value(index_response["headers"], "Cache-Control")
    styles_cache = header_value(styles_response["headers"], "Cache-Control")
    app_cache = header_value(app_response["headers"], "Cache-Control")
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
        "expected_styles_href": expected_styles_href,
        "expected_app_href": expected_app_href,
    }


def unknown_api_acceptance(base_url: str) -> Dict[str, Any]:
    response = http_get(f"{base_url.rstrip('/')}/api/unknown-route")
    payload = json.loads(response["body_text"] or "{}")
    ok = int(response["status"]) == 404 and payload.get("error_code") == "unknown_api_endpoint"
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
        to_user="dgteam-backfill",
        content=content,
        media_id="",
        pic_url="",
        msg_id=msg_id,
        raw_payload={"backfill": True},
    )


def wechat_acceptance(
    *,
    settings: Settings,
    db_path: Path,
    primary_query: str,
    refinement_hint: str,
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
        "final_session": final_session,
    }


def _build_store(settings: Settings) -> ReleaseStore:
    return ReleaseStore(
        settings.cloud_root,
        current_dir=settings.release.current_dir,
        previous_dir=settings.release.previous_dir,
        history_dir=settings.release.history_dir,
        staging_dir=settings.release.staging_dir,
        state_dir=settings.release.state_dir,
        uploads_dir=settings.publish_api.uploads_dir,
    )


def _current_previous_state(store: ReleaseStore) -> Dict[str, Any]:
    summary = store.summary()
    return {
        "summary": summary,
        "current_manifest": dict(summary.get("current") or {}),
        "previous_manifest": dict(summary.get("previous") or {}),
        "current_validation": dict(summary.get("current_validation") or {}),
        "previous_validation": dict(summary.get("previous_validation") or {}),
    }


def _build_release(
    *,
    source_db: Path,
    release_dir: Path,
    run_key: str,
) -> Dict[str, Any]:
    if release_dir.exists():
        shutil.rmtree(release_dir, ignore_errors=True)
    archive_path = release_dir.with_suffix(".zip")
    if archive_path.exists():
        archive_path.unlink()
    storage = DGTeamStorage(source_db)
    storage.init_db()
    bundle = build_local_release_bundle(storage, release_dir, run_key=run_key)
    archive = archive_release_bundle(release_dir, archive_path)
    result = {
        "bundle": bundle,
        "archive": archive,
        "local_paths": {
            "release_dir": str(release_dir),
            "archive_path": str(archive_path),
        },
    }
    return result


def _cleanup_release_artifacts(release_dir: Path) -> Dict[str, Any]:
    archive_path = release_dir.with_suffix(".zip")
    release_removed = False
    archive_removed = False
    if release_dir.exists():
        shutil.rmtree(release_dir, ignore_errors=True)
        release_removed = not release_dir.exists()
    if archive_path.exists():
        archive_path.unlink(missing_ok=True)
        archive_removed = not archive_path.exists()
    return {
        "release_dir_removed": release_removed,
        "archive_removed": archive_removed,
    }


def _upload_then_activate(
    *,
    server_url: str,
    token: str,
    archive_path: Path,
    release_id: str,
) -> Dict[str, Any]:
    uploaded = upload_release_bundle(
        server_url=server_url,
        archive_path=archive_path,
        token=token,
        release_id=release_id,
        activate=False,
    )
    activated = activate_release(
        server_url=server_url,
        token=token,
        release_id=release_id,
    )
    return {
        "uploaded": uploaded,
        "activated": activated,
    }


def run_backfill(
    *,
    project_root: Path,
    source_db: Path | None,
    server_url: str,
    query_base_url: str,
    public_base_url: str,
    token: str,
    release_prefix: str,
    activation_wait_seconds: float,
    keep_local_builds: bool,
    final_reactivate: bool,
    report_path: Path | None,
) -> Dict[str, Any]:
    settings = load_settings(project_root=project_root)
    store = _build_store(settings)
    source_db_contract = resolve_real_source_db(project_root, explicit_source_db=source_db)
    source_db_path = Path(source_db_contract["resolved_path"]).expanduser().resolve()

    source_storage = DGTeamStorage(source_db_path)
    source_storage.init_db()
    run_key = str(source_storage.get_preferred_run_key() or "").strip()
    if not run_key:
        raise RuntimeError(f"No completed run is available in source database: {source_db_path}")

    stamp = time.strftime("%Y%m%d_%H%M%S")
    release_id_a = f"{release_prefix}_{stamp}_a"
    release_id_b = f"{release_prefix}_{stamp}_b"
    release_dir_a = settings.local_root / "releases" / release_id_a
    release_dir_b = settings.local_root / "releases" / release_id_b

    state_before = _current_previous_state(store)
    legacy_current_has_ui = bool((settings.release.current_dir / RELEASE_UI_DIRNAME / "asset-manifest.json").is_file())

    build_a = _build_release(
        source_db=source_db_path,
        release_dir=release_dir_a,
        run_key=run_key,
    )
    upload_a = _upload_then_activate(
        server_url=server_url,
        token=token,
        archive_path=Path(build_a["archive"]["archive_path"]),
        release_id=release_id_a,
    )
    build_a["local_cleanup"] = (
        _cleanup_release_artifacts(release_dir_a) if not keep_local_builds else {"release_dir_removed": False, "archive_removed": False}
    )
    time.sleep(max(0.0, activation_wait_seconds))

    build_b = _build_release(
        source_db=source_db_path,
        release_dir=release_dir_b,
        run_key=run_key,
    )
    upload_b = _upload_then_activate(
        server_url=server_url,
        token=token,
        archive_path=Path(build_b["archive"]["archive_path"]),
        release_id=release_id_b,
    )
    build_b["local_cleanup"] = (
        _cleanup_release_artifacts(release_dir_b) if not keep_local_builds else {"release_dir_removed": False, "archive_removed": False}
    )
    time.sleep(max(0.0, activation_wait_seconds))

    state_after_second = _current_previous_state(store)
    activated_current_dir = settings.release.current_dir
    activated_query_status = json_get(f"{query_base_url.rstrip('/')}/api/status")
    activated_public_status = json_get(f"{public_base_url.rstrip('/')}/api/status")
    activated_publish_status = json_get(f"{server_url.rstrip('/')}/api/status")
    activated_query_checks = query_result_acceptance(public_base_url, search_queries=REAL_SEARCH_QUERIES)
    activated_query_ui = query_ui_acceptance(public_base_url, current_dir=activated_current_dir)
    activated_unknown_api = unknown_api_acceptance(public_base_url)
    activated_wechat = wechat_acceptance(
        settings=settings,
        db_path=activated_current_dir / "dgteam.db",
        primary_query=str(
            activated_query_checks["detail"].get("header_title")
            or activated_query_checks.get("selected_label")
            or activated_query_checks.get("selected_query")
            or REAL_SEARCH_QUERIES[0]
        ).strip(),
        refinement_hint=str(
            activated_query_checks["detail"].get("default_capacity_label")
            or (activated_query_checks["detail"].get("capacity_labels") or [""])[0]
        ).strip(),
        open_id=f"backfill-{stamp}-activated",
    )

    rollback_payload = rollback_release(server_url=server_url, token=token)
    time.sleep(max(0.0, activation_wait_seconds))

    state_after_rollback = _current_previous_state(store)
    rollback_current_dir = settings.release.current_dir
    rollback_query_status = json_get(f"{query_base_url.rstrip('/')}/api/status")
    rollback_public_status = json_get(f"{public_base_url.rstrip('/')}/api/status")
    rollback_query_checks = query_result_acceptance(public_base_url, search_queries=REAL_SEARCH_QUERIES)
    rollback_query_ui = query_ui_acceptance(public_base_url, current_dir=rollback_current_dir)
    rollback_unknown_api = unknown_api_acceptance(public_base_url)

    final_reactivate_payload: Dict[str, Any] = {}
    if final_reactivate:
        archived_current_dir_text = str(
            ((rollback_payload.get("rolled_back") or {}).get("rollback_evidence") or {}).get("archived_current_dir") or ""
        ).strip()
        archived_current_dir = Path(archived_current_dir_text).expanduser().resolve() if archived_current_dir_text else None
        reimport_payload: Dict[str, Any] = {}
        if archived_current_dir is not None and archived_current_dir.exists():
            reimport_payload = store.import_local_release(archived_current_dir, release_id=release_id_b)
        final_reactivate_payload = {
            "reimported": reimport_payload,
            "activated": activate_release(server_url=server_url, token=token, release_id=release_id_b),
        }
        time.sleep(max(0.0, activation_wait_seconds))

    final_state = _current_previous_state(store)
    final_current_dir = settings.release.current_dir
    final_query_status = json_get(f"{query_base_url.rstrip('/')}/api/status")
    final_public_status = json_get(f"{public_base_url.rstrip('/')}/api/status")
    final_publish_status = json_get(f"{server_url.rstrip('/')}/api/status")
    final_query_checks = query_result_acceptance(public_base_url, search_queries=REAL_SEARCH_QUERIES)
    final_query_ui = query_ui_acceptance(public_base_url, current_dir=final_current_dir)
    final_unknown_api = unknown_api_acceptance(public_base_url)
    final_wechat = wechat_acceptance(
        settings=settings,
        db_path=final_current_dir / "dgteam.db",
        primary_query=str(
            final_query_checks["detail"].get("header_title")
            or final_query_checks.get("selected_label")
            or final_query_checks.get("selected_query")
            or REAL_SEARCH_QUERIES[0]
        ).strip(),
        refinement_hint=str(
            final_query_checks["detail"].get("default_capacity_label")
            or (final_query_checks["detail"].get("capacity_labels") or [""])[0]
        ).strip(),
        open_id=f"backfill-{stamp}-final",
    )

    current_validation = dict(final_state.get("current_validation") or {})
    previous_validation = dict(final_state.get("previous_validation") or {})
    current_manifest = dict(final_state.get("current_manifest") or {})
    previous_manifest = dict(final_state.get("previous_manifest") or {})

    ok = all(
        [
            current_validation.get("ok"),
            previous_validation.get("ok"),
            current_validation.get("query_ui", {}).get("required"),
            current_validation.get("query_ui", {}).get("ok"),
            previous_validation.get("query_ui", {}).get("required"),
            previous_validation.get("query_ui", {}).get("ok"),
            activated_query_status.get("ok"),
            activated_public_status.get("ok"),
            activated_publish_status.get("ok"),
            activated_query_ui.get("ok"),
            activated_unknown_api.get("ok"),
            activated_wechat.get("ok"),
            rollback_query_status.get("ok"),
            rollback_public_status.get("ok"),
            rollback_query_ui.get("ok"),
            rollback_unknown_api.get("ok"),
            final_query_status.get("ok"),
            final_public_status.get("ok"),
            final_publish_status.get("ok"),
            final_query_ui.get("ok"),
            final_unknown_api.get("ok"),
            final_wechat.get("ok"),
            current_manifest.get("release_id") == release_id_b if final_reactivate else True,
            previous_manifest.get("release_id") == release_id_a,
        ]
    )

    report = {
        "ok": bool(ok),
        "contract_version": "dgteam-live-release-backfill.v1",
        "project_root": str(project_root),
        "server_url": server_url,
        "query_base_url": query_base_url,
        "public_base_url": public_base_url,
        "source_db": str(source_db_path),
        "source_db_contract": source_db_contract,
        "run_key": run_key,
        "release_ids": {
            "bundle_a": release_id_a,
            "bundle_b": release_id_b,
        },
        "release_backfill": {
            "legacy_current_had_query_ui": legacy_current_has_ui,
            "built_from_source_db": str(source_db_path),
            "build_a": build_a,
            "build_b": build_b,
            "upload_a": upload_a,
            "upload_b": upload_b,
            "final_reactivate": final_reactivate_payload,
        },
        "current_previous_state": {
            "before": state_before,
            "after_second_activation": state_after_second,
            "after_rollback": state_after_rollback,
            "final": final_state,
        },
        "activation_checks": {
            "query_status": activated_query_status,
            "public_status": activated_public_status,
            "publish_status": activated_publish_status,
            "query_checks": activated_query_checks,
            "query_ui": activated_query_ui,
            "unknown_api": activated_unknown_api,
            "wechat": activated_wechat,
        },
        "rollback_evidence": {
            "rollback_payload": rollback_payload,
            "rollback_query_status": rollback_query_status,
            "rollback_public_status": rollback_public_status,
            "rollback_query_checks": rollback_query_checks,
            "rollback_query_ui": rollback_query_ui,
            "rollback_unknown_api": rollback_unknown_api,
        },
        "final_checks": {
            "query_status": final_query_status,
            "public_status": final_public_status,
            "publish_status": final_publish_status,
            "query_checks": final_query_checks,
            "query_ui": final_query_ui,
            "unknown_api": final_unknown_api,
            "wechat": final_wechat,
        },
        "fallback_retirement": {
            "legacy_current_had_query_ui": legacy_current_has_ui,
            "final_current_query_ui_required": bool(current_validation.get("query_ui", {}).get("required")),
            "final_current_query_ui_ok": bool(current_validation.get("query_ui", {}).get("ok")),
            "final_previous_query_ui_required": bool(previous_validation.get("query_ui", {}).get("required")),
            "final_previous_query_ui_ok": bool(previous_validation.get("query_ui", {}).get("ok")),
            "final_current_release_id": str(current_manifest.get("release_id") or ""),
            "final_previous_release_id": str(previous_manifest.get("release_id") or ""),
        },
    }
    if report_path is not None:
        write_json_utf8(report_path, report)
    return report


def main() -> None:
    configure_utf8_stdio()
    args = parse_args()
    project_root = Path(args.project_root).expanduser().resolve()
    settings = load_settings(project_root=project_root)
    token = str(args.token or settings.publish_api.shared_token or "").strip()
    if not token:
        raise RuntimeError("Publish API token is empty. Pass --token or configure DGTEAM_PUBLISH_TOKEN.")
    report = run_backfill(
        project_root=project_root,
        source_db=Path(args.source_db).expanduser().resolve() if str(args.source_db or "").strip() else None,
        server_url=str(args.server_url or "").strip(),
        query_base_url=str(args.query_base_url or "").strip(),
        public_base_url=str(args.public_base_url or "").strip(),
        token=token,
        release_prefix=str(args.release_prefix or "").strip() or "standard_backfill",
        activation_wait_seconds=float(args.activation_wait_seconds),
        keep_local_builds=bool(args.keep_local_builds),
        final_reactivate=not bool(args.skip_final_reactivate),
        report_path=Path(args.report_path).expanduser().resolve() if str(args.report_path or "").strip() else None,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
