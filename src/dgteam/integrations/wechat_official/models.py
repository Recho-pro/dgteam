from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


WechatOfficialReplyKind = Literal["empty", "no_result", "ambiguous", "snapshot"]
WechatOfficialImageResolutionKind = Literal["snapshot", "ambiguous", "no_result"]
WechatOfficialFastLaneStatus = Literal["direct_hit", "deferred", "terminal"]


@dataclass(slots=True)
class WechatOfficialInboundMessage:
    msg_type: str
    event: str
    event_key: str
    from_user: str
    to_user: str
    content: str
    media_id: str
    pic_url: str
    msg_id: str
    raw_payload: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "WechatOfficialInboundMessage":
        return cls(
            msg_type=str(payload.get("MsgType") or "").strip().lower(),
            event=str(payload.get("Event") or "").strip().lower(),
            event_key=str(payload.get("EventKey") or "").strip().upper(),
            from_user=str(payload.get("FromUserName") or "").strip(),
            to_user=str(payload.get("ToUserName") or "").strip(),
            content=str(payload.get("Content") or "").strip(),
            media_id=str(payload.get("MediaId") or "").strip(),
            pic_url=str(payload.get("PicUrl") or "").strip(),
            msg_id=str(payload.get("MsgId") or "").strip(),
            raw_payload=dict(payload or {}),
        )


@dataclass(slots=True)
class WechatOfficialSessionState:
    open_id: str
    updated_at: int
    source_query: str = ""
    last_query: str = ""
    last_result_title: str = ""
    last_candidate: dict[str, Any] = field(default_factory=dict)
    pending_candidates: list[dict[str, Any]] = field(default_factory=list)
    pending_image: dict[str, Any] = field(default_factory=dict)
    pending_task_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "open_id": self.open_id,
            "updated_at": int(self.updated_at or 0),
            "source_query": str(self.source_query or ""),
            "last_query": str(self.last_query or ""),
            "last_result_title": str(self.last_result_title or ""),
            "last_candidate": dict(self.last_candidate or {}),
            "pending_candidates": list(self.pending_candidates or []),
            "pending_image": dict(self.pending_image or {}),
            "pending_task_id": str(self.pending_task_id or ""),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "WechatOfficialSessionState":
        return cls(
            open_id=str(payload.get("open_id") or "").strip(),
            updated_at=int(payload.get("updated_at") or 0),
            source_query=str(payload.get("source_query") or "").strip(),
            last_query=str(payload.get("last_query") or "").strip(),
            last_result_title=str(payload.get("last_result_title") or "").strip(),
            last_candidate=dict(payload.get("last_candidate") or {}),
            pending_candidates=list(payload.get("pending_candidates") or []),
            pending_image=dict(payload.get("pending_image") or {}),
            pending_task_id=str(payload.get("pending_task_id") or "").strip(),
        )


@dataclass(slots=True)
class WechatOfficialRecognitionTask:
    task_id: str
    open_id: str
    created_at: int
    updated_at: int
    status: str
    media_id: str = ""
    pic_url: str = ""
    msg_id: str = ""
    source_type: str = "wechat_official_image"
    query_hint: str = ""
    attempts: int = 0
    last_error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": str(self.task_id or ""),
            "open_id": str(self.open_id or ""),
            "created_at": int(self.created_at or 0),
            "updated_at": int(self.updated_at or 0),
            "status": str(self.status or ""),
            "media_id": str(self.media_id or ""),
            "pic_url": str(self.pic_url or ""),
            "msg_id": str(self.msg_id or ""),
            "source_type": str(self.source_type or ""),
            "query_hint": str(self.query_hint or ""),
            "attempts": int(self.attempts or 0),
            "last_error": str(self.last_error or ""),
            "metadata": dict(self.metadata or {}),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "WechatOfficialRecognitionTask":
        return cls(
            task_id=str(payload.get("task_id") or "").strip(),
            open_id=str(payload.get("open_id") or "").strip(),
            created_at=int(payload.get("created_at") or 0),
            updated_at=int(payload.get("updated_at") or 0),
            status=str(payload.get("status") or "").strip(),
            media_id=str(payload.get("media_id") or "").strip(),
            pic_url=str(payload.get("pic_url") or "").strip(),
            msg_id=str(payload.get("msg_id") or "").strip(),
            source_type=str(payload.get("source_type") or "wechat_official_image").strip(),
            query_hint=str(payload.get("query_hint") or "").strip(),
            attempts=int(payload.get("attempts") or 0),
            last_error=str(payload.get("last_error") or "").strip(),
            metadata=dict(payload.get("metadata") or {}),
        )


@dataclass(slots=True)
class WechatOfficialRecognitionResult:
    task_id: str
    status: str
    recognized_query: str = ""
    confidence: str = "unknown"
    model: str = ""
    candidates: list[str] = field(default_factory=list)
    summary: str = ""
    raw_payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": str(self.task_id or ""),
            "status": str(self.status or ""),
            "recognized_query": str(self.recognized_query or ""),
            "confidence": str(self.confidence or "unknown"),
            "model": str(self.model or ""),
            "candidates": [str(item or "").strip() for item in list(self.candidates or []) if str(item or "").strip()],
            "summary": str(self.summary or ""),
            "raw_payload": dict(self.raw_payload or {}),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "WechatOfficialRecognitionResult":
        return cls(
            task_id=str(payload.get("task_id") or "").strip(),
            status=str(payload.get("status") or "").strip(),
            recognized_query=str(payload.get("recognized_query") or "").strip(),
            confidence=str(payload.get("confidence") or "unknown").strip(),
            model=str(payload.get("model") or "").strip(),
            candidates=[str(item or "").strip() for item in list(payload.get("candidates") or []) if str(item or "").strip()],
            summary=str(payload.get("summary") or "").strip(),
            raw_payload=dict(payload.get("raw_payload") or {}),
        )


@dataclass(slots=True)
class WechatOfficialMarketReplyPlan:
    kind: WechatOfficialReplyKind
    query: str = ""
    reply_text: str = ""
    results: list[dict[str, Any]] = field(default_factory=list)
    candidate: dict[str, Any] = field(default_factory=dict)
    snapshot: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class WechatOfficialImageCandidateResolution:
    kind: WechatOfficialImageResolutionKind
    reply_text: str = ""
    pending_candidates: list[dict[str, Any]] = field(default_factory=list)
    resolved_query: str = ""
    resolved_title: str = ""
    matched_query: str = ""
    resolved_candidate: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class WechatOfficialFastLaneOutcome:
    status: WechatOfficialFastLaneStatus
    task: WechatOfficialRecognitionTask
    reply_text: str
    recognition_result: WechatOfficialRecognitionResult | None = None
    resolution: WechatOfficialImageCandidateResolution | None = None
    timings_ms: dict[str, float] = field(default_factory=dict)
