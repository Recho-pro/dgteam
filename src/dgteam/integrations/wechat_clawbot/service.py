from __future__ import annotations

import logging
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from dgteam.core.config import WechatClawbotConfig
from dgteam.integrations.wechat_clawbot.adapter import DefaultWechatClawbotAdapter
from dgteam.integrations.wechat_clawbot.commands import ClawbotCommand
from dgteam.integrations.wechat_clawbot.formatter import (
    format_ambiguous_result,
    format_market_snapshot,
    format_no_result,
)
from dgteam.integrations.wechat_clawbot.models import ClawbotAck, build_event_from_payload
from dgteam.integrations.wechat_clawbot.storage import ClawbotEventStore, ClawbotStateStore
from dgteam.integrations.wechat_clawbot.wecom_client import WecomCustomerServiceClient
from dgteam.integrations.wechat_clawbot.wecom_crypto import WecomCallbackCrypto, WecomCryptoError
from dgteam.query_api.service import QueryService

LOGGER = logging.getLogger("dgteam.wecom_bridge.service")


def parse_wecom_xml(xml_text: str) -> dict[str, Any]:
    root = ET.fromstring(str(xml_text or "").strip())
    payload: dict[str, Any] = {}
    for child in root:
        text = "".join(child.itertext()).strip()
        if list(child):
            nested: dict[str, Any] = {}
            for sub in child:
                nested[sub.tag] = "".join(sub.itertext()).strip()
            payload[child.tag] = nested
        else:
            payload[child.tag] = text
    return payload


def extract_encrypt_from_xml(xml_text: str) -> str:
    payload = parse_wecom_xml(xml_text)
    encrypted = str(payload.get("Encrypt") or "").strip()
    if not encrypted:
        raise WecomCryptoError("Callback body does not contain Encrypt.")
    return encrypted


def _string_candidates(payload: dict[str, Any], *names: str) -> str:
    lowered = {str(key).lower(): value for key, value in payload.items()}
    for name in names:
        direct = payload.get(name)
        if direct not in (None, ""):
            return str(direct).strip()
        lowered_value = lowered.get(str(name).lower())
        if lowered_value not in (None, ""):
            return str(lowered_value).strip()
    return ""


