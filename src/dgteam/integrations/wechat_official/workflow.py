from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Iterable

from dgteam.integrations.wechat_official.formatter import (
    format_contact_message,
    format_greeting_message,
    format_help_message,
    format_image_help_message,
    format_image_query_placeholder,
    format_numeric_selection_expired,
    format_numeric_selection_out_of_range,
    format_pending_image_status,
    format_reset_message,
    format_subscribe_message,
)
from dgteam.integrations.wechat_official.fast_lane import WechatOfficialImageFastLane
from dgteam.integrations.wechat_official.models import WechatOfficialInboundMessage
from dgteam.integrations.wechat_official.recognition_queue import WechatOfficialRecognitionQueue
from dgteam.integrations.wechat_official.response_layer import WechatOfficialMarketResponseLayer
from dgteam.integrations.wechat_official.session_store import WechatOfficialSessionStore
from dgteam.integrations.wechat_official.conversation_state import (
    apply_ambiguous_plan,
    apply_fast_lane_direct_hit,
    apply_fast_lane_terminal,
    apply_image_queued,
    apply_no_result,
    apply_snapshot_plan,
    classify_session,
)
from dgteam.integrations.wechat_official.trace import WechatOfficialTraceLogger
from dgteam.query_api.service import QueryService


LOGGER = logging.getLogger("dgteam.wechat_official.workflow")
SESSION_TTL_SECONDS = 15 * 60
CAPACITY_HINT_RE = re.compile(r"(?i)\b\d+(?:\+\d+)?(?:g|gb|t|tb)\b")
PURE_STORAGE_CAPACITY_VALUES = {64, 128, 256, 512, 1024, 2048}
NUMERIC_CHOICE_RE = re.compile(r"^(?:选|第)?\s*([1-9])\s*(?:个|号)?$")
RESET_KEYWORDS = {"重查", "重新查", "清空", "换一个", "换个", "都不是", "不是这个", "重新开始"}
HELP_KEYWORDS = {"帮助", "help", "怎么查", "怎么用", "使用说明", "说明", "菜单"}
IMAGE_HELP_KEYWORDS = {"怎么发图", "截图怎么发", "图片怎么发", "发图说明", "截图说明"}
CONTACT_KEYWORDS = {"联系", "人工", "负责人", "微信", "找人"}
GREETING_KEYWORDS = {"你好", "您好", "在吗", "hello", "hi", "哈喽"}
STATUS_KEYWORDS = {"进度", "状态", "好了没", "结果呢", "图片进度", "查进度", "还没好", "处理好没"}
REFINEMENT_KEYWORDS = (
    "黑",
    "白",
    "银",
    "金",
    "紫",
    "蓝",
    "绿",
    "橙",
    "红",
    "粉",
    "灰",
    "钛",
    "国行",
    "港版",
    "美版",
    "欧版",
    "日版",
    "韩版",
    "wifi",
    "蜂窝",
    "颜色",
    "版本",
    "原封",
)


