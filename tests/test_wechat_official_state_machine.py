from __future__ import annotations

from dgteam.integrations.wechat_official.conversation_state import (
    apply_ambiguous_plan,
    apply_dispatch_resolution,
    apply_fast_lane_direct_hit,
    apply_fast_lane_terminal,
    apply_image_queued,
    apply_snapshot_plan,
    classify_session,
)
from dgteam.integrations.wechat_official.models import (
    WechatOfficialFastLaneOutcome,
    WechatOfficialImageCandidateResolution,
    WechatOfficialMarketReplyPlan,
    WechatOfficialRecognitionTask,
    WechatOfficialSessionState,
)


def make_session() -> WechatOfficialSessionState:
    return WechatOfficialSessionState(open_id="openid-test", updated_at=0)


def make_task(task_id: str = "task-1") -> WechatOfficialRecognitionTask:
    return WechatOfficialRecognitionTask(
        task_id=task_id,
        open_id="openid-test",
        created_at=1710000000,
        updated_at=1710000000,
        status="queued",
        media_id="media-1",
        pic_url="https://example.com/image.jpg",
        msg_id="msg-1",
    )


def make_snapshot_plan() -> WechatOfficialMarketReplyPlan:
    return WechatOfficialMarketReplyPlan(
        kind="snapshot",
        query="iphone17promax",
        candidate={"family_title": "iPhone 17 Pro Max", "label": "iPhone 17 Pro Max"},
        snapshot={"header": {"title": "iPhone 17 Pro Max"}},
        reply_text="iPhone 17 Pro Max\n当前参考区间：9540-9560",
    )


def test_state_machine_classifies_idle_choice_image_and_refinement_states():
    session = make_session()
    assert classify_session(session).phase == "idle"

    apply_ambiguous_plan(
        session,
        effective_query="苹果17",
        plan=WechatOfficialMarketReplyPlan(
            kind="ambiguous",
            results=[{"label": "iPhone 17 Pro Max"}],
            reply_text="",
        ),
    )
    assert classify_session(session).phase == "awaiting_candidate_selection"

    apply_image_queued(session, task=make_task())
    assert classify_session(session).phase == "awaiting_image_result"

    apply_fast_lane_terminal(session)
    apply_snapshot_plan(session, effective_query="iPhone 17 Pro Max", plan=make_snapshot_plan())
    assert classify_session(session).phase == "ready_for_refinement"


def test_dispatch_resolution_transitions_image_task_to_ambiguous_then_snapshot():
    session = make_session()
    apply_image_queued(session, task=make_task())

    apply_dispatch_resolution(
        session,
        resolution=WechatOfficialImageCandidateResolution(
            kind="ambiguous",
            reply_text="更像下面这几个：",
            pending_candidates=[{"label": "iPhone 17"}, {"label": "iPhone 17 Pro Max"}],
            resolved_query="苹果17",
        ),
    )

    ambiguous_state = classify_session(session)
    assert ambiguous_state.phase == "awaiting_candidate_selection"
    assert session.pending_task_id == ""
    assert session.source_query == "苹果17"
    assert session.last_candidate == {}

    apply_dispatch_resolution(
        session,
        resolution=WechatOfficialImageCandidateResolution(
            kind="snapshot",
            reply_text="iPhone 17 Pro Max\n当前参考区间：9540-9560",
            resolved_query="iPhone 17 Pro Max 256G",
            resolved_title="iPhone 17 Pro Max",
            resolved_candidate={"family_title": "iPhone 17 Pro Max"},
        ),
    )

    snapshot_state = classify_session(session)
    assert snapshot_state.phase == "ready_for_refinement"
    assert session.pending_candidates == []
    assert session.last_query == "iPhone 17 Pro Max 256G"
    assert session.last_result_title == "iPhone 17 Pro Max"


def test_fast_lane_direct_hit_clears_pending_image_and_keeps_snapshot_context():
    session = make_session()
    apply_image_queued(session, task=make_task("task-direct"))

    apply_fast_lane_direct_hit(
        session,
        outcome=WechatOfficialFastLaneOutcome(
            status="direct_hit",
            task=make_task("task-direct"),
            reply_text="iPhone 17 Pro Max\n当前参考区间：9540-9560",
            resolution=WechatOfficialImageCandidateResolution(
                kind="snapshot",
                reply_text="iPhone 17 Pro Max\n当前参考区间：9540-9560",
                resolved_query="iPhone 17 Pro Max 256G",
                resolved_title="iPhone 17 Pro Max",
                resolved_candidate={"family_title": "iPhone 17 Pro Max"},
            ),
        ),
    )

    state = classify_session(session)
    assert state.phase == "ready_for_refinement"
    assert session.pending_task_id == ""
    assert session.pending_image == {}
    assert session.last_candidate["family_title"] == "iPhone 17 Pro Max"
