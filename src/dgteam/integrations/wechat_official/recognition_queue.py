from __future__ import annotations

import mimetypes
import time
import uuid
from pathlib import Path
from typing import Any

from dgteam.core.textio import read_json_utf8, write_json_utf8
from dgteam.integrations.wechat_official.models import (
    WechatOfficialInboundMessage,
    WechatOfficialRecognitionResult,
    WechatOfficialRecognitionTask,
)


class WechatOfficialRecognitionQueue:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.inbox_dir = self.root / "inbox"
        self.queued_dir = self.root / "queued"
        self.processing_dir = self.root / "processing"
        self.completed_dir = self.root / "completed"
        self.failed_dir = self.root / "failed"
        self.downloads_dir = self.root / "downloads"
        for directory in (
            self.inbox_dir,
            self.queued_dir,
            self.processing_dir,
            self.completed_dir,
            self.failed_dir,
            self.downloads_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def build_image_task(
        self,
        message: WechatOfficialInboundMessage,
        *,
        query_hint: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> WechatOfficialRecognitionTask:
        now = int(time.time())
        task_metadata = {
            "from_user": message.from_user,
            "to_user": message.to_user,
            "raw_payload": dict(message.raw_payload or {}),
        }
        if metadata:
            task_metadata.update(dict(metadata or {}))
        return WechatOfficialRecognitionTask(
            task_id=f"wximg_{now}_{uuid.uuid4().hex[:10]}",
            open_id=message.from_user,
            created_at=now,
            updated_at=now,
            status="queued",
            media_id=message.media_id,
            pic_url=message.pic_url,
            msg_id=message.msg_id,
            query_hint=str(query_hint or "").strip(),
            metadata=task_metadata,
        )

    def enqueue_task(self, task: WechatOfficialRecognitionTask) -> WechatOfficialRecognitionTask:
        payload = task.to_dict()
        write_json_utf8(self.inbox_dir / f"{task.task_id}.json", payload)
        write_json_utf8(self.queued_dir / f"{task.task_id}.json", payload)
        return task

    def enqueue_image_message(self, message: WechatOfficialInboundMessage) -> WechatOfficialRecognitionTask:
        task = self.build_image_task(message)
        return self.enqueue_task(task)

    def stage_downloaded_image(
        self,
        *,
        task: WechatOfficialRecognitionTask,
        image_name: str,
        image_bytes: bytes,
        content_type: str = "",
    ) -> Path:
        suffix = Path(str(image_name or "").strip()).suffix
        if not suffix:
            mime = str(content_type or "").split(";", 1)[0].strip()
            suffix = mimetypes.guess_extension(mime) or ".bin"
        target = self.downloads_dir / f"{task.task_id}{suffix}"
        target.write_bytes(image_bytes)
        return target

    def claim_next(self) -> WechatOfficialRecognitionTask | None:
        candidates = sorted(self.queued_dir.glob("*.json"), key=lambda item: item.stat().st_mtime)
        if not candidates:
            return None
        source = candidates[0]
        payload = read_json_utf8(source)
        task = WechatOfficialRecognitionTask.from_dict(dict(payload or {}))
        task.status = "processing"
        task.updated_at = int(time.time())
        task.attempts = int(task.attempts or 0) + 1
        source.replace(self.processing_dir / source.name)
        write_json_utf8(self.processing_dir / source.name, task.to_dict())
        return task

    def complete(self, task: WechatOfficialRecognitionTask, result: WechatOfficialRecognitionResult) -> None:
        task.status = "completed"
        task.updated_at = int(time.time())
        processing_path = self.processing_dir / f"{task.task_id}.json"
        payload = {
            "task": task.to_dict(),
            "result": result.to_dict(),
        }
        if processing_path.exists():
            processing_path.unlink()
        write_json_utf8(self.completed_dir / f"{task.task_id}.json", payload)

    def fail(self, task: WechatOfficialRecognitionTask, *, error: str) -> None:
        task.status = "failed"
        task.last_error = str(error or "").strip()
        task.updated_at = int(time.time())
        processing_path = self.processing_dir / f"{task.task_id}.json"
        if processing_path.exists():
            processing_path.unlink()
        payload = {
            "task": task.to_dict(),
            "error": task.last_error,
        }
        write_json_utf8(self.failed_dir / f"{task.task_id}.json", payload)

    def status_payload(self) -> dict[str, Any]:
        return {
            "queued": len(list(self.queued_dir.glob("*.json"))),
            "processing": len(list(self.processing_dir.glob("*.json"))),
            "completed": len(list(self.completed_dir.glob("*.json"))),
            "failed": len(list(self.failed_dir.glob("*.json"))),
        }

    def get_task_status(self, task_id: str) -> dict[str, Any]:
        clean_task_id = str(task_id or "").strip()
        if not clean_task_id:
            return {"ok": False, "status": "", "task": {}, "result": {}, "error": ""}

        locations = (
            ("queued", self.queued_dir / f"{clean_task_id}.json"),
            ("processing", self.processing_dir / f"{clean_task_id}.json"),
            ("completed", self.completed_dir / f"{clean_task_id}.json"),
            ("failed", self.failed_dir / f"{clean_task_id}.json"),
        )
        for status, path in locations:
            if not path.exists():
                continue
            payload = read_json_utf8(path)
            if status in {"completed", "failed"}:
                data = dict(payload or {}) if isinstance(payload, dict) else {}
                return {
                    "ok": True,
                    "status": status,
                    "task": dict(data.get("task") or {}),
                    "result": dict(data.get("result") or {}),
                    "error": str(data.get("error") or "").strip(),
                    "path": str(path),
                }
            task = dict(payload or {}) if isinstance(payload, dict) else {}
            return {
                "ok": True,
                "status": status,
                "task": task,
                "result": {},
                "error": "",
                "path": str(path),
            }
        return {"ok": False, "status": "", "task": {}, "result": {}, "error": ""}