class WechatOfficialWorkflow:
    def __init__(
        self,
        *,
        query_service: QueryService,
        state_dir: Path,
        fast_lane: WechatOfficialImageFastLane | None = None,
        trace_logger: WechatOfficialTraceLogger | None = None,
    ):
        self.query = query_service
        self.state_dir = Path(state_dir).resolve()
        self.session_store = WechatOfficialSessionStore(self.state_dir / "sessions")
        self.response_layer = WechatOfficialMarketResponseLayer(query_service=query_service)
        self.recognition_queue = WechatOfficialRecognitionQueue(self.state_dir / "recognition")
        self.fast_lane = fast_lane
        self.trace = trace_logger

    def status_payload(self) -> dict[str, object]:
        session_files = len(list(self.session_store.root.glob("*.json")))
        return {
            "session_dir": str(self.session_store.root),
            "session_count": session_files,
            "recognition_queue": self.recognition_queue.status_payload(),
            "fast_lane_enabled": self.fast_lane is not None,
            "trace": self.trace.status_payload() if self.trace is not None else {},
        }

    def handle_message(self, message: WechatOfficialInboundMessage) -> str:
        if message.msg_type == "text":
            reply = self.handle_text_message(message)
        elif message.msg_type == "image":
            reply = self.handle_image_message(message)
        elif message.msg_type == "event" and message.event == "subscribe":
            reply = format_subscribe_message()
        elif message.msg_type == "event" and message.event == "click":
            reply = self._handle_click_event(message.event_key)
        else:
            reply = ""

        if reply and self.trace is not None:
            self.trace.log_reply(
                open_id=message.from_user,
                channel="passive",
                reply_text=reply,
                payload={"msg_type": message.msg_type, "event": message.event, "event_key": message.event_key},
            )
        return reply

    def handle_text_message(self, message: WechatOfficialInboundMessage) -> str:
        query = str(message.content or "").strip()
        open_id = message.from_user
        session = self.session_store.load(open_id)
        session_state = classify_session(session)
        LOGGER.info("wechat official text query open_id=%s text=%s", open_id, query)

        if not query:
            return format_subscribe_message()

        normalized = self._normalize_surface(query)
        if self._matches_any(normalized, HELP_KEYWORDS):
            return format_help_message()
        if self._matches_any(normalized, IMAGE_HELP_KEYWORDS):
            return format_image_help_message()
        if self._matches_any(normalized, CONTACT_KEYWORDS):
            return format_contact_message()
        if self._matches_any(normalized, RESET_KEYWORDS):
            self.session_store.clear(open_id)
            return format_reset_message()
        if self._matches_any(normalized, GREETING_KEYWORDS):
            return format_greeting_message()
        if self._matches_any(normalized, STATUS_KEYWORDS) and session_state.phase == "awaiting_image_result":
            return self._handle_pending_image_status(session.pending_task_id, has_candidates=bool(session.pending_candidates))

        numeric_choice = self._parse_numeric_choice(query)
        if numeric_choice is not None:
            return self._handle_numeric_choice(open_id=open_id, choice=numeric_choice)

        is_refinement = self._looks_like_refinement(query)
        effective_query = self._build_contextual_query(session, query)
        if is_refinement and session.last_candidate:
            plan = self.response_layer.resolve_refinement_query(
                base_candidate=dict(session.last_candidate or {}),
                refinement_query=query,
            )
            if plan.kind == "snapshot":
                apply_snapshot_plan(
                    session,
                    effective_query=effective_query,
                    plan=plan,
                    fallback_candidate=dict(session.last_candidate or {}),
                )
                self.session_store.save(session)
                return plan.reply_text

        plan = self.response_layer.resolve_query(
            effective_query,
            **self._preferred_search_kwargs(session, is_refinement=is_refinement),
        )

        if plan.kind == "no_result":
            apply_no_result(session, query=query, clear_last_candidate=not is_refinement)
            self.session_store.save(session)
            return plan.reply_text

        if plan.kind == "ambiguous":
            apply_ambiguous_plan(session, effective_query=effective_query, plan=plan)
            self.session_store.save(session)
            return plan.reply_text

        apply_snapshot_plan(session, effective_query=effective_query, plan=plan)
        self.session_store.save(session)
        return plan.reply_text

    def handle_image_message(self, message: WechatOfficialInboundMessage) -> str:
        session = self.session_store.load(message.from_user)
        if self.fast_lane is not None:
            outcome = self.fast_lane.probe(message)
            task = outcome.task
            if outcome.status == "terminal":
                apply_fast_lane_terminal(session)
                self.session_store.save(session)
                LOGGER.info(
                    "wechat official fast lane terminal from=%s task_id=%s timings_ms=%s",
                    message.from_user,
                    task.task_id,
                    dict(outcome.timings_ms or {}),
                )
                return outcome.reply_text
            if outcome.status == "direct_hit":
                apply_fast_lane_direct_hit(session, outcome=outcome)
                self.session_store.save(session)
                LOGGER.info(
                    "wechat official fast lane direct-hit from=%s task_id=%s timings_ms=%s",
                    message.from_user,
                    task.task_id,
                    dict(outcome.timings_ms or {}),
                )
                return outcome.reply_text

            self.recognition_queue.enqueue_task(task)
            reply_text = outcome.reply_text or format_image_query_placeholder(task.task_id)
            LOGGER.info(
                "wechat official fast lane deferred from=%s task_id=%s query_hint=%s timings_ms=%s",
                message.from_user,
                task.task_id,
                task.query_hint,
                dict(outcome.timings_ms or {}),
            )
        else:
            task = self.recognition_queue.enqueue_image_message(message)
            reply_text = format_image_query_placeholder(task.task_id)

        apply_image_queued(session, task=task)
        self.session_store.save(session)
        LOGGER.info(
            "wechat official image queued from=%s media_id=%s task_id=%s has_pic_url=%s",
            message.from_user,
            message.media_id,
            task.task_id,
            bool(message.pic_url),
        )
        return reply_text

    def _handle_click_event(self, event_key: str) -> str:
        normalized = str(event_key or "").strip().upper()
        if normalized == "DG_HELP":
            return format_help_message()
        if normalized == "DG_IMAGE_HELP":
            return format_image_help_message()
        if normalized == "DG_CONTACT":
            return format_contact_message()
        return ""

    def _handle_pending_image_status(self, task_id: str, *, has_candidates: bool) -> str:
        snapshot = self.recognition_queue.get_task_status(task_id)
        if not snapshot.get("ok"):
            return format_pending_image_status(status="queued", task_id=task_id, has_candidates=has_candidates)
        return format_pending_image_status(
            status=str(snapshot.get("status") or ""),
            task_id=task_id,
            has_candidates=has_candidates,
        )

    def _handle_numeric_choice(self, *, open_id: str, choice: int) -> str:
        session = self.session_store.load(open_id)
        if classify_session(session).phase != "awaiting_candidate_selection" or not session.updated_at:
            return format_numeric_selection_expired()
        if int(time.time()) - int(session.updated_at) > SESSION_TTL_SECONDS:
            session.pending_candidates = []
            session.source_query = ""
            self.session_store.save(session)
            return format_numeric_selection_expired()
        if choice < 1 or choice > len(session.pending_candidates):
            return format_numeric_selection_out_of_range()

        candidate = dict(session.pending_candidates[choice - 1] or {})
        plan = self.response_layer.resolve_candidate(candidate)
        if plan.kind != "snapshot":
            return format_numeric_selection_out_of_range()
        apply_snapshot_plan(
            session,
            effective_query=str(
                candidate.get("label") or candidate.get("family_title") or candidate.get("model_title") or ""
            ),
            plan=plan,
            fallback_candidate=candidate,
        )
        self.session_store.save(session)
        return plan.reply_text

    @staticmethod
    def _parse_numeric_choice(text: str) -> int | None:
        cleaned = str(text or "").strip()
        if cleaned.isdigit():
            value = int(cleaned)
            if 1 <= value <= 9:
                return value
        match = NUMERIC_CHOICE_RE.fullmatch(cleaned)
        if match:
            return int(match.group(1))
        return None

    @classmethod
    def _build_contextual_query(cls, session, query: str) -> str:
        clean_query = str(query or "").strip()
        if not clean_query:
            return ""
        if not cls._looks_like_refinement(clean_query):
            return clean_query
        base = str(session.source_query or session.last_result_title or session.last_query or "").strip()
        if not base:
            return clean_query
        return f"{base} {clean_query}".strip()

    @staticmethod
    def _plan_result_title(plan) -> str:
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

    @staticmethod
    def _preferred_search_kwargs(session, *, is_refinement: bool) -> dict[str, str]:
        if not is_refinement:
            return {}
        candidate = dict(session.last_candidate or {})
        preferred_brand = str(candidate.get("brand_title") or "").strip()
        preferred_family = str(
            candidate.get("family_title")
            or candidate.get("model_title")
            or session.last_result_title
            or ""
        ).strip()
        kwargs: dict[str, str] = {}
        if preferred_brand:
            kwargs["preferred_brand"] = preferred_brand
        if preferred_family:
            kwargs["preferred_family"] = preferred_family
        return kwargs

    @classmethod
    def _looks_like_refinement(cls, query: str) -> bool:
        clean = str(query or "").strip()
        if not clean:
            return False
        if clean.isdigit():
            return int(clean) in PURE_STORAGE_CAPACITY_VALUES
        lower = clean.casefold()
        compact = lower.replace(" ", "")
        if CAPACITY_HINT_RE.search(lower):
            return True
        if any(keyword.casefold() in compact for keyword in REFINEMENT_KEYWORDS):
            return True
        return len(compact) <= 8 and not any(ch.isdigit() for ch in compact) and len(compact) <= 4

    @staticmethod
    def _normalize_surface(text: str) -> str:
        return "".join(str(text or "").strip().lower().split())

    @classmethod
    def _matches_any(cls, normalized: str, keywords: Iterable[str]) -> bool:
        return any(cls._normalize_surface(keyword) in normalized for keyword in keywords if keyword)
