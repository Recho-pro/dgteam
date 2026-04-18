from __future__ import annotations

from pathlib import Path

from dgteam.core.config import WechatClawbotConfig
from dgteam.integrations.wechat_clawbot.service import WechatClawbotBridgeService
from dgteam.integrations.wechat_clawbot.wecom_crypto import WecomCallbackCrypto


class FakeQueryApp:
    def snapshot_payload(self, **_: str) -> dict:
        return {
            "ok": True,
            "header": {
                "title": "iPhone 17 Pro Max",
                "selected_gprice_labels": ["04-15"],
            },
            "hero": {
                "market_price": 9560,
            },
            "market_v1": {
                "price_range": "9540-9560",
            },
            "branches": [
                {
                    "capacity_groups": [
                        {"capacity_label": "256G", "price_range": "9540-9560"},
                        {"capacity_label": "512G", "price_range": "11395-11420"},
                    ]
                }
            ],
        }


class FakeQueryService:
    def __init__(self, results: list[dict]):
        self._results = results
        self.app = FakeQueryApp()

    def status_payload(self) -> dict:
        return {"ok": True, "run_key": "test-run"}

    def search(self, query: str, *, limit: int = 6) -> dict:
        return {
            "ok": True,
            "query": query,
            "results": self._results[:limit],
        }


class FakeClient:
    def __init__(self, sync_pages: list[dict] | None = None):
        self.sync_pages = list(sync_pages or [])
        self.sent_messages: list[dict] = []
        self.configured = True

    def sync_messages(self, *, sync_token: str, open_kfid: str, cursor: str = "", limit: int = 1000) -> dict:
        assert sync_token == "sync-token"
        assert open_kfid == "kf-open-id"
        assert limit == 1000
        if self.sync_pages:
            return self.sync_pages.pop(0)
        return {"errcode": 0, "errmsg": "ok", "has_more": 0, "msg_list": []}

    def send_text_message(self, *, touser: str, open_kfid: str, content: str) -> dict:
        payload = {
            "touser": touser,
            "open_kfid": open_kfid,
            "content": content,
        }
        self.sent_messages.append(payload)
        return {"errcode": 0, "errmsg": "ok"}


def make_config(tmp_path: Path) -> WechatClawbotConfig:
    return WechatClawbotConfig(
        enabled=True,
        host="127.0.0.1",
        port=8965,
        bridge_mode="wecom_customer_service",
        callback_path="/wechat/kf/callback",
        shared_secret="",
        corp_id="corp-test-id",
        corp_secret="corp-secret",
        callback_token="callback-token",
        encoding_aes_key="abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG",
        default_open_kfid="kf-open-id",
        api_base_url="https://qyapi.weixin.qq.com",
        inbox_dir=tmp_path / "inbox",
        archive_dir=tmp_path / "archive",
        state_dir=tmp_path / "state",
    )


def make_candidate(label: str = "iPhone 17 Pro Max") -> dict:
    return {
        "data_source": "quote_rows",
        "brand_title": "苹果",
        "series_title": "iPhone 17",
        "model_title": label,
        "family_title": label,
        "group_title": "",
        "condition_bucket": "apple_company_pure_sealed_target",
        "label": label,
        "meta": "苹果 / iPhone 17",
        "detail_key": "detail-1",
        "query_ref": {
            "data_source": "quote_rows",
            "brand_title": "苹果",
            "series_title": "iPhone 17",
            "model_title": label,
            "family_title": label,
            "group_title": "",
            "condition_bucket": "apple_company_pure_sealed_target",
            "detail_key": "detail-1",
            "external_key": "",
        },
    }


def encrypt_callback_xml(config: WechatClawbotConfig, xml_text: str, *, timestamp: str, nonce: str) -> tuple[str, str]:
    crypto = WecomCallbackCrypto(
        token=config.callback_token,
        encoding_aes_key=config.encoding_aes_key,
        receive_id=config.corp_id,
    )
    encrypted = crypto.encrypt_message(xml_text, timestamp=timestamp, nonce=nonce)
    wrapped = f"<xml><Encrypt><![CDATA[{encrypted['Encrypt']}]]></Encrypt></xml>"
    return wrapped, encrypted["MsgSignature"]


