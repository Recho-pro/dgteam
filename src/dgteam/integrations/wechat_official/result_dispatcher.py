from __future__ import annotations

from dgteam.integrations.wechat_official.client import WechatOfficialClient
from dgteam.integrations.wechat_official.formatter import format_image_unsupported
from dgteam.integrations.wechat_official.models import (
    WechatOfficialRecognitionResult,
    WechatOfficialRecognitionTask,
)
from dgteam.integrations.wechat_official.conversation_state import apply_dispatch_resolution
from dgteam.integrations.wechat_official.response_layer import WechatOfficialMarketResponseLayer
from dgteam.integrations.wechat_official.session_store import WechatOfficialSessionStore
from dgteam.integrations.wechat_official.trace import WechatOfficialTraceLogger


class WechatOfficialImageResultDispatcher:
    def __init__(
        self,
        *,
        client: WechatOfficialClient,
        response_layer: WechatOfficialMarketResponseLayer,
        session_store: WechatOfficialSessionStore | None = None,
        trace_logger: WechatOfficialTraceLogger | None = None,
    ):
        self.client = client
        self.response_layer = response_layer
        self.session_store = session_store
        self.trace = trace_logger

    def handle(self, task: WechatOfficialRecognitionTask, result: WechatOfficialRecognitionResult) -> str:
        recognized_summary = str(result.summary or "").strip()
        reply_text = ""
        resolution = None
        preferred_brand = str(result.raw_payload.get("brand") or "").strip()
        preferred_family = str(result.raw_payload.get("family") or "").strip()

        if str(result.status or "").lower() not in {"success", "partial"}:
            reply_text = format_image_unsupported(
                reason=str(result.raw_payload.get("human_reason") or recognized_summary).strip(),
                page_type=str(result.raw_payload.get("page_type") or "").strip(),
            )
        else:
            candidate_queries = [str(item or "").strip() for item in list(result.candidates or []) if str(item or "").strip()]
            if str(result.recognized_query or "").strip():
                candidate_queries.insert(0, str(result.recognized_query or "").strip())
            resolution = self._resolve_candidates(
                recognized_summary=recognized_summary,
                candidate_queries=candidate_queries,
                preferred_brand=preferred_brand,
                preferred_family=preferred_family,
            )
            reply_text = resolution.reply_text

        self.client.send_custom_text(open_id=task.open_id, content=reply_text)
        if self.trace is not None:
            self.trace.log_reply(
                open_id=task.open_id,
                channel="custom",
                reply_text=reply_text,
                payload={
                    "task_id": task.task_id,
                    "recognized_query": str(result.recognized_query or "").strip(),
                    "confidence": str(result.confidence or "").strip(),
                    "status": str(result.status or "").strip(),
                },
            )
        if self.session_store is not None:
            session = self.session_store.load(task.open_id)
            apply_dispatch_resolution(session, resolution=resolution)
            self.session_store.save(session)
        return reply_text

    def _resolve_candidates(
        self,
        *,
        recognized_summary: str,
        candidate_queries: list[str],
        preferred_brand: str,
        preferred_family: str,
    ):
        if hasattr(self.response_layer, "resolve_image_candidates"):
            return self.response_layer.resolve_image_candidates(
                recognized_summary=recognized_summary,
                candidate_queries=candidate_queries,
                preferred_brand=preferred_brand,
                preferred_family=preferred_family,
            )

        # Backward compatibility for lightweight test doubles or older adapters.
        from dgteam.integrations.wechat_official.models import WechatOfficialImageCandidateResolution
        from dgteam.integrations.wechat_official.formatter import (
            format_image_candidates,
            format_image_market_snapshot,
            format_image_no_result,
        )

        ordered_queries: list[str] = []
        seen: set[str] = set()
        for item in candidate_queries:
            clean = str(item or "").strip()
            if not clean:
                continue
            key = clean.casefold()
            if key in seen:
                continue
            seen.add(key)
            ordered_queries.append(clean)

        if not ordered_queries:
            return WechatOfficialImageCandidateResolution(
                kind="no_result",
                reply_text=format_image_no_result(recognized_summary=recognized_summary),
            )

        ambiguous_bucket: list[str] = []
        first_ambiguous_results: list[dict[str, object]] = []
        for query in ordered_queries[:4]:
            plan = self.response_layer.resolve_query(
                query,
                limit=3,
                preferred_brand=preferred_brand,
                preferred_family=preferred_family,
            )
            if plan.kind == "snapshot":
                resolved_title = str(
                    plan.snapshot.get("header", {}).get("title")
                    or plan.candidate.get("label")
                    or plan.candidate.get("family_title")
                    or plan.candidate.get("model_title")
                    or ""
                ).strip()
                return WechatOfficialImageCandidateResolution(
                    kind="snapshot",
                    reply_text=format_image_market_snapshot(
                        recognized_summary=recognized_summary,
                        market_reply=plan.reply_text,
                    ),
                    resolved_query=query,
                    resolved_title=resolved_title,
                    matched_query=query,
                    resolved_candidate=dict(plan.candidate or {}),
                )
            if plan.kind == "ambiguous":
                if not first_ambiguous_results:
                    first_ambiguous_results = [dict(item or {}) for item in list(plan.results or [])[:3]]
                ambiguous_bucket.extend(
                    str(item.get("label") or item.get("family_title") or item.get("model_title") or "").strip()
                    for item in list(plan.results or [])
                )

        if ambiguous_bucket:
            labels: list[str] = []
            seen_labels: set[str] = set()
            for item in ambiguous_bucket:
                clean = str(item or "").strip()
                if not clean:
                    continue
                key = clean.casefold()
                if key in seen_labels:
                    continue
                seen_labels.add(key)
                labels.append(clean)
            return WechatOfficialImageCandidateResolution(
                kind="ambiguous",
                reply_text=format_image_candidates(
                    recognized_summary=recognized_summary,
                    candidates=labels[:3],
                ),
                pending_candidates=first_ambiguous_results,
                resolved_query=ordered_queries[0],
            )

        return WechatOfficialImageCandidateResolution(
            kind="no_result",
            reply_text=format_image_no_result(recognized_summary=recognized_summary),
        )
