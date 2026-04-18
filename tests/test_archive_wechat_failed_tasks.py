from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run_archive(tmp_path: Path, *extra: str) -> dict[str, object]:
    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "archive_wechat_failed_tasks.py"),
            "--project-root",
            str(tmp_path),
            *extra,
        ],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def _seed_failed_task(tmp_path: Path, *, task_id: str = "task_1") -> Path:
    recognition_root = tmp_path / "runtime" / "local" / "wechat_official" / "state" / "recognition"
    failed_path = recognition_root / "failed" / f"{task_id}.json"
    inbox_path = recognition_root / "inbox" / f"{task_id}.json"
    download_path = recognition_root / "downloads" / f"{task_id}.jpg"
    download_path.parent.mkdir(parents=True, exist_ok=True)
    download_path.write_bytes(b"image")
    inbox_path.parent.mkdir(parents=True, exist_ok=True)
    inbox_path.write_text("{}", encoding="utf-8")
    failed_path.parent.mkdir(parents=True, exist_ok=True)
    failed_path.write_text(
        json.dumps(
            {
                "task": {
                    "task_id": task_id,
                    "status": "failed",
                    "metadata": {
                        "downloaded_image_path": str(download_path),
                    },
                },
                "error": "model timeout",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return failed_path


def test_archive_wechat_failed_tasks_dry_run_keeps_runtime_unchanged(tmp_path: Path) -> None:
    failed_path = _seed_failed_task(tmp_path, task_id="task_dry_run")

    payload = _run_archive(tmp_path, "--dry-run")

    assert payload["mode"] == "dry_run"
    assert payload["archived_count"] == 1
    assert failed_path.exists()


def test_archive_wechat_failed_tasks_moves_failed_payloads_and_artifacts(tmp_path: Path) -> None:
    failed_path = _seed_failed_task(tmp_path, task_id="task_apply")

    payload = _run_archive(tmp_path, "--archive-name", "failed_archive_round30")

    archive_dir = Path(str(payload["archive_dir"]))
    assert payload["mode"] == "apply"
    assert payload["archived_count"] == 1
    assert not failed_path.exists()
    assert (archive_dir / "failed" / "task_apply.json").exists()
    assert (archive_dir / "inbox" / "task_apply.json").exists()
    assert (archive_dir / "downloads" / "task_apply.jpg").exists()
    assert (archive_dir / "archive_manifest.json").exists()
