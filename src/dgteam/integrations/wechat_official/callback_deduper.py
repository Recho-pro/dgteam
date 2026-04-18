from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from dgteam.core.textio import read_json_utf8
from dgteam.integrations.wechat_official.models import WechatOfficialInboundMessage


class WechatOfficialCallbackDeduper:
    def __init__(self, root: Path, *, ttl_seconds: int = 24 * 60 * 60):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = max(int(ttl_seconds or 0), 60)

    def begin(self, message: WechatOfficialInboundMessage) -> dict[str, Any]:
        self._cleanup_expired()
        key = self._key_for_message(message)
        path = self.root / f"{key}.json"
        now = int(time.time())
        payload = {
            "key": key,
            "status": "processing",
            "created_at": now,
            "updated_at": now,
            "msg_type": str(message.msg_type or "").strip(),
            "msg_id": str(message.msg_id or "").strip(),
            "from_user": str(message.from_user or "").strip(),
            "media_id": str(message.media_id or "").strip(),
        }
        try:
            with path.open("x", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            return {"fresh": True, **payload}
        except FileExistsError:
            existing = self._read_entry(path)
            return {"fresh": False, **existing}

    def complete(
        self,
        message: WechatOfficialInboundMessage,
        *,
        reply_text: str,
        response_xml: str,
    ) -> None:
        key = self._key_for_message(message)
        path = self.root / f"{key}.json"
        current = self._read_entry(path)
        current.update(
            {
                "key": key,
                "status": "completed",
                "updated_at": int(time.time()),
                "reply_text": str(reply_text or ""),
                "response_xml": str(response_xml or "success"),
            }
        )
        path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")

    def abort(self, message: WechatOfficialInboundMessage) -> None:
        path = self.root / f"{self._key_for_message(message)}.json"
        if path.exists():
            path.unlink()

    def _read_entry(self, path: Path) -> dict[str, Any]:
        try:
            payload = read_json_utf8(path)
        except Exception:
            return {}
        return dict(payload or {}) if isinstance(payload, dict) else {}

    def _cleanup_expired(self) -> None:
        now = int(time.time())
        for path in self.root.glob("*.json"):
            try:
                payload = self._read_entry(path)
                updated_at = int(payload.get("updated_at") or payload.get("created_at") or 0)
                if updated_at and now - updated_at <= self.ttl_seconds:
                    continue
                path.unlink(missing_ok=True)
            except Exception:
                continue

    @staticmethod
    def _key_for_message(message: WechatOfficialInboundMessage) -> str:
        raw_payload = dict(message.raw_payload or {})
        key_payload = {
            "msg_type": str(message.msg_type or "").strip(),
            "event": str(message.event or "").strip(),
            "event_key": str(message.event_key or "").strip(),
            "from_user": str(message.from_user or "").strip(),
            "to_user": str(message.to_user or "").strip(),
            "msg_id": str(message.msg_id or "").strip(),
            "media_id": str(message.media_id or "").strip(),
            "pic_url": str(message.pic_url or "").strip(),
            "content": str(message.content or "").strip(),
            "create_time": str(raw_payload.get("CreateTime") or "").strip(),
        }
        digest = hashlib.sha1(
            json.dumps(key_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return digest
