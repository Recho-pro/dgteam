from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from dgteam.integrations.wechat_clawbot.models import ClawbotEvent
from dgteam.core.textio import read_json_utf8, write_json_utf8, write_text_utf8


class ClawbotEventStore:
    def __init__(self, inbox_dir: Path, archive_dir: Path):
        self.inbox_dir = Path(inbox_dir)
        self.archive_dir = Path(archive_dir)
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)

    def save_inbox(self, event: ClawbotEvent) -> Path:
        target = self.inbox_dir / f"{event.created_at.replace(':', '-').replace(' ', '_')}_{event.event_id or 'event'}.json"
        write_json_utf8(target, event.to_dict())
        return target

    def save_callback(self, *, event_name: str, payload: dict[str, Any], raw_xml: str = "") -> Path:
        event_id = hashlib.sha1(f"{event_name}:{raw_xml or payload}".encode("utf-8")).hexdigest()[:16]
        created_at = str(payload.get("CreateTime") or payload.get("created_at") or "callback")
        target = self.archive_dir / f"{created_at}_{event_name}_{event_id}.json"
        write_json_utf8(
            target,
            {
                "event_name": event_name,
                "payload": payload,
                "raw_xml": raw_xml,
            },
        )
        return target


class ClawbotStateStore:
    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.processed_dir = self.state_dir / "processed_messages"
        self.processed_dir.mkdir(parents=True, exist_ok=True)
        self.token_cache_file = self.state_dir / "access_token_cache.json"
        self.last_callback_file = self.state_dir / "last_callback.xml"

    def _processed_path(self, message_id: str) -> Path:
        digest = hashlib.sha1(str(message_id or "").encode("utf-8")).hexdigest()
        return self.processed_dir / f"{digest}.json"

    def has_processed_message(self, message_id: str) -> bool:
        if not str(message_id or "").strip():
            return False
        return self._processed_path(message_id).exists()

    def mark_processed_message(self, message_id: str, payload: dict[str, Any]) -> Path:
        target = self._processed_path(message_id)
        write_json_utf8(target, payload)
        return target

    def load_access_token_cache(self) -> dict[str, Any]:
        if not self.token_cache_file.exists():
            return {}
        try:
            payload = read_json_utf8(self.token_cache_file)
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def save_access_token_cache(self, payload: dict[str, Any]) -> Path:
        return write_json_utf8(self.token_cache_file, payload)

    def save_last_callback_xml(self, xml_text: str) -> Path:
        return write_text_utf8(self.last_callback_file, xml_text)