class WechatClawbotBridgeService:
    def __init__(
        self,
        *,
        config: WechatClawbotConfig,
        db_path: Path | None = None,
        client: WecomCustomerServiceClient | None = None,
        query_service: QueryService | None = None,
    ):
        self.config = config
        self.adapter = DefaultWechatClawbotAdapter()
        self.store = ClawbotEventStore(config.inbox_dir, config.archive_dir)
        self.state_store = ClawbotStateStore(config.state_dir)
        self.query = query_service or QueryService(db_path=db_path)
        self.crypto = (
            WecomCallbackCrypto(
                token=config.callback_token,
                encoding_aes_key=config.encoding_aes_key,
                receive_id=config.corp_id,
            )
            if config.callback_token and config.encoding_aes_key and config.corp_id
            else None
        )
        self.client = client or WecomCustomerServiceClient(
            corp_id=config.corp_id,
            corp_secret=config.corp_secret,
            api_base_url=config.api_base_url,
            state_store=self.state_store,
        )

    def health_payload(self) -> dict[str, object]:
        return {
            "ok": True,
            "service": "dgteam-wecom-customer-service-bridge",
            "enabled": self.config.enabled,
            "bridge_mode": self.config.bridge_mode,
            "callback_path": self.config.callback_path,
            "has_callback_crypto": self.crypto is not None,
            "has_api_credentials": self.client.configured,
            "default_open_kfid": self.config.default_open_kfid,
            "query_status": self.query.status_payload(),
            "capabilities": {
                "url_verification": True,
                "callback_decrypt": True,
                "sync_messages": True,
                "query_reply": True,
                "send_text_reply": True,
            },
        }

    def ingest_payload(self, payload: dict) -> dict[str, object]:
        event = self.adapter.normalize_event(payload)
        stored_path = self.store.save_inbox(event)
        ack = ClawbotAck(
            ok=True,
            event_id=event.event_id or stored_path.stem,
            stored_at=str(stored_path),
            message="Event accepted by DGTEAM WeCom customer-service bridge.",
        )
        return ack.to_dict()

    def route_command(self, payload: dict) -> dict[str, object]:
        command = ClawbotCommand(
            command=str(payload.get("command") or "query"),
            text=str(payload.get("text") or ""),
            sender=str(payload.get("sender") or ""),
            room=str(payload.get("room") or ""),
        )
        reply = self.handle_text_query(command.text)
        return {
            "ok": True,
            "handled": True,
            "command": command.command,
            "message": reply,
        }

    def verify_callback_url(self, *, msg_signature: str, timestamp: str, nonce: str, echostr: str) -> str:
        if self.crypto is None:
            raise WecomCryptoError("Callback crypto is not configured yet.")
        plain = self.crypto.decrypt_echo(
            msg_signature=msg_signature,
            timestamp=timestamp,
            nonce=nonce,
            echostr=echostr,
        )
        LOGGER.info(
            "wecom callback verify ok timestamp=%s nonce=%s plain=%s",
            timestamp,
            nonce,
            plain,
        )
        return plain

    def handle_wecom_callback(
        self,
        *,
        raw_body: str,
        msg_signature: str,
        timestamp: str,
        nonce: str,
    ) -> dict[str, Any]:
        if self.crypto is None:
            raise WecomCryptoError("Callback crypto is not configured yet.")

        encrypted = extract_encrypt_from_xml(raw_body)
        plain_xml = self.crypto.decrypt_message(
            msg_signature=msg_signature,
            timestamp=timestamp,
            nonce=nonce,
            encrypted=encrypted,
        )
        payload = parse_wecom_xml(plain_xml)
        self.state_store.save_last_callback_xml(plain_xml)
        callback_type = str(payload.get("Event") or payload.get("MsgType") or "callback").strip()
        LOGGER.info(
            "wecom callback decrypted callback_type=%s payload_keys=%s",
            callback_type,
            sorted(payload.keys()),
        )
        self.store.save_callback(event_name=callback_type or "callback", payload=payload, raw_xml=plain_xml)

        sync_token = self._extract_sync_token(payload)
        open_kfid = self._extract_open_kfid(payload)

        processed_messages: list[dict[str, Any]] = []
        if sync_token and open_kfid:
            LOGGER.info("wecom callback syncing messages open_kfid=%s has_token=%s", open_kfid, bool(sync_token))
            processed_messages = self._sync_and_process_messages(sync_token=sync_token, open_kfid=open_kfid)
        else:
            direct_message = self._direct_message_payload(payload, fallback_open_kfid=open_kfid)
            if direct_message:
                processed = self._process_message_payload(direct_message)
                if processed:
                    processed_messages.append(processed)

        return {
            "ok": True,
            "callback_type": callback_type,
            "processed_count": len(processed_messages),
            "processed_messages": processed_messages,
        }

    def handle_text_query(self, text: str) -> str:
        query = str(text or "").strip()
        LOGGER.info("wecom query text=%s", query)
        if not query:
            return "发一个型号给我就行，例如：iPhone17ProMax、苹果17、红米k80。"

        search_payload = self.query.search(query, limit=6)
        results = list(search_payload.get("results") or [])
        if not results:
            LOGGER.info("wecom query no_result text=%s", query)
            return format_no_result(query)

        if self._is_ambiguous_query(query, results):
            LOGGER.info("wecom query ambiguous text=%s result_count=%s", query, len(results))
            return format_ambiguous_result(query, results)

        candidate = results[0]
        snapshot = self._snapshot_for_candidate(candidate)
        if not snapshot.get("ok"):
            LOGGER.info("wecom query snapshot fallback ambiguous text=%s", query)
            return format_ambiguous_result(query, results)
        LOGGER.info("wecom query resolved text=%s label=%s", query, candidate.get("label"))
        return format_market_snapshot(candidate=candidate, snapshot=snapshot)

    def _snapshot_for_candidate(self, candidate: dict[str, Any]) -> dict[str, Any]:
        query_ref = dict(candidate.get("query_ref") or {})
        return self.query.app.snapshot_payload(
            data_source=str(query_ref.get("data_source") or candidate.get("data_source") or "quote_rows"),
            external_key=str(query_ref.get("external_key") or candidate.get("external_key") or ""),
            detail_key=str(query_ref.get("detail_key") or candidate.get("detail_key") or ""),
            brand_title=str(query_ref.get("brand_title") or candidate.get("brand_title") or ""),
            series_title=str(query_ref.get("series_title") or candidate.get("series_title") or ""),
            model_title=str(query_ref.get("model_title") or candidate.get("model_title") or ""),
            family_title=str(query_ref.get("family_title") or candidate.get("family_title") or ""),
            group_title=str(query_ref.get("group_title") or candidate.get("group_title") or ""),
            condition_bucket=str(query_ref.get("condition_bucket") or candidate.get("condition_bucket") or ""),
        )

    @staticmethod
    def _is_ambiguous_query(query: str, results: list[dict[str, Any]]) -> bool:
        labels = [str(item.get("label") or item.get("family_title") or item.get("model_title") or "").strip() for item in results]
        normalized = {WechatClawbotBridgeService._compact_surface(label) for label in labels if label}
        query_compact = WechatClawbotBridgeService._compact_surface(query)
        if not query_compact:
            return True
        if query_compact in normalized:
            return False
        return len(normalized) > 1

    @staticmethod
    def _compact_surface(text: str) -> str:
        normalized = unicodedata.normalize("NFKC", str(text or "").strip()).lower()
        return re.sub(r"[^\w\u4e00-\u9fff]+", "", normalized)

    def _extract_sync_token(self, payload: dict[str, Any]) -> str:
        event_block = payload.get("Event")
        if isinstance(event_block, dict):
            token = _string_candidates(event_block, "Token", "token", "SyncToken", "sync_token")
            if token:
                return token
        return _string_candidates(payload, "Token", "token", "SyncToken", "sync_token")

    def _extract_open_kfid(self, payload: dict[str, Any]) -> str:
        event_block = payload.get("Event")
        if isinstance(event_block, dict):
            open_kfid = _string_candidates(event_block, "OpenKfId", "open_kfid", "openKfId")
            if open_kfid:
                return open_kfid
        return _string_candidates(payload, "OpenKfId", "open_kfid", "openKfId") or self.config.default_open_kfid

    def _sync_and_process_messages(self, *, sync_token: str, open_kfid: str) -> list[dict[str, Any]]:
        processed: list[dict[str, Any]] = []
        cursor = ""
        page_count = 0
        while page_count < 5:
            page_count += 1
            response = self.client.sync_messages(
                sync_token=sync_token,
                open_kfid=open_kfid,
                cursor=cursor,
                limit=1000,
            )
            message_list = list(response.get("msg_list") or [])
            LOGGER.info(
                "wecom sync page=%s open_kfid=%s message_count=%s has_more=%s",
                page_count,
                open_kfid,
                len(message_list),
                response.get("has_more"),
            )
            for message in message_list:
                result = self._process_message_payload(dict(message), fallback_open_kfid=open_kfid)
                if result:
                    processed.append(result)

            has_more = int(response.get("has_more") or 0)
            cursor = str(response.get("next_cursor") or "").strip()
            if not has_more or not cursor:
                break
        return processed

    def _direct_message_payload(self, payload: dict[str, Any], *, fallback_open_kfid: str = "") -> dict[str, Any] | None:
        text_value = _string_candidates(payload, "Content", "content")
        msg_type = _string_candidates(payload, "MsgType", "msgtype")
        external_userid = _string_candidates(payload, "FromUserName", "external_userid", "ExternalUserID")
        if msg_type.lower() != "text" or not text_value or not external_userid:
            return None
        return {
            "msgid": _string_candidates(payload, "MsgId", "msgid") or f"direct-{int(time.time())}",
            "open_kfid": _string_candidates(payload, "OpenKfId", "open_kfid") or fallback_open_kfid,
            "external_userid": external_userid,
            "send_time": _string_candidates(payload, "CreateTime") or str(int(time.time())),
            "origin": 3,
            "msgtype": "text",
            "text": {"content": text_value},
            "source": "wecom_customer_service_callback",
        }

    def _process_message_payload(
        self,
        message_payload: dict[str, Any],
        fallback_open_kfid: str = "",
    ) -> dict[str, Any] | None:
        event = build_event_from_payload(
            {
                **message_payload,
                "open_kfid": str(message_payload.get("open_kfid") or fallback_open_kfid or ""),
                "source": str(message_payload.get("source") or "wecom_customer_service"),
            }
        )
        if not event.event_id:
            event = build_event_from_payload(
                {
                    **message_payload,
                    "id": f"msg-{hash(str(message_payload))}",
                    "open_kfid": str(message_payload.get("open_kfid") or fallback_open_kfid or ""),
                    "source": str(message_payload.get("source") or "wecom_customer_service"),
                }
            )

        if self.state_store.has_processed_message(event.event_id):
            LOGGER.info("wecom message skipped already_processed event_id=%s", event.event_id)
            return {
                "event_id": event.event_id,
                "skipped": True,
                "reason": "already_processed",
            }

        origin = str(message_payload.get("origin") or "").strip()
        msg_type = str(message_payload.get("msgtype") or event.event_type or "").strip().lower()
        if origin and origin != "3":
            LOGGER.info("wecom message skipped origin event_id=%s origin=%s", event.event_id, origin)
            self.state_store.mark_processed_message(
                event.event_id,
                {"skipped": True, "reason": f"origin_{origin}", "payload": message_payload},
            )
            return {
                "event_id": event.event_id,
                "skipped": True,
                "reason": f"origin_{origin}",
            }
        if msg_type != "text" or not event.text:
            LOGGER.info("wecom message skipped unsupported event_id=%s msg_type=%s", event.event_id, msg_type)
            self.state_store.mark_processed_message(
                event.event_id,
                {"skipped": True, "reason": f"unsupported_{msg_type or 'unknown'}", "payload": message_payload},
            )
            return {
                "event_id": event.event_id,
                "skipped": True,
                "reason": f"unsupported_{msg_type or 'unknown'}",
            }

        stored_path = self.store.save_inbox(event)
        reply_text = self.handle_text_query(event.text)
        open_kfid = str(message_payload.get("open_kfid") or fallback_open_kfid or self.config.default_open_kfid).strip()
        if not open_kfid:
            raise ValueError("Missing open_kfid for customer-service reply.")
        self.client.send_text_message(
            touser=event.sender,
            open_kfid=open_kfid,
            content=reply_text,
        )
        LOGGER.info(
            "wecom reply sent event_id=%s sender=%s open_kfid=%s preview=%s",
            event.event_id,
            event.sender,
            open_kfid,
            reply_text[:80],
        )
        self.state_store.mark_processed_message(
            event.event_id,
            {
                "stored_at": str(stored_path),
                "reply_text": reply_text,
                "sender": event.sender,
                "open_kfid": open_kfid,
            },
        )
        return {
            "event_id": event.event_id,
            "stored_at": str(stored_path),
            "sender": event.sender,
            "open_kfid": open_kfid,
            "reply_preview": reply_text[:120],
        }
