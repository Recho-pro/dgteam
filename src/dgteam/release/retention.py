from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List


@dataclass(frozen=True)
class RemovedArtifact:
    path: str
    size_bytes: int
    reason: str


def _path_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return int(path.stat().st_size)
        except OSError:
            return 0
    total = 0
    for item in path.rglob("*"):
        if not item.is_file():
            continue
        try:
            total += int(item.stat().st_size)
        except OSError:
            continue
    return total


def _remove_path(path: Path, *, reason: str, removed: List[RemovedArtifact]) -> None:
    target = Path(path)
    if not target.exists():
        return
    size_bytes = _path_size(target)
    if target.is_dir():
        shutil.rmtree(target, ignore_errors=False)
    else:
        target.unlink()
    removed.append(RemovedArtifact(path=str(target), size_bytes=size_bytes, reason=reason))


def _sorted_children(parent: Path, *, predicate: Callable[[Path], bool]) -> List[Path]:
    if not parent.exists():
        return []
    children = [item for item in parent.iterdir() if predicate(item)]
    return sorted(children, key=lambda item: item.stat().st_mtime, reverse=True)


def _summary_dict(removed: Iterable[RemovedArtifact], *, scope: str) -> dict:
    removed_list = list(removed)
    total_bytes = sum(item.size_bytes for item in removed_list)
    return {
        "scope": scope,
        "removed_count": len(removed_list),
        "reclaimed_bytes": total_bytes,
        "reclaimed_gb": round(total_bytes / 1024 / 1024 / 1024, 3),
        "removed": [
            {
                "path": item.path,
                "size_mb": round(item.size_bytes / 1024 / 1024, 1),
                "reason": item.reason,
            }
            for item in removed_list
        ],
    }


def prune_local_runtime(
    *,
    releases_dir: Path,
    integration_smoke_dir: Path,
    keep_release_dirs: int,
    keep_release_archives: int,
    keep_integration_smoke_runs: int,
) -> dict:
    removed: List[RemovedArtifact] = []
    release_dirs = _sorted_children(releases_dir, predicate=lambda item: item.is_dir())
    for item in release_dirs[max(0, int(keep_release_dirs)) :]:
        _remove_path(item, reason="local_release_dir", removed=removed)

    release_archives = _sorted_children(
        releases_dir,
        predicate=lambda item: item.is_file() and item.suffix.lower() == ".zip",
    )
    for item in release_archives[max(0, int(keep_release_archives)) :]:
        _remove_path(item, reason="local_release_archive", removed=removed)

    smoke_runs = _sorted_children(integration_smoke_dir, predicate=lambda item: item.is_dir())
    for item in smoke_runs[max(0, int(keep_integration_smoke_runs)) :]:
        _remove_path(item, reason="integration_smoke_run", removed=removed)

    return _summary_dict(removed, scope="local")


def prune_cloud_runtime(
    *,
    history_dir: Path,
    uploads_dir: Path,
    keep_release_dirs: int,
    keep_rollback_dirs: int,
    clear_uploads: bool,
) -> dict:
    removed: List[RemovedArtifact] = []
    release_dirs = _sorted_children(
        history_dir,
        predicate=lambda item: item.is_dir() and not item.name.startswith("rolled_back_"),
    )
    for item in release_dirs[max(0, int(keep_release_dirs)) :]:
        _remove_path(item, reason="cloud_release_dir", removed=removed)

    rollback_dirs = _sorted_children(
        history_dir,
        predicate=lambda item: item.is_dir() and item.name.startswith("rolled_back_"),
    )
    for item in rollback_dirs[max(0, int(keep_rollback_dirs)) :]:
        _remove_path(item, reason="cloud_rollback_dir", removed=removed)

    if clear_uploads and uploads_dir.exists():
        upload_items = _sorted_children(uploads_dir, predicate=lambda item: True)
        for item in upload_items:
            _remove_path(item, reason="cloud_upload_artifact", removed=removed)

    return _summary_dict(removed, scope="cloud")
