from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from dgteam.core.config import load_settings
from dgteam.core.textio import read_json_utf8, write_json_utf8


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Archive DGTEAM WeChat recognition failed tasks into an auditable archive directory."
    )
    parser.add_argument("--project-root", default=str(PROJECT_ROOT))
    parser.add_argument(
        "--task-id",
        action="append",
        default=[],
        help="Only archive the specified task id. Repeat the flag to archive multiple tasks.",
    )
    parser.add_argument(
        "--archive-name",
        default="",
        help="Optional archive directory name. Defaults to failed_archive_<timestamp>.",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def _task_ids_filter(task_ids: list[str]) -> set[str]:
    return {str(item or "").strip() for item in task_ids if str(item or "").strip()}


def _failed_paths(failed_dir: Path, *, selected_task_ids: set[str]) -> list[Path]:
    paths = sorted(failed_dir.glob("*.json"), key=lambda item: item.name)
    if not selected_task_ids:
        return paths
    return [path for path in paths if path.stem in selected_task_ids]


def _normalize_archive_name(value: str) -> str:
    cleaned = str(value or "").strip().replace("\\", "_").replace("/", "_")
    return cleaned


def _archive_payload(
    path: Path,
    *,
    recognition_root: Path,
    archive_dir: Path,
    dry_run: bool,
) -> dict[str, Any]:
    payload = read_json_utf8(path)
    task = dict(payload.get("task") or {}) if isinstance(payload, dict) else {}
    metadata = dict(task.get("metadata") or {}) if isinstance(task, dict) else {}
    task_id = str(task.get("task_id") or path.stem).strip()
    archive_failed_dir = archive_dir / "failed"
    archive_inbox_dir = archive_dir / "inbox"
    archive_downloads_dir = archive_dir / "downloads"
    failed_target = archive_failed_dir / path.name
    inbox_source = recognition_root / "inbox" / path.name
    inbox_target = archive_inbox_dir / path.name
    raw_downloaded_path = str(metadata.get("downloaded_image_path") or "").strip()
    downloaded_image_path: Path | None = None
    download_target: Path | None = None
    if raw_downloaded_path:
        downloaded_image_path = Path(raw_downloaded_path).expanduser()
        if not downloaded_image_path.is_absolute():
            downloaded_image_path = (recognition_root / downloaded_image_path).resolve()
        if downloaded_image_path.name:
            download_target = archive_downloads_dir / downloaded_image_path.name

    archived = {
        "task_id": task_id,
        "failed_source": str(path),
        "failed_target": str(failed_target),
        "error": str(payload.get("error") or "").strip() if isinstance(payload, dict) else "",
        "query_hint": str(task.get("query_hint") or "").strip(),
        "status": str(task.get("status") or "").strip(),
        "inbox_source": str(inbox_source) if inbox_source.exists() else "",
        "inbox_target": str(inbox_target) if inbox_source.exists() else "",
        "download_source": str(downloaded_image_path)
        if downloaded_image_path is not None and downloaded_image_path.exists() and recognition_root in downloaded_image_path.parents
        else "",
        "download_target": str(download_target) if download_target is not None else "",
    }

    if dry_run:
        return archived

    archive_failed_dir.mkdir(parents=True, exist_ok=True)
    path.replace(failed_target)

    if inbox_source.exists():
        archive_inbox_dir.mkdir(parents=True, exist_ok=True)
        inbox_source.replace(inbox_target)

    if (
        download_target is not None
        and downloaded_image_path is not None
        and downloaded_image_path.exists()
        and recognition_root in downloaded_image_path.parents
    ):
        archive_downloads_dir.mkdir(parents=True, exist_ok=True)
        downloaded_image_path.replace(download_target)

    return archived


def main() -> int:
    args = parse_args()
    settings = load_settings(project_root=Path(args.project_root).expanduser().resolve())
    recognition_root = settings.wechat_official.state_dir / "recognition"
    failed_dir = recognition_root / "failed"
    selected_task_ids = _task_ids_filter(list(args.task_id or []))
    archive_name = _normalize_archive_name(args.archive_name)
    if not archive_name:
        archive_name = f"failed_archive_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    archive_dir = recognition_root / archive_name
    failed_paths = _failed_paths(failed_dir, selected_task_ids=selected_task_ids)

    archived_items = [
        _archive_payload(
            path,
            recognition_root=recognition_root,
            archive_dir=archive_dir,
            dry_run=bool(args.dry_run),
        )
        for path in failed_paths
    ]

    result = {
        "ok": True,
        "mode": "dry_run" if args.dry_run else "apply",
        "project_root": str(settings.project_root),
        "recognition_root": str(recognition_root),
        "archive_dir": str(archive_dir),
        "selected_task_ids": sorted(selected_task_ids),
        "archived_count": len(archived_items),
        "archived_tasks": archived_items,
    }
    if not args.dry_run:
        write_json_utf8(
            archive_dir / "archive_manifest.json",
            {
                "archived_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                **result,
            },
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
