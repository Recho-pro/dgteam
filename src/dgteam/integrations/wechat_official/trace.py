from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_-]+")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class WechatOfficialTraceLogger:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.events_dir = self.root / "events"
        self.conversations_dir = self.root / "conversations"
        self.events_dir.mkdir(parents=True, exist_ok=True)
        self.conversations_dir.mkdir(parents=True, exist_ok=True)

    def _events_path(self) -> Path:
        return self.events_dir / f"{datetime.now().strftime('%Y-%m-%d')}.jsonl"

    def _conversation_path(self, open_id: str) -> Path:
        safe_name = SAFE_NAME_RE.sub("_", str(open_id or "").strip()) or "anonymous"
        return self.conversations_dir / f"{safe_name}.jsonl"

    def log_event(
        self,
        *,
        category: str,
        open_id: str = "",
        msg_type: str = "",
        event: str = "",
        content: str = "",
        payload: dict[str, Any] | None = None,
    ) -> None:
        record = {
            "time": _now(),
            "category": str(category or "").strip(),
            "open_id": str(open_id or "").strip(),
            "msg_type": str(msg_type or "").strip(),
            "event": str(event or "").strip(),
            "content": str(content or "").strip(),
            "payload": dict(payload or {}),
        }
        self._append_jsonl(self._events_path(), record)
        if record["open_id"]:
            self._append_jsonl(self._conversation_path(record["open_id"]), record)

    def log_inbound(self, *, open_id: str, msg_type: str, event: str = "", content: str = "", payload: dict[str, Any] | None = None) -> None:
        self.log_event(
            category="inbound",
            open_id=open_id,
            msg_type=msg_type,
            event=event,
            content=content,
            payload=payload,
        )

    def log_reply(
        self,
        *,
        open_id: str,
        channel: str,
        reply_text: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.log_event(
            category=f"reply:{channel}",
            open_id=open_id,
            msg_type="text",
            content=reply_text,
            payload=payload,
        )

    def log_error(
        self,
        *,
        open_id: str = "",
        stage: str,
        error: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.log_event(
            category=f"error:{stage}",
            open_id=open_id,
            msg_type="error",
            content=error,
            payload=payload,
        )

    def status_payload(self) -> dict[str, Any]:
        event_files = list(self.events_dir.glob("*.jsonl"))
        conversation_files = list(self.conversations_dir.glob("*.jsonl"))
        return {
            "root": str(self.root),
            "event_files": len(event_files),
            "conversation_files": len(conversation_files),
            "latest_event_file": str(max(event_files, key=lambda item: item.name)) if event_files else "",
        }

    @staticmethod
    def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")

