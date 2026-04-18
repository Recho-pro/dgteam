from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from dgteam.core.config import WechatOfficialConfig
from dgteam.integrations.wechat_official.callback_deduper import WechatOfficialCallbackDeduper
from dgteam.integrations.wechat_official.client import WechatOfficialClient
from dgteam.integrations.wechat_official.fast_lane import WechatOfficialImageFastLane
from dgteam.integrations.wechat_official.image_recognizer import WechatOfficialEcommerceImageRecognizer
from dgteam.integrations.wechat_official.image_runtime import WechatOfficialImageRuntimeProfile
from dgteam.integrations.wechat_official.ingress import WechatOfficialIngress
from dgteam.integrations.wechat_official.trace import WechatOfficialTraceLogger
from dgteam.integrations.wechat_official.workflow import WechatOfficialWorkflow
from dgteam.query_api.service import QueryService


LOGGER = logging.getLogger("dgteam.wechat_official.service")


class WechatOfficialService:
    def __init__(
        self,
        *,
        config: WechatOfficialConfig,
        db_path: Path | None = None,
        query_service: QueryService | None = None,
    ):
        self.config = config
        self.query = query_service or QueryService(db_path=db_path)
        self.ingress = WechatOfficialIngress(config)
        self.client = WechatOfficialClient(config=self.config)
        self.trace = WechatOfficialTraceLogger(self.config.state_dir / "trace")
        self.callback_deduper = WechatOfficialCallbackDeduper(self.config.state_dir / "callback_dedupe")
        self.workflow = WechatOfficialWorkflow(
            query_service=self.query,
            state_dir=self.config.state_dir,
            trace_logger=self.trace,
        )
        self.fast_lane = self._build_fast_lane()
        self.workflow.fast_lane = self.fast_lane

    def health_payload(self) -> dict[str, object]:
        image_runtime = WechatOfficialImageRuntimeProfile.from_config(self.config)
        return {
            "ok": True,
            "service": "dgteam-wechat-official-bridge",
            "enabled": self.config.enabled,
            "callback_path": self.config.callback_path,
            "has_callback_crypto": self.ingress.crypto is not None,
            "has_app_secret": bool(self.config.app_secret),
            "query_status": self.query.status_payload(),
            "capabilities": {
                "url_verification": True,
                "callback_decrypt": True,
                "passive_text_reply": True,
                "session_context": True,
                "image_intake": True,
                "image_fast_lane": image_runtime.fast_lane_enabled,
                "image_worker": image_runtime.worker_enabled,
                "custom_menu_publish": bool(self.config.app_secret),
            },
            "workflow": self.workflow.status_payload(),
            "trace": self.trace.status_payload(),
            "image_runtime": image_runtime.to_dict(),
            "image_models": {
                "worker_requested": self.config.image_worker_enabled,
                "worker_enabled": image_runtime.worker_enabled,
                "worker_mode": image_runtime.worker_mode,
                "worker_reason": image_runtime.worker_reason,
                "has_api_key": image_runtime.has_api_key,
                "fast_lane_enabled": image_runtime.fast_lane_enabled,
                "fast_lane_reason": image_runtime.fast_lane_reason,
                "fast_model": self.config.image_fast_model,
                "fast_timeout_seconds": self.config.image_fast_timeout_seconds,
                "fast_max_edge_px": self.config.image_fast_max_edge_px,
                "fast_max_bytes": self.config.image_fast_max_bytes,
                "fast_jpeg_quality": self.config.image_fast_jpeg_quality,
                "primary": self.config.image_primary_model,
                "fallback": self.config.image_fallback_model,
                "poll_interval_seconds": self.config.image_poll_interval_seconds,
                "timeout_seconds": self.config.image_timeout_seconds,
                "max_edge_px": self.config.image_max_edge_px,
                "max_bytes": self.config.image_max_bytes,
                "jpeg_quality": self.config.image_jpeg_quality,
            },
        }

    def verify_callback_url(self, *, msg_signature: str, timestamp: str, nonce: str, echostr: str) -> str:
        return self.ingress.verify_callback_url(
            msg_signature=msg_signature,
            timestamp=timestamp,
            nonce=nonce,
            echostr=echostr,
        )

    def handle_callback(self, *, raw_body: str, msg_signature: str, timestamp: str, nonce: str) -> dict[str, Any]:
        decoded = self.ingress.decode_callback(
            raw_body=raw_body,
            msg_signature=msg_signature,
            timestamp=timestamp,
            nonce=nonce,
        )
        self.config.state_dir.mkdir(parents=True, exist_ok=True)
        (self.config.state_dir / "last_callback.xml").write_text(decoded.plain_xml, encoding="utf-8")
        LOGGER.info(
            "wechat official callback decrypted msg_type=%s event=%s from=%s to=%s",
            decoded.message.msg_type,
            decoded.message.event,
            decoded.message.from_user,
            decoded.message.to_user,
        )
        dedupe_entry = self.callback_deduper.begin(decoded.message)
        if not dedupe_entry.get("fresh"):
            status = str(dedupe_entry.get("status") or "").strip().lower()
            LOGGER.info(
                "wechat official callback deduped msg_type=%s from=%s msg_id=%s status=%s",
                decoded.message.msg_type,
                decoded.message.from_user,
                decoded.message.msg_id,
                status or "unknown",
            )
            if status == "completed":
                return {
                    "ok": True,
                    "payload": decoded.payload,
                    "reply_text": str(dedupe_entry.get("reply_text") or ""),
                    "response_xml": str(dedupe_entry.get("response_xml") or "success"),
                    "deduped": True,
                }
            return {
                "ok": True,
                "payload": decoded.payload,
                "reply_text": "",
                "response_xml": "success",
                "deduped": True,
            }
        self.trace.log_inbound(
            open_id=decoded.message.from_user,
            msg_type=decoded.message.msg_type,
            event=decoded.message.event,
            content=decoded.message.content,
            payload={
                "event_key": decoded.message.event_key,
                "msg_id": decoded.message.msg_id,
                "media_id": decoded.message.media_id,
                "pic_url": decoded.message.pic_url,
            },
        )

        try:
            reply_text = self.workflow.handle_message(decoded.message)
            if not reply_text:
                response_xml = "success"
                self.callback_deduper.complete(
                    decoded.message,
                    reply_text="",
                    response_xml=response_xml,
                )
                return {
                    "ok": True,
                    "payload": decoded.payload,
                    "reply_text": "",
                    "response_xml": response_xml,
                }

            response_xml = self.ingress.encode_reply(
                to_user=decoded.message.from_user,
                from_user=decoded.message.to_user,
                reply_text=reply_text,
                timestamp=timestamp,
                nonce=nonce,
            )
            self.callback_deduper.complete(
                decoded.message,
                reply_text=reply_text,
                response_xml=response_xml,
            )
            return {
                "ok": True,
                "payload": decoded.payload,
                "reply_text": reply_text,
                "response_xml": response_xml,
            }
        except Exception:
            self.callback_deduper.abort(decoded.message)
            raise

    def _build_fast_lane(self) -> WechatOfficialImageFastLane | None:
        image_runtime = WechatOfficialImageRuntimeProfile.from_config(self.config)
        if not image_runtime.fast_lane_enabled:
            return None
        recognizer = WechatOfficialEcommerceImageRecognizer(
            client=self.client,
            api_key=self.config.image_api_key,
            primary_model=self.config.image_fast_model,
            fallback_model="",
            cache_dir=self.config.state_dir / "recognition" / "cache",
            cache_namespace="fast_lane",
            recognition_profile="fast",
            timeout_seconds=self.config.image_fast_timeout_seconds,
            max_edge_px=self.config.image_fast_max_edge_px,
            max_bytes=self.config.image_fast_max_bytes,
            jpeg_quality=self.config.image_fast_jpeg_quality,
        )
        return WechatOfficialImageFastLane(
            client=self.client,
            queue=self.workflow.recognition_queue,
            recognizer=recognizer,
            response_layer=self.workflow.response_layer,
        )
