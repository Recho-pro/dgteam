from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Optional

from dgteam.agent.pipeline import run_pipeline
from dgteam.core.console_encoding import configure_utf8_stdio
from dgteam.release.sync_cli import sync_release


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_AUTOMATION_HOME = PROJECT_ROOT / "config" / "automation" / "prod"


class Tee:
    def __init__(self, *streams: object):
        self.streams = streams

    def write(self, data: str) -> int:
        for stream in self.streams:
            stream.write(data)
            stream.flush()
        return len(data)

    def flush(self) -> None:
        for stream in self.streams:
            stream.flush()


@dataclass(frozen=True)
class ProductionPaths:
    automation_home: Path
    runtime_root: Path
    local_root: Path
    cloud_root: Path
    db_path: Path
    profile_dir: Path
    runs_dir: Path
    releases_dir: Path
    cache_dir: Path
    catalog_cache_dir: Path
    ocr_cache_file: Path
    ops_root: Path
    state_dir: Path
    log_dir: Path
    auth_path: Path
    profile_env_path: Path
    sync_env_path: Path
    pending_sync_path: Path
    lock_path: Path
    current_run_path: Path

    def to_dict(self) -> Dict[str, str]:
        return {key: str(value) for key, value in asdict(self).items()}


def read_env_file(path: Path) -> Dict[str, str]:
    payload: Dict[str, str] = {}
    if not path.exists():
        return payload
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        payload[key.strip()] = value.strip()
    return payload


