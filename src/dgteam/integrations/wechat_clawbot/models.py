from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ClawbotEvent:
    event_id: str
    event_type: str
    source: str
    sender: str
    room: str
    text: str
    created_at: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ClawbotAck:
    ok: bool
    event_id: str
    stored_at: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_event_from_payload(payload: dict[str, Any]) -> ClawbotEvent:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    nested_text = payload.get("text")
    if isinstance(nested_text, dict):
        text_value = str(nested_text.get("content") or nested_text.get("Content") or "")
    else:
        text_value = str(payload.get("text") or payload.get("content") or payload.get("Content") or "")

    event_block = payload.get("event")
    event_type = ""
    if isinstance(event_block, dict):
        event_type = str(event_block.get("event_type") or event_block.get("Event") or "").strip()

    return ClawbotEvent(
        event_id=str(
            payload.get("event_id")
            or payload.get("message_id")
            or payload.get("msgid")
            or payload.get("id")
            or payload.get("MsgId")
            or ""
        ),
        event_type=event_type or str(payload.get("event_type") or payload.get("type") or payload.get("msgtype") or "message"),
        source=str(payload.get("source") or "wecom_customer_service"),
        sender=str(
            payload.get("sender")
            or payload.get("from")
            or payload.get("external_userid")
            or payload.get("FromUserName")
            or ""
        ),
        room=str(payload.get("room") or payload.get("conversation") or payload.get("open_kfid") or payload.get("ToUserName") or ""),
        text=text_value,
        created_at=str(payload.get("created_at") or payload.get("send_time") or payload.get("CreateTime") or now),
        payload=dict(payload),
    )
