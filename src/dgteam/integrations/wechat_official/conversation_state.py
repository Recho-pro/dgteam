from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from dgteam.integrations.wechat_official.models import (
    WechatOfficialFastLaneOutcome,
    WechatOfficialImageCandidateResolution,
    WechatOfficialMarketReplyPlan,
    WechatOfficialRecognitionTask,
    WechatOfficialSessionState,
)


WechatOfficialConversationPhase = Literal[
    "idle",
    "awaiting_candidate_selection",
    "awaiting_image_result",
    "ready_for_refinement",
]


@dataclass(frozen=True, slots=True)
class WechatOfficialConversationStateSnapshot:
    phase: WechatOfficialConversationPhase
    pending_task_id: str
    pending_candidate_count: int
    has_last_candidate: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "phase": self.phase,
            "pending_task_id": self.pending_task_id,
            "pending_candidate_count": self.pending_candidate_count,
            "has_last_candidate": self.has_last_candidate,
        }


def classify_session(session: WechatOfficialSessionState) -> WechatOfficialConversationStateSnapshot:
    pending_task_id = str(session.pending_task_id or "").strip()
    pending_candidate_count = len(list(session.pending_candidates or []))
    has_last_candidate = bool(dict(session.last_candidate or {}))
    if pending_task_id:
        phase: WechatOfficialConversationPhase = "awaiting_image_result"
    elif pending_candidate_count:
        phase = "awaiting_candidate_selection"
    elif has_last_candidate:
        phase = "ready_for_refinement"
    else:
        phase = "idle"
    return WechatOfficialConversationStateSnapshot(
        phase=phase,
        pending_task_id=pending_task_id,
        pending_candidate_count=pending_candidate_count,
        has_last_candidate=has_last_candidate,
    )


def apply_no_result(
    session: WechatOfficialSessionState,
    *,
    query: str,
    clear_last_candidate: bool,
) -> WechatOfficialSessionState:
    _clear_pending_candidates(session)
    session.last_query = str(query or "").strip()
    if clear_last_candidate:
        session.last_candidate = {}
    return session


def apply_ambiguous_plan(
    session: WechatOfficialSessionState,
    *,
    effective_query: str,
    plan: WechatOfficialMarketReplyPlan,
) -> WechatOfficialSessionState:
    session.source_query = str(effective_query or "").strip()
    session.last_query = str(effective_query or "").strip()
    session.last_result_title = ""
    session.last_candidate = {}
    session.pending_candidates = [dict(item or {}) for item in list(plan.results or [])[:6]]
    return session


def apply_snapshot_plan(
    session: WechatOfficialSessionState,
    *,
    effective_query: str,
    plan: WechatOfficialMarketReplyPlan,
    fallback_candidate: dict[str, object] | None = None,
) -> WechatOfficialSessionState:
    _clear_pending_candidates(session)
    session.last_query = str(effective_query or "").strip()
    session.last_result_title = _plan_result_title(plan)
    resolved_candidate = dict(plan.candidate or fallback_candidate or session.last_candidate or {})
    session.last_candidate = resolved_candidate
    return session


def apply_image_queued(
    session: WechatOfficialSessionState,
    *,
    task: WechatOfficialRecognitionTask,
) -> WechatOfficialSessionState:
    session.pending_image = {
        "task_id": task.task_id,
        "media_id": task.media_id,
        "pic_url": task.pic_url,
        "received_at": task.created_at,
    }
    session.pending_task_id = task.task_id
    return session


def apply_fast_lane_terminal(session: WechatOfficialSessionState) -> WechatOfficialSessionState:
    _clear_pending_candidates(session)
    _clear_pending_image(session)
    return session


def apply_fast_lane_direct_hit(
    session: WechatOfficialSessionState,
    *,
    outcome: WechatOfficialFastLaneOutcome,
) -> WechatOfficialSessionState:
    apply_fast_lane_terminal(session)
    resolution = outcome.resolution
    if resolution is None:
        return session
    if resolution.resolved_query:
        session.last_query = str(resolution.resolved_query or "").strip()
    if resolution.resolved_title:
        session.last_result_title = str(resolution.resolved_title or "").strip()
    if resolution.resolved_candidate:
        session.last_candidate = dict(resolution.resolved_candidate or {})
    return session


def apply_dispatch_resolution(
    session: WechatOfficialSessionState,
    *,
    resolution: WechatOfficialImageCandidateResolution | None,
) -> WechatOfficialSessionState:
    _clear_pending_image(session)
    if resolution is None:
        session.pending_candidates = []
        return session

    if resolution.kind == "snapshot":
        _clear_pending_candidates(session)
        if resolution.resolved_query:
            session.last_query = str(resolution.resolved_query or "").strip()
        if resolution.resolved_title:
            session.last_result_title = str(resolution.resolved_title or "").strip()
        if resolution.resolved_candidate:
            session.last_candidate = dict(resolution.resolved_candidate or {})
        return session

    if resolution.kind == "ambiguous":
        session.source_query = str(resolution.resolved_query or "").strip()
        session.last_query = str(resolution.resolved_query or "").strip()
        session.last_result_title = ""
        session.last_candidate = {}
        session.pending_candidates = [dict(item or {}) for item in list(resolution.pending_candidates or [])]
        return session

    _clear_pending_candidates(session)
    return session


def _clear_pending_candidates(session: WechatOfficialSessionState) -> None:
    session.pending_candidates = []
    session.source_query = ""


def _clear_pending_image(session: WechatOfficialSessionState) -> None:
    session.pending_image = {}
    session.pending_task_id = ""


def _plan_result_title(plan: WechatOfficialMarketReplyPlan) -> str:
    if plan.kind != "snapshot":
        return ""
    snapshot = dict(plan.snapshot or {})
    header = dict(snapshot.get("header") or {})
    return str(
        header.get("title")
        or plan.candidate.get("label")
        or plan.candidate.get("family_title")
        or plan.candidate.get("model_title")
        or ""
    ).strip()