def write_json(path: Path, payload: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def acquire_lock(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError(f"Production automation is already active: {path}") from exc
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(f"pid={os.getpid()}\n")
        fh.write(f"started_at={time.strftime('%Y-%m-%d %H:%M:%S')}\n")


def release_lock(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def parse_bool(value: str, default: bool) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return bool(default)
    return text in {"1", "true", "yes", "on"}


def parse_int(value: str, default: int) -> int:
    text = str(value or "").strip()
    if not text:
        return int(default)
    return int(text)


def parse_float(value: str, default: float) -> float:
    text = str(value or "").strip()
    if not text:
        return float(default)
    return float(text)


def resolve_automation_home(project_root: Path, requested: str = "") -> Path:
    env_value = str(os.environ.get("DGTEAM_AUTOMATION_HOME") or "").strip()
    chosen = str(requested or env_value).strip()
    if chosen:
        return Path(chosen).expanduser().resolve()
    return DEFAULT_AUTOMATION_HOME.resolve()


def build_paths(project_root: Path, automation_home: Path) -> ProductionPaths:
    runtime_root = project_root / "runtime"
    local_root = runtime_root / "local"
    cloud_root = runtime_root / "cloud"
    ops_root = local_root / "automation" / "prod"
    return ProductionPaths(
        automation_home=automation_home,
        runtime_root=runtime_root,
        local_root=local_root,
        cloud_root=cloud_root,
        db_path=local_root / "data" / "dgteam.db",
        profile_dir=local_root / "browser_profile",
        runs_dir=local_root / "runs",
        releases_dir=local_root / "releases",
        cache_dir=local_root / "cache",
        catalog_cache_dir=local_root / "cache" / "catalog",
        ocr_cache_file=local_root / "cache" / "ocr_price_cache.jsonl",
        ops_root=ops_root,
        state_dir=ops_root / "state",
        log_dir=ops_root / "logs",
        auth_path=automation_home / "auth.json",
        profile_env_path=automation_home / "profile.env",
        sync_env_path=automation_home / "sync.env",
        pending_sync_path=(ops_root / "state" / "pending_sync.json"),
        lock_path=(ops_root / "state" / "collect_and_sync.lock"),
        current_run_path=(ops_root / "state" / "current_run.json"),
    )


def load_profile(paths: ProductionPaths) -> Dict[str, object]:
    profile_env = read_env_file(paths.profile_env_path)
    return {
        "brand_filter": str(profile_env.get("DGTEAM_AUTOMATION_BRAND_FILTER") or "").strip(),
        "series_filter": str(profile_env.get("DGTEAM_AUTOMATION_SERIES_FILTER") or "").strip(),
        "history_days": parse_int(str(profile_env.get("DGTEAM_AUTOMATION_HISTORY_DAYS") or ""), 3),
        "delay_seconds": parse_float(str(profile_env.get("DGTEAM_AUTOMATION_DELAY_SECONDS") or ""), 0.05),
        "process_workers": parse_int(str(profile_env.get("DGTEAM_AUTOMATION_PROCESS_WORKERS") or ""), 2),
        "request_workers": parse_int(str(profile_env.get("DGTEAM_AUTOMATION_REQUEST_WORKERS") or ""), 1),
        "max_tasks": parse_int(str(profile_env.get("DGTEAM_AUTOMATION_MAX_TASKS") or ""), 0),
        "activate": parse_bool(str(profile_env.get("DGTEAM_AUTOMATION_ACTIVATE") or ""), True),
        "release_prefix": str(profile_env.get("DGTEAM_AUTOMATION_RELEASE_PREFIX") or "").strip(),
    }


def effective_value(cli_value: object, env_value: str, fallback: object) -> object:
    if isinstance(cli_value, str) and cli_value.strip():
        return cli_value.strip()
    if isinstance(cli_value, (int, float)) and cli_value >= 0:
        return cli_value
    if str(env_value or "").strip():
        return env_value.strip()
    return fallback


def build_release_id(prefix: str) -> str:
    clean_prefix = str(prefix or "").strip()
    stamp = time.strftime("%Y-%m-%dT%H-%M-%S")
    return f"{clean_prefix}_{stamp}" if clean_prefix else f"release_{stamp}"


def build_crawler_args(
    *,
    run_outdir: Path,
    paths: ProductionPaths,
    brand_filter: str,
    series_filter: str,
    history_days: int,
    delay_seconds: float,
    process_workers: int,
    request_workers: int,
    max_tasks: int,
) -> list[str]:
    args = [
        "--outdir",
        str(run_outdir),
        "--profile-dir",
        str(paths.profile_dir),
        "--catalog-cache-dir",
        str(paths.catalog_cache_dir),
        "--ocr-cache-file",
        str(paths.ocr_cache_file),
        "--sqlite-db",
        str(paths.db_path),
        "--history-days",
        str(history_days),
        "--delay",
        str(delay_seconds),
        "--process-workers",
        str(process_workers),
        "--request-workers",
        str(request_workers),
    ]
    if brand_filter:
        args.extend(["--brand-filter", brand_filter])
    if series_filter:
        args.extend(["--series-filter", series_filter])
    if max_tasks > 0:
        args.extend(["--max-tasks", str(max_tasks)])
    return args


def ensure_dirs(paths: ProductionPaths) -> None:
    for path in [
        paths.automation_home,
        paths.state_dir,
        paths.log_dir,
        paths.db_path.parent,
        paths.profile_dir,
        paths.runs_dir,
        paths.releases_dir,
        paths.cache_dir,
        paths.catalog_cache_dir,
        paths.cloud_root,
    ]:
        path.mkdir(parents=True, exist_ok=True)


def _dir_has_entries(path: Path) -> bool:
    return path.exists() and any(path.iterdir())


def _copytree_replace(source: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)


def migrate_legacy_runtime_if_needed(paths: ProductionPaths) -> Dict[str, object]:
    legacy_local_root = paths.automation_home / "runtime" / "local"
    if not legacy_local_root.exists():
        return {"migrated": False, "reason": "legacy_runtime_missing"}

    migrated: Dict[str, str] = {}

    legacy_profile = legacy_local_root / "browser_profile"
    if legacy_profile.exists():
        profile_target = paths.profile_dir
        if not _dir_has_entries(profile_target) or legacy_profile.stat().st_mtime > profile_target.stat().st_mtime:
            _copytree_replace(legacy_profile, profile_target)
            migrated["browser_profile"] = str(profile_target)

    legacy_catalog = legacy_local_root / "cache" / "catalog"
    if legacy_catalog.exists():
        catalog_target = paths.catalog_cache_dir
        if not _dir_has_entries(catalog_target) or legacy_catalog.stat().st_mtime > catalog_target.stat().st_mtime:
            _copytree_replace(legacy_catalog, catalog_target)
            migrated["catalog_cache_dir"] = str(catalog_target)

    legacy_ocr = legacy_local_root / "cache" / "ocr_price_cache.jsonl"
    if legacy_ocr.exists():
        ocr_target = paths.ocr_cache_file
        if not ocr_target.exists() or legacy_ocr.stat().st_mtime > ocr_target.stat().st_mtime:
            ocr_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(legacy_ocr, ocr_target)
            migrated["ocr_cache_file"] = str(ocr_target)

    return {
        "migrated": bool(migrated),
        "legacy_local_root": str(legacy_local_root),
        "items": migrated,
    }


def retry_pending_sync_if_needed(
    *,
    pending_path: Path,
    server_url: str,
    token: str,
    project_root: Path,
    activate: bool,
) -> Optional[Dict[str, object]]:
    if not pending_path.exists():
        return None
    pending = json.loads(pending_path.read_text(encoding="utf-8"))
    release_dir = Path(str(pending.get("release_dir") or "")).expanduser().resolve()
    release_id = str(pending.get("release_id") or "").strip()
    if not release_dir.exists() or not release_id:
        raise FileNotFoundError(f"Pending release is no longer available: {release_dir}")
    sync_payload = sync_release(
        server_url=server_url,
        token=token,
        release_id=release_id,
        release_dir=release_dir,
        project_root=project_root,
        activate=activate,
    )
    pending_path.unlink()
    return sync_payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the formal DGTEAM production automation path.")
    parser.add_argument("--automation-home", default="")
    parser.add_argument("--brand-filter", default="")
    parser.add_argument("--series-filter", default="")
    parser.add_argument("--history-days", type=int, default=-1)
    parser.add_argument("--delay-seconds", type=float, default=-1)
    parser.add_argument("--process-workers", type=int, default=-1)
    parser.add_argument("--request-workers", type=int, default=-1)
    parser.add_argument("--max-tasks", type=int, default=-1)
    parser.add_argument("--server-url", default="")
    parser.add_argument("--token", default="")
    parser.add_argument("--release-prefix", default="")
    parser.add_argument("--skip-sync", action="store_true")
    parser.add_argument("--no-activate", action="store_true")
    parser.add_argument("--skip-pending-retry", action="store_true")
    parser.add_argument("--print-paths", action="store_true")
    return parser


def main() -> None:
    configure_utf8_stdio()
    args = build_parser().parse_args()
    automation_home = resolve_automation_home(PROJECT_ROOT, args.automation_home)
    paths = build_paths(PROJECT_ROOT, automation_home)

    if args.print_paths:
        print(json.dumps(paths.to_dict(), ensure_ascii=False, indent=2))
        return

    ensure_dirs(paths)
    if not paths.auth_path.exists():
        raise FileNotFoundError(f"Missing auth.json: {paths.auth_path}")

    migration_note = migrate_legacy_runtime_if_needed(paths)
    sync_env = read_env_file(paths.sync_env_path)
    profile = load_profile(paths)

    brand_filter = str(effective_value(args.brand_filter, str(profile["brand_filter"]), "") or "")
    series_filter = str(effective_value(args.series_filter, str(profile["series_filter"]), "") or "")
    history_days = int(effective_value(args.history_days, str(profile["history_days"]), 3))
    delay_seconds = float(effective_value(args.delay_seconds, str(profile["delay_seconds"]), 0.05))
    process_workers = int(effective_value(args.process_workers, str(profile["process_workers"]), 2))
    request_workers = int(effective_value(args.request_workers, str(profile["request_workers"]), 1))
    max_tasks = int(effective_value(args.max_tasks, str(profile["max_tasks"]), 0))
    release_prefix = str(effective_value(args.release_prefix, str(profile["release_prefix"]), "auto") or "")
    activate = bool(profile["activate"]) and not bool(args.no_activate)
    server_url = str(args.server_url or sync_env.get("DGTEAM_PROD_PUBLISH_SERVER_URL") or "").strip()
    token = str(args.token or sync_env.get("DGTEAM_PROD_PUBLISH_TOKEN") or "").strip()

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    run_outdir = paths.runs_dir / f"prod_{timestamp}"
    log_path = paths.log_dir / f"collect_and_sync_{timestamp}.log"

    crawler_args = build_crawler_args(
        run_outdir=run_outdir,
        paths=paths,
        brand_filter=brand_filter,
        series_filter=series_filter,
        history_days=history_days,
        delay_seconds=delay_seconds,
        process_workers=process_workers,
        request_workers=request_workers,
        max_tasks=max_tasks,
    )

    acquire_lock(paths.lock_path)
    try:
        with log_path.open("w", encoding="utf-8") as log_fh:
            with contextlib.redirect_stdout(Tee(sys.stdout, log_fh)), contextlib.redirect_stderr(Tee(sys.stderr, log_fh)):
                running_payload = {
                    "ok": None,
                    "status": "running",
                    "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "log_path": str(log_path),
                    "run_outdir": str(run_outdir),
                    "db_path": str(paths.db_path),
                    "automation_home": str(paths.automation_home),
                    "brand_filter": brand_filter,
                    "series_filter": series_filter,
                    "history_days": history_days,
                    "delay_seconds": delay_seconds,
                    "process_workers": process_workers,
                    "request_workers": request_workers,
                    "max_tasks": max_tasks,
                    "activate": activate,
                    "server_url": server_url,
                }
                write_json(paths.current_run_path, running_payload)
                print(f"[prod-auto] automation_home={paths.automation_home}")
                print(f"[prod-auto] project_root={PROJECT_ROOT}")
                print(f"[prod-auto] db_path={paths.db_path}")
                print(f"[prod-auto] run_outdir={run_outdir}")
                print(f"[prod-auto] releases_dir={paths.releases_dir}")
                print(f"[prod-auto] brand_filter={brand_filter or '(all)'}")
                print(f"[prod-auto] series_filter={series_filter or '(all)'}")
                print(f"[prod-auto] activate={activate}")
                print(f"[prod-auto] server_url={server_url or '(disabled)'}")
                print(f"[prod-auto] migration_note={json.dumps(migration_note, ensure_ascii=False)}")

                env_overrides = {
                    "DGTEAM_AUTH_FILE": str(paths.auth_path),
                    "DGTEAM_LOCAL_ROOT": str(paths.local_root),
                    "DGTEAM_CLOUD_ROOT": str(paths.cloud_root),
                    "DGTEAM_AGENT_PROFILE_DIR": str(paths.profile_dir),
                    "DGTEAM_AGENT_RUNS_DIR": str(paths.runs_dir),
                    "DGTEAM_AGENT_RELEASES_DIR": str(paths.releases_dir),
                    "DGTEAM_DB_PATH": str(paths.db_path),
                    "DGTEAM_RULES_PATH": str(PROJECT_ROOT / "rules" / "default_rules.json"),
                    "DGTEAM_BLACKLIST_PATH": str(PROJECT_ROOT / "config" / "model_blacklist.csv"),
                    "DGTEAM_RETENTION_ENABLED": "true",
                    "DGTEAM_KEEP_LOCAL_RELEASES": "1",
                    "DGTEAM_KEEP_LOCAL_RELEASE_ARCHIVES": "0",
                    "DGTEAM_KEEP_INTEGRATION_SMOKE_RUNS": "1",
                    "DGTEAM_KEEP_CLOUD_RELEASES": "0",
                    "DGTEAM_KEEP_CLOUD_ROLLBACKS": "1",
                    "DGTEAM_PRUNE_CLOUD_UPLOADS": "true",
                }

                previous_env = {key: os.environ.get(key) for key in env_overrides}
                try:
                    os.environ.update(env_overrides)

                    retried_sync: Dict[str, object] = {}
                    if not args.skip_pending_retry and server_url and token:
                        retry_result = retry_pending_sync_if_needed(
                            pending_path=paths.pending_sync_path,
                            server_url=server_url,
                            token=token,
                            project_root=PROJECT_ROOT,
                            activate=activate,
                        )
                        if retry_result:
                            retried_sync = retry_result
                            print(f"[prod-auto] pending release synced successfully: {retry_result.get('release_id')}")

                    pipeline_result = run_pipeline(
                        "collect-and-publish",
                        project_root=PROJECT_ROOT,
                        crawler_args=crawler_args,
                        release_id=build_release_id(release_prefix),
                    )
                finally:
                    for key, old_value in previous_env.items():
                        if old_value is None:
                            os.environ.pop(key, None)
                        else:
                            os.environ[key] = old_value

                pipeline_payload = dict(pipeline_result.__dict__)
                sync_payload: Dict[str, object] = {}

                if not args.skip_sync:
                    if not server_url:
                        raise ValueError(
                            f"Missing production publish server URL. Set DGTEAM_PROD_PUBLISH_SERVER_URL in {paths.sync_env_path}."
                        )
                    if not token:
                        raise ValueError(
                            f"Missing production publish token. Set DGTEAM_PROD_PUBLISH_TOKEN in {paths.sync_env_path}."
                        )
                    release_dir = Path(str(pipeline_payload.get("release_dir") or "")).expanduser().resolve()
                    release_id = str(pipeline_payload.get("release_id") or "").strip()
                    try:
                        sync_payload = sync_release(
                            server_url=server_url,
                            token=token,
                            release_id=release_id,
                            release_dir=release_dir,
                            project_root=PROJECT_ROOT,
                            activate=activate,
                        )
                    except Exception:
                        pending_payload = {
                            "release_id": release_id,
                            "release_dir": str(release_dir),
                            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "activate": activate,
                        }
                        write_json(paths.pending_sync_path, pending_payload)
                        raise

                result_payload = {
                    "ok": True,
                    "ran_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "log_path": str(log_path),
                    "run_outdir": str(run_outdir),
                    "db_path": str(paths.db_path),
                    "automation_home": str(paths.automation_home),
                    "migration_note": migration_note,
                    "retried_pending_sync": retried_sync,
                    "pipeline": pipeline_payload,
                    "sync": sync_payload,
                }
                write_json(paths.state_dir / f"run_{timestamp}.json", result_payload)
                write_json(paths.state_dir / "last_run.json", result_payload)
                if paths.current_run_path.exists():
                    paths.current_run_path.unlink()
                for stale in [paths.state_dir / "last_error.txt", paths.state_dir / "last_failure.json"]:
                    if stale.exists():
                        stale.unlink()
                print(json.dumps(result_payload, ensure_ascii=False, indent=2))
    except Exception as exc:
        failure_payload = {
            "ok": False,
            "failed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "error": f"{type(exc).__name__}: {exc}",
            "log_path": str(log_path),
            "automation_home": str(paths.automation_home),
        }
        if paths.current_run_path.exists():
            paths.current_run_path.unlink()
        write_json(paths.state_dir / "last_failure.json", failure_payload)
        (paths.state_dir / "last_error.txt").write_text(failure_payload["error"], encoding="utf-8")
        raise
    finally:
        release_lock(paths.lock_path)


if __name__ == "__main__":
    main()
