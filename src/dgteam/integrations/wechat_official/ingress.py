from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

from dgteam.core.config import WechatOfficialConfig
from dgteam.integrations.wechat_official.crypto import WechatOfficialCrypto, WechatOfficialCryptoError
from dgteam.integrations.wechat_official.models import WechatOfficialInboundMessage


LOGGER = logging.getLogger("dgteam.wechat_official.ingress")


def parse_xml(xml_text: str) -> dict[str, Any]:
    root = ET.fromstring(str(xml_text or "").strip())
    payload: dict[str, Any] = {}
    for child in root:
        payload[child.tag] = "".join(child.itertext()).strip()
    return payload


def extract_encrypt_from_xml(xml_text: str) -> str:
    payload = parse_xml(xml_text)
    encrypted = str(payload.get("Encrypt") or "").strip()
    if not encrypted:
        raise WechatOfficialCryptoError("Callback body does not contain Encrypt.")
    return encrypted


def build_text_reply_xml(*, to_user: str, from_user: str, content: str, timestamp: int | None = None) -> str:
    created = int(timestamp or time.time())
    return (
        "<xml>"
        f"<ToUserName><![CDATA[{to_user}]]></ToUserName>"
        f"<FromUserName><![CDATA[{from_user}]]></FromUserName>"
        f"<CreateTime>{created}</CreateTime>"
        "<MsgType><![CDATA[text]]></MsgType>"
        f"<Content><![CDATA[{content}]]></Content>"
        "</xml>"
    )


def build_encrypted_reply_xml(*, encrypted: dict[str, str]) -> str:
    return (
        "<xml>"
        f"<Encrypt><![CDATA[{encrypted['Encrypt']}]]></Encrypt>"
        f"<MsgSignature><![CDATA[{encrypted['MsgSignature']}]]></MsgSignature>"
        f"<TimeStamp>{encrypted['TimeStamp']}</TimeStamp>"
        f"<Nonce><![CDATA[{encrypted['Nonce']}]]></Nonce>"
        "</xml>"
    )


@dataclass(slots=True)
class WechatOfficialDecodedCallback:
    plain_xml: str
    payload: dict[str, Any]
    message: WechatOfficialInboundMessage


class WechatOfficialIngress:
    def __init__(self, config: WechatOfficialConfig):
        self.config = config
        self.crypto = (
            WechatOfficialCrypto(
                token=config.callback_token,
                encoding_aes_key=config.encoding_aes_key,
                app_id=config.app_id,
            )
            if config.callback_token and config.encoding_aes_key and config.app_id
            else None
        )

    def verify_callback_url(self, *, msg_signature: str, timestamp: str, nonce: str, echostr: str) -> str:
        if self.crypto is None:
            raise WechatOfficialCryptoError("Official account callback crypto is not configured yet.")
        if not self.crypto.verify_url_signature(signature=msg_signature, timestamp=timestamp, nonce=nonce):
            raise WechatOfficialCryptoError("Invalid callback signature.")
        plain = str(echostr or "")
        LOGGER.info("wechat official callback verify ok timestamp=%s nonce=%s plain=%s", timestamp, nonce, plain)
        return plain

    def decode_callback(self, *, raw_body: str, msg_signature: str, timestamp: str, nonce: str) -> WechatOfficialDecodedCallback:
        if self.crypto is None:
            raise WechatOfficialCryptoError("Official account callback crypto is not configured yet.")
        encrypted = extract_encrypt_from_xml(raw_body)
        plain_xml = self.crypto.decrypt_message(
            msg_signature=msg_signature,
            timestamp=timestamp,
            nonce=nonce,
            encrypted=encrypted,
        )
        payload = parse_xml(plain_xml)
        return WechatOfficialDecodedCallback(
            plain_xml=plain_xml,
            payload=payload,
            message=WechatOfficialInboundMessage.from_payload(payload),
        )

    def encode_reply(
        self,
        *,
        to_user: str,
        from_user: str,
        reply_text: str,
        timestamp: str,
        nonce: str,
    ) -> str:
        if self.crypto is None:
            raise WechatOfficialCryptoError("Official account callback crypto is not configured yet.")
        plaintext_reply = build_text_reply_xml(
            to_user=to_user,
            from_user=from_user,
            content=reply_text,
        )
        encrypted_reply = self.crypto.encrypt_message(
            plaintext_reply,
            timestamp=timestamp or str(int(time.time())),
            nonce=nonce or "dgteam",
        )
        return build_encrypted_reply_xml(encrypted=encrypted_reply)
