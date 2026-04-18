from __future__ import annotations

import logging
import time

from dgteam.integrations.wechat_official.formatter import (
    format_image_unsupported,
    format_image_query_deferred,
    format_image_query_placeholder,
)
from dgteam.integrations.wechat_official.image_recognizer import WechatOfficialEcommerceImageRecognizer
from dgteam.integrations.wechat_official.models import (
    WechatOfficialFastLaneOutcome,
    WechatOfficialInboundMessage,
)
from dgteam.integrations.wechat_official.recognition_queue import WechatOfficialRecognitionQueue
from dgteam.integrations.wechat_official.response_layer import WechatOfficialMarketResponseLayer
from dgteam.integrations.wechat_official.client import WechatOfficialClient


LOGGER = logging.getLogger("dgteam.wechat_official.fast_lane")
DIRECT_HIT_CONFIDENCE = {"high"}


class WechatOfficialImageFastLane:
    def __init__(
        self,
        *,
        client: WechatOfficialClient,
        queue: WechatOfficialRecognitionQueue,
        recognizer: WechatOfficialEcommerceImageRecognizer,
        response_layer: WechatOfficialMarketResponseLayer,
    ):
        self.client = client
        self.queue = queue
        self.recognizer = recognizer
        self.response_layer = response_layer

    def probe(self, message: WechatOfficialInboundMessage) -> WechatOfficialFastLaneOutcome:
        started_at = time.perf_counter()
        task = self.queue.build_image_task(message)

        download_started_at = time.perf_counter()
        try:
            image_bytes, image_name, content_type = self._download_image(message)
        except Exception as exc:
            LOGGER.warning(
                "wechat official fast lane download failed from=%s media_id=%s error=%s",
                message.from_user,
                message.media_id,
                exc,
            )
            return WechatOfficialFastLaneOutcome(
                status="deferred",
                task=task,
                reply_text=format_image_query_placeholder(task.task_id),
                timings_ms={
                    "download_ms": round((time.perf_counter() - download_started_at) * 1000, 1),
                    "total_ms": round((time.perf_counter() - started_at) * 1000, 1),
                },
            )

        download_ms = round((time.perf_counter() - download_started_at) * 1000, 1)
        staged_path = self.queue.stage_downloaded_image(
            task=task,
            image_name=image_name,
            image_bytes=image_bytes,
            content_type=content_type,
        )
        task.metadata.update(
            {
                "downloaded_image_path": str(staged_path),
                "downloaded_image_name": image_name,
                "downloaded_content_type": content_type,
            }
        )

        recognize_started_at = time.perf_counter()
        try:
            result = self.recognizer.recognize_image_blob(
                task_id=task.task_id,
                image_name=image_name,
                image_bytes=image_bytes,
                content_type=content_type,
            )
        except Exception as exc:
            LOGGER.exception("wechat official fast lane recognize failed task_id=%s", task.task_id)
            result = None
            task.metadata["fast_probe_error"] = str(exc or "").strip()
        recognize_ms = round((time.perf_counter() - recognize_started_at) * 1000, 1)

        resolution = None
        resolve_ms = 0.0
        recognized_summary = ""
        query_hint = ""
        if result is not None:
            recognized_summary = str(result.summary or "").strip()
            query_hint = str(result.recognized_query or "").strip()
            if not query_hint:
                query_hint = next(
                    (str(item or "").strip() for item in list(result.candidates or []) if str(item or "").strip()),
                    "",
                )
            task.query_hint = query_hint
            task.metadata["fast_probe"] = {
                "status": str(result.status or "").strip(),
                "confidence": str(result.confidence or "unknown").strip(),
                "recognized_query": query_hint,
                "summary": recognized_summary,
                "page_type": str(result.raw_payload.get("page_type") or "").strip(),
                "candidates": list(result.candidates or []),
                "timings_ms": dict(result.raw_payload.get("timings_ms") or {}),
            }

            if (
                str(result.status or "").lower() == "unsupported"
                and bool(result.raw_payload.get("terminal_unsupported"))
            ):
                return WechatOfficialFastLaneOutcome(
                    status="terminal",
                    task=task,
                    reply_text=format_image_unsupported(
                        reason=str(result.raw_payload.get("human_reason") or result.summary or "").strip(),
                        page_type=str(result.raw_payload.get("page_type") or "").strip(),
                    ),
                    recognition_result=result,
                    timings_ms={
                        "download_ms": download_ms,
                        "fast_recognize_ms": recognize_ms,
                        "total_ms": round((time.perf_counter() - started_at) * 1000, 1),
                    },
                )

            if (
                str(result.status or "").lower() in {"success", "partial"}
                and str(result.confidence or "").strip().lower() in DIRECT_HIT_CONFIDENCE
            ):
                resolve_started_at = time.perf_counter()
                resolution = self.response_layer.resolve_image_candidates(
                    recognized_summary=recognized_summary,
                    candidate_queries=[query_hint, *list(result.candidates or [])],
                    preferred_brand=str(result.raw_payload.get("brand") or "").strip(),
                    preferred_family=str(result.raw_payload.get("family") or "").strip(),
                )
                resolve_ms = round((time.perf_counter() - resolve_started_at) * 1000, 1)
                if resolution.kind == "snapshot":
                    return WechatOfficialFastLaneOutcome(
                        status="direct_hit",
                        task=task,
                        reply_text=resolution.reply_text,
                        recognition_result=result,
                        resolution=resolution,
                        timings_ms={
                            "download_ms": download_ms,
                            "fast_recognize_ms": recognize_ms,
                            "fast_resolve_ms": resolve_ms,
                            "total_ms": round((time.perf_counter() - started_at) * 1000, 1),
                        },
                    )

        reply_text = format_image_query_deferred(
            recognized_summary=recognized_summary,
            query_hint=query_hint,
            task_id=task.task_id,
            page_type=str(result.raw_payload.get("page_type") or "").strip() if result is not None else "",
        )
        return WechatOfficialFastLaneOutcome(
            status="deferred",
            task=task,
            reply_text=reply_text,
            recognition_result=result,
            resolution=resolution,
            timings_ms={
                "download_ms": download_ms,
                "fast_recognize_ms": recognize_ms,
                "fast_resolve_ms": resolve_ms,
                "total_ms": round((time.perf_counter() - started_at) * 1000, 1),
            },
        )

    def _download_image(self, message: WechatOfficialInboundMessage) -> tuple[bytes, str, str]:
        if message.media_id:
            return self.client.download_media(message.media_id)
        if message.pic_url:
            return self.client.download_image_url(message.pic_url)
        raise RuntimeError("Image message does not include a usable media_id or pic_url.")