def test_verify_callback_url_roundtrip(tmp_path):
    config = make_config(tmp_path)
    crypto = WecomCallbackCrypto(
        token=config.callback_token,
        encoding_aes_key=config.encoding_aes_key,
        receive_id=config.corp_id,
    )
    encrypted = crypto.encrypt_message("verify-ok", timestamp="1710000000", nonce="nonce-1")
    service = WechatClawbotBridgeService(
        config=config,
        client=FakeClient(),
        query_service=FakeQueryService([make_candidate()]),
    )
    plaintext = service.verify_callback_url(
        msg_signature=encrypted["MsgSignature"],
        timestamp="1710000000",
        nonce="nonce-1",
        echostr=encrypted["Encrypt"],
    )
    assert plaintext == "verify-ok"


def test_handle_text_query_returns_market_summary(tmp_path):
    config = make_config(tmp_path)
    service = WechatClawbotBridgeService(
        config=config,
        client=FakeClient(),
        query_service=FakeQueryService([make_candidate()]),
    )
    reply = service.handle_text_query("iphone17promax")
    assert "iPhone 17 Pro Max" in reply
    assert "行情区间：9540-9560" in reply
    assert "256G：9540-9560" in reply


def test_handle_text_query_returns_choices_for_ambiguous_query(tmp_path):
    config = make_config(tmp_path)
    service = WechatClawbotBridgeService(
        config=config,
        client=FakeClient(),
        query_service=FakeQueryService(
            [
                make_candidate("iPhone 17"),
                make_candidate("iPhone 17 Pro"),
                make_candidate("iPhone 17 Pro Max"),
            ]
        ),
    )
    reply = service.handle_text_query("苹果17")
    assert "可能对应这些机型" in reply
    assert "1. iPhone 17" in reply
    assert "3. iPhone 17 Pro Max" in reply


def test_callback_syncs_messages_and_sends_reply_once(tmp_path):
    config = make_config(tmp_path)
    client = FakeClient(
        sync_pages=[
            {
                "errcode": 0,
                "errmsg": "ok",
                "has_more": 0,
                "msg_list": [
                    {
                        "msgid": "msg-001",
                        "open_kfid": "kf-open-id",
                        "external_userid": "external-user-1",
                        "send_time": 1710000000,
                        "origin": 3,
                        "msgtype": "text",
                        "text": {"content": "iphone17promax"},
                    }
                ],
            },
            {
                "errcode": 0,
                "errmsg": "ok",
                "has_more": 0,
                "msg_list": [
                    {
                        "msgid": "msg-001",
                        "open_kfid": "kf-open-id",
                        "external_userid": "external-user-1",
                        "send_time": 1710000000,
                        "origin": 3,
                        "msgtype": "text",
                        "text": {"content": "iphone17promax"},
                    }
                ],
            },
        ]
    )
    service = WechatClawbotBridgeService(
        config=config,
        client=client,
        query_service=FakeQueryService([make_candidate()]),
    )
    callback_xml = """
    <xml>
      <ToUserName><![CDATA[corp-test-id]]></ToUserName>
      <FromUserName><![CDATA[sys]]></FromUserName>
      <CreateTime>1710000000</CreateTime>
      <MsgType><![CDATA[event]]></MsgType>
      <Event><![CDATA[kf_msg_or_event]]></Event>
      <Token><![CDATA[sync-token]]></Token>
      <OpenKfId><![CDATA[kf-open-id]]></OpenKfId>
    </xml>
    """.strip()
    wrapped_xml, signature = encrypt_callback_xml(
        config,
        callback_xml,
        timestamp="1710000000",
        nonce="nonce-2",
    )

    result = service.handle_wecom_callback(
        raw_body=wrapped_xml,
        msg_signature=signature,
        timestamp="1710000000",
        nonce="nonce-2",
    )
    assert result["ok"] is True
    assert result["processed_count"] == 1
    assert len(client.sent_messages) == 1
    assert client.sent_messages[0]["touser"] == "external-user-1"

    second = service.handle_wecom_callback(
        raw_body=wrapped_xml,
        msg_signature=signature,
        timestamp="1710000000",
        nonce="nonce-2",
    )
    assert second["processed_messages"][0]["reason"] == "already_processed"
    assert len(client.sent_messages) == 1
