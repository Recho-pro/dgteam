from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from dgteam.core.config import WechatOfficialConfig
from dgteam.integrations.wechat_official.formatter import (
    format_image_query_deferred,
    format_image_unsupported,
    format_market_snapshot,
)
from dgteam.integrations.wechat_official.image_recognizer import WechatOfficialEcommerceImageRecognizer
from dgteam.integrations.wechat_official.ingress import extract_encrypt_from_xml, parse_xml
from dgteam.integrations.wechat_official.menu import build_default_menu
from dgteam.integrations.wechat_official.models import (
    WechatOfficialFastLaneOutcome,
    WechatOfficialImageCandidateResolution,
    WechatOfficialInboundMessage,
    WechatOfficialRecognitionResult,
    WechatOfficialRecognitionTask,
)
from dgteam.integrations.wechat_official.result_dispatcher import WechatOfficialImageResultDispatcher
from dgteam.integrations.wechat_official.service import WechatOfficialService
from dgteam.integrations.wechat_official.session_store import WechatOfficialSessionStore
from dgteam.integrations.wechat_official.trace import WechatOfficialTraceLogger
from dgteam.integrations.wechat_official.workflow import WechatOfficialWorkflow
from dgteam.integrations.wechat_official.response_layer import WechatOfficialMarketResponseLayer
from dgteam.query_api.snapshot_assembly import refinement_resolution_payload
from dgteam.query_api.snapshot_refinement import refine_snapshot


class FakeQueryApp:
    def snapshot_payload(self, **kwargs: str) -> dict:
        title = str(kwargs.get("family_title") or kwargs.get("model_title") or "Current Model")
        if title == "iPhone 17":
            return _snapshot_payload(title="iPhone 17", price_range="5530-5590", market_price=5575)
        if title == "iPhone 17 Pro":
            return _snapshot_payload(title="iPhone 17 Pro", price_range="7910-7990", market_price=7950)
        if title == "Series 11 GPS":
            return _snapshot_payload(
                title="Series 11 GPS",
                price_range="2120-2126",
                market_price=2122,
                capacity_groups=[
                    {
                        "capacity_label": "默认规格",
                        "market_price": 2122,
                        "price_range": "2120-2126",
                        "selected_gprice_labels": ["04-16"],
                        "colors": [
                            {
                                "color_label": "42mm银色 运动(UU4)M/L",
                                "group_title": "42mm银色 运动(UU4)M/L",
                                "market_price": 2122,
                                "price_range": "2120-2126",
                                "selected_gprice_labels": ["04-16"],
                            },
                            {
                                "color_label": "42mm深空灰 运动(UG4)S/M",
                                "group_title": "42mm深空灰 运动(UG4)S/M",
                                "market_price": 2068,
                                "price_range": "2045-2079",
                                "selected_gprice_labels": ["04-16"],
                            },
                            {
                                "color_label": "46mm深空灰 运动(VN4)S/M",
                                "group_title": "46mm深空灰 运动(VN4)S/M",
                                "market_price": 2344,
                                "price_range": "2341-2348",
                                "selected_gprice_labels": ["04-16"],
                            },
                            {
                                "color_label": "46mm银色 运动(VW4)S/M",
                                "group_title": "46mm银色 运动(VW4)S/M",
                                "market_price": 2329,
                                "price_range": "2327-2330",
                                "selected_gprice_labels": ["04-16"],
                            },
                        ],
                    }
                ],
            )
        if title == "Series 11 GPS+蜂窝":
            return _snapshot_payload(
                title="Series 11 GPS+蜂窝",
                price_range="2990-3010",
                market_price=3000,
                capacity_groups=[
                    {
                        "capacity_label": "默认规格",
                        "market_price": 3000,
                        "price_range": "2990-3010",
                        "selected_gprice_labels": ["04-16"],
                        "colors": [
                            {
                                "color_label": "46mm深空灰 蜂窝版",
                                "group_title": "46mm深空灰 蜂窝版",
                                "market_price": 3000,
                                "price_range": "2990-3010",
                                "selected_gprice_labels": ["04-16"],
                            }
                        ],
                    }
                ],
            )
        if title == "吸尘器":
            return _snapshot_payload(
                title="吸尘器",
                price_range="3252-3284",
                market_price=3274,
                capacity_groups=[
                    {
                        "capacity_label": "默认规格",
                        "market_price": 3274,
                        "price_range": "3252-3284",
                        "selected_gprice_labels": ["04-16"],
                        "colors": [
                            {
                                "color_label": "V15 Detect Fluffy",
                                "group_title": "V15 Detect Fluffy",
                                "market_price": 3274,
                                "price_range": "3252-3284",
                                "selected_gprice_labels": ["04-16"],
                            },
                            {
                                "color_label": "V12 Detect Slim Fluffy",
                                "group_title": "V12 Detect Slim Fluffy",
                                "market_price": 3150,
                                "price_range": "3150-3150",
                                "selected_gprice_labels": ["04-16"],
                            },
                            {
                                "color_label": "G5 Detect Fluffy",
                                "group_title": "G5 Detect Fluffy",
                                "market_price": 4050,
                                "price_range": "4050-4050",
                                "selected_gprice_labels": ["04-16"],
                            },
                            {
                                "color_label": "WashG1 洗地机",
                                "group_title": "WashG1 洗地机",
                                "market_price": 3185,
                                "price_range": "3180-3190",
                                "selected_gprice_labels": ["04-16"],
                            },
                        ],
                    }
                ],
            )
        if title == "Apple充电头/数据线":
            return _snapshot_payload(
                title="Apple充电头/数据线",
                price_range="28-230",
                market_price=50,
                capacity_groups=[
                    {
                        "capacity_label": "默认规格",
                        "market_price": 50,
                        "price_range": "28-230",
                        "selected_gprice_labels": ["04-16"],
                        "colors": [
                            {
                                "color_label": "Apple 20W 充电头",
                                "group_title": "Apple 20W 充电头",
                                "market_price": 50,
                                "price_range": "50-50",
                                "selected_gprice_labels": ["04-16"],
                            },
                            {
                                "color_label": "30W USB-C端口电源适配器 2G3",
                                "group_title": "30W USB-C端口电源适配器 2G3",
                                "market_price": 230,
                                "price_range": "230-230",
                                "selected_gprice_labels": ["04-16"],
                            },
                            {
                                "color_label": "Apple USB-双C口平板编织快充线1m( KJ3)",
                                "group_title": "Apple USB-双C口平板编织快充线1m( KJ3)",
                                "market_price": 29,
                                "price_range": "28-30",
                                "selected_gprice_labels": ["04-16"],
                            },
                        ],
                    }
                ],
            )
        if title == "Air Pods 4":
            return _snapshot_payload(
                title="Air Pods 4",
                price_range="755-758",
                market_price=755,
                capacity_groups=[
                    {
                        "capacity_label": "默认规格",
                        "market_price": 755,
                        "price_range": "755-758",
                        "selected_gprice_labels": ["04-16"],
                        "colors": [
                            {
                                "color_label": "标准款 白色 P63",
                                "group_title": "标准款 白色 P63",
                                "market_price": 755,
                                "price_range": "755-758",
                                "selected_gprice_labels": ["04-16"],
                            },
                            {
                                "color_label": "降噪款 白色 P93",
                                "group_title": "降噪款 白色 P93",
                                "market_price": 1085,
                                "price_range": "1084-1085",
                                "selected_gprice_labels": ["04-16"],
                            },
                            {
                                "color_label": "降噪款 P93 充电盒",
                                "group_title": "降噪款 P93 充电盒",
                                "market_price": 420,
                                "price_range": "420-450",
                                "selected_gprice_labels": ["04-16"],
                            },
                            {
                                "color_label": "标准款 P63 充电盒",
                                "group_title": "标准款 P63 充电盒",
                                "market_price": 310,
                                "price_range": "300-320",
                                "selected_gprice_labels": ["04-16"],
                            },
                        ],
                    }
                ],
            )
        if title == "Apple Pencil (手写笔)":
            return _snapshot_payload(
                title="Apple Pencil (手写笔)",
                price_range="780-792",
                market_price=785,
                capacity_groups=[
                    {
                        "capacity_label": "默认规格",
                        "market_price": 785,
                        "price_range": "780-792",
                        "selected_gprice_labels": ["04-16"],
                        "colors": [
                            {
                                "color_label": "Pencil Pro (2024款) 2D3",
                                "group_title": "Pencil Pro (2024款) 2D3",
                                "market_price": 785,
                                "price_range": "780-792",
                                "selected_gprice_labels": ["04-16"],
                            },
                            {
                                "color_label": "Pencil USB-C(2023款) WA3",
                                "group_title": "Pencil USB-C(2023款) WA3",
                                "market_price": 500,
                                "price_range": "490-510",
                                "selected_gprice_labels": ["04-16"],
                            },
                            {
                                "color_label": "Pencil 2代(2025款) N43",
                                "group_title": "Pencil 2代(2025款) N43",
                                "market_price": 650,
                                "price_range": "640-675",
                                "selected_gprice_labels": ["04-16"],
                            },
                        ],
                    }
                ],
            )
        if title == "小新14SE(14C) 2025款":
            return _snapshot_payload(
                title="小新14SE(14C) 2025款",
                price_range="4010-4030",
                market_price=4029,
                capacity_groups=[
                    {
                        "capacity_label": "16G",
                        "market_price": 4029,
                        "price_range": "4010-4030",
                        "selected_gprice_labels": ["04-16"],
                        "colors": [
                            {
                                "color_label": "R7-8745HS512G 深灰色",
                                "group_title": "R7-8745HS 16G 512G 深灰色",
                                "market_price": 3710,
                                "price_range": "3700-3720",
                                "selected_gprice_labels": ["04-16"],
                            },
                            {
                                "color_label": "i5-13420H512G 深灰色",
                                "group_title": "i5-13420H 16G 512G 深灰色",
                                "market_price": 4029,
                                "price_range": "4010-4030",
                                "selected_gprice_labels": ["04-16"],
                            },
                        ],
                    }
                ],
            )
        if title == "MagicBook Pro 14 2025款":
            return _snapshot_payload(
                title="MagicBook Pro 14 2025款",
                price_range="5380-5900",
                market_price=5380,
                capacity_groups=[
                    {
                        "capacity_label": "16G+1T",
                        "market_price": 5380,
                        "price_range": "5380-5380",
                        "selected_gprice_labels": ["04-16"],
                        "colors": [
                            {
                                "color_label": "银色",
                                "group_title": "16G+1T 银色",
                                "market_price": 5380,
                                "price_range": "5380-5380",
                                "selected_gprice_labels": ["04-16"],
                            },
                            {
                                "color_label": "灰色",
                                "group_title": "16G+1T 灰色",
                                "market_price": 5450,
                                "price_range": "5450-5450",
                                "selected_gprice_labels": ["04-16"],
                            },
                        ],
                    },
                    {
                        "capacity_label": "32G+1T",
                        "market_price": 5875,
                        "price_range": "5850-5900",
                        "selected_gprice_labels": ["04-16"],
                        "colors": [
                            {
                                "color_label": "银色",
                                "group_title": "32G+1T 银色",
                                "market_price": 5875,
                                "price_range": "5850-5900",
                                "selected_gprice_labels": ["04-16"],
                            }
                        ],
                    },
                ],
            )
        return _snapshot_payload(title="iPhone 17 Pro Max", price_range="9540-9560", market_price=9560)


class FakeQueryService:
    def __init__(self):
        self.app = FakeQueryApp()
        self.search_calls: list[str] = []
        self.snapshot_calls: list[dict[str, object]] = []

    def status_payload(self) -> dict[str, object]:
        return {"ok": True, "run_key": "test-run"}

    def search(self, query: str, *, limit: int = 6) -> dict[str, object]:
        self.search_calls.append(query)
        normalized = _compact(query)
        if normalized in {"iphone17promax", "17promax", "iphone17promax256g"}:
            results = [_candidate("iPhone 17 Pro Max")]
        elif normalized in {"苹果17", "apple17"}:
            results = [
                _candidate("iPhone 17"),
                _candidate("iPhone 17 Pro"),
                _candidate("iPhone 17 Pro Max"),
            ]
        elif normalized in {"iphone17promax512g", "17promax512g"}:
            results = [_candidate("iPhone 17 Pro Max")]
        elif normalized in {"联想小新14se16g512g", "联想小新14se512g", "小新14se16g512g", "小新14se(14c)2025款"}:
            results = [_candidate("小新14SE(14C) 2025款", brand_title="联想电脑", series_title="Lenovo 小新")]
        elif normalized in {"magicbookpro142025款", "荣耀magicbookpro142025款"}:
            results = [_candidate("MagicBook Pro 14 2025款", brand_title="荣耀", series_title="MagicBook")]
        elif normalized in {"magicbookpro142025款16g+1t银色", "荣耀magicbookpro142025款16g+1t银色"}:
            results = [
                _candidate("MagicBook Pro 16 2025款", brand_title="荣耀", series_title="MagicBook"),
                _candidate("MagicBook Pro 16 2025 HUNTER版", brand_title="荣耀", series_title="MagicBook"),
                _candidate("MagicBook Pro 14 2025款", brand_title="荣耀", series_title="MagicBook"),
                _candidate("MagicBook Pro 14 2025 极客版", brand_title="荣耀", series_title="MagicBook"),
                _candidate("MagicBook Art 14 2025款", brand_title="荣耀", series_title="MagicBook"),
                _candidate("MagicBook Pro 16 2024款", brand_title="荣耀", series_title="MagicBook"),
            ]
        elif normalized in {"series11gps", "applewatchs11深空灰色gps"}:
            results = [
                _candidate("Series 11 GPS+蜂窝", brand_title="苹果", series_title="Apple Watch"),
                _candidate("Series 11 GPS", brand_title="苹果", series_title="Apple Watch"),
                _candidate("SE3 GPS(2025款)", brand_title="苹果", series_title="Apple Watch"),
            ]
        elif normalized in {"detectfluffy", "v15detectfluffy"}:
            results = [_candidate("吸尘器", brand_title="网红潮品", series_title="戴森")]
        elif normalized in {"apple20w充电头"}:
            results = [
                _candidate("Apple充电头/数据线", brand_title="苹果", series_title="Apple 配件"),
                _candidate("Apple Watch 磁力充电器", brand_title="苹果", series_title="Apple 配件"),
            ]
        elif normalized in {"applepencilpro"}:
            results = [
                _candidate("Apple Pencil (手写笔)", brand_title="苹果", series_title="Apple 配件"),
                _candidate("Apple充电头/数据线", brand_title="苹果", series_title="Apple 配件"),
            ]
        elif normalized in {"airpods4", "airpods4降噪款"}:
            results = [_candidate("Air Pods 4", brand_title="苹果", series_title="Apple 耳机")]
        else:
            results = []
        return {
            "ok": True,
            "query": query,
            "results": results[:limit],
        }

    def snapshot(self, **kwargs: object) -> dict[str, object]:
        self.snapshot_calls.append(dict(kwargs))
        refinement_query = str(kwargs.get("refinement_query") or "")
        payload = self.app.snapshot_payload(**kwargs)
        refinement = refine_snapshot(payload, refinement_query)
        payload = refinement.snapshot
        resolution = dict(payload.get("resolution") or {})
        resolution["refinement"] = refinement_resolution_payload(refinement, refinement_query)
        payload["resolution"] = resolution
        if refinement_query:
            query_payload = dict(payload.get("query") or {})
            query_payload["refinement_query"] = refinement_query
            payload["query"] = query_payload
        return payload


class FakeFastLane:
    def __init__(self, outcome: WechatOfficialFastLaneOutcome):
        self.outcome = outcome
        self.calls: list[str] = []

    def probe(self, message: WechatOfficialInboundMessage) -> WechatOfficialFastLaneOutcome:
        self.calls.append(message.from_user)
        return self.outcome


class FakeWechatClient:
    def __init__(self):
        self.sent_messages: list[tuple[str, str]] = []

    def send_custom_text(self, *, open_id: str, content: str):
        self.sent_messages.append((open_id, content))
        return {"errcode": 0, "errmsg": "ok"}


def make_config(tmp_path: Path, *, image_api_key: str = "") -> WechatOfficialConfig:
    return WechatOfficialConfig(
        enabled=True,
        host="127.0.0.1",
        port=8966,
        callback_path="/wechat/official/callback",
        app_id="wx-test-app",
        app_secret="secret-test",
        callback_token="callback-token",
        encoding_aes_key="abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG",
        api_base_url="https://api.weixin.qq.com",
        state_dir=tmp_path / "official_state",
        image_worker_enabled=True,
        image_api_key=image_api_key,
        image_fast_model="openai/gpt-4.1-mini",
        image_fast_timeout_seconds=8,
        image_fast_max_edge_px=1400,
        image_fast_max_bytes=350000,
        image_fast_jpeg_quality=80,
        image_primary_model="google/gemini-2.5-flash",
        image_fallback_model="qwen/qwen3-vl-235b-a22b-instruct",
        image_poll_interval_seconds=1.0,
        image_timeout_seconds=30,
        image_max_edge_px=2000,
        image_max_bytes=700000,
        image_jpeg_quality=84,
    )


def make_text_message(content: str, *, open_id: str = "user-open-id") -> WechatOfficialInboundMessage:
    return WechatOfficialInboundMessage(
        msg_type="text",
        event="",
        event_key="",
        from_user=open_id,
        to_user="gh_test",
        content=content,
        media_id="",
        pic_url="",
        msg_id=f"msg-{_compact(content) or 'text'}",
        raw_payload={},
    )


def make_image_message(*, open_id: str = "user-open-id") -> WechatOfficialInboundMessage:
    return WechatOfficialInboundMessage(
        msg_type="image",
        event="",
        event_key="",
        from_user=open_id,
        to_user="gh_test",
        content="",
        media_id="media-1",
        pic_url="",
        msg_id="msg-image-1",
        raw_payload={},
    )


def make_event_message(event: str, *, event_key: str = "", open_id: str = "user-open-id") -> WechatOfficialInboundMessage:
    return WechatOfficialInboundMessage(
        msg_type="event",
        event=event,
        event_key=event_key,
        from_user=open_id,
        to_user="gh_test",
        content="",
        media_id="",
        pic_url="",
        msg_id=f"msg-{event}-{event_key or 'none'}",
        raw_payload={},
    )


def _candidate(label: str, *, brand_title: str = "苹果", series_title: str = "iPhone 17") -> dict[str, object]:
    return {
        "data_source": "quote_rows",
        "brand_title": brand_title,
        "series_title": series_title,
        "model_title": label,
        "family_title": label,
        "group_title": "",
        "condition_bucket": "apple_company_pure_sealed_target" if brand_title == "苹果" else "non_apple_allowed",
        "label": label,
        "meta": f"{brand_title} / {series_title}",
        "detail_key": f"detail-{_compact(label)}",
        "query_ref": {
            "data_source": "quote_rows",
            "external_key": "",
            "detail_key": f"detail-{_compact(label)}",
            "brand_title": brand_title,
            "series_title": series_title,
            "model_title": label,
            "family_title": label,
            "group_title": "",
            "condition_bucket": "apple_company_pure_sealed_target" if brand_title == "苹果" else "non_apple_allowed",
        },
    }


def _snapshot_payload(
    *,
    title: str,
    price_range: str,
    market_price: int,
    capacity_groups: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    normalized_groups = capacity_groups or [
        {"capacity_label": "256G", "market_price": 9560, "price_range": "9540-9560"},
        {"capacity_label": "512G", "market_price": 11410, "price_range": "11395-11420"},
    ]
    return {
        "ok": True,
        "header": {
            "title": title,
            "selected_gprice_labels": ["04-16"],
        },
        "hero": {
            "market_price": market_price,
        },
        "market_v1": {
            "price_range": price_range,
        },
        "branches": [
            {
                "capacity_groups": normalized_groups
            }
        ],
    }


def _compact(text: str) -> str:
    return "".join(str(text or "").strip().lower().split())


def _url_signature(token: str, timestamp: str, nonce: str) -> str:
    parts = [token, timestamp, nonce]
    parts.sort()
    return hashlib.sha1("".join(parts).encode("utf-8")).hexdigest()


def _wrap_encrypted_callback(
    service: WechatOfficialService,
    plain_xml: str,
    *,
    timestamp: str = "1710000000",
    nonce: str = "nonce-1",
) -> tuple[str, str]:
    encrypted = service.ingress.crypto.encrypt_message(plain_xml, timestamp=timestamp, nonce=nonce)
    wrapped = f"<xml><Encrypt><![CDATA[{encrypted['Encrypt']}]]></Encrypt></xml>"
    return wrapped, encrypted["MsgSignature"]


def _decrypt_reply_xml(service: WechatOfficialService, response_xml: str) -> str:
    payload = parse_xml(response_xml)
    return service.ingress.crypto.decrypt_message(
        msg_signature=str(payload.get("MsgSignature") or ""),
        timestamp=str(payload.get("TimeStamp") or ""),
        nonce=str(payload.get("Nonce") or ""),
        encrypted=extract_encrypt_from_xml(response_xml),
    )


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_official_callback_verification_roundtrip(tmp_path: Path):
    service = WechatOfficialService(
        config=make_config(tmp_path),
        query_service=FakeQueryService(),
    )

    timestamp = "1710000000"
    nonce = "nonce-verify"
    signature = _url_signature(service.config.callback_token, timestamp, nonce)

    assert service.verify_callback_url(
        msg_signature=signature,
        timestamp=timestamp,
        nonce=nonce,
        echostr="verify-ok",
    ) == "verify-ok"


def test_official_text_callback_returns_snapshot_and_writes_trace(tmp_path: Path):
    service = WechatOfficialService(
        config=make_config(tmp_path),
        query_service=FakeQueryService(),
    )
    plain_xml = """
    <xml>
      <ToUserName><![CDATA[gh_test]]></ToUserName>
      <FromUserName><![CDATA[user-open-id]]></FromUserName>
      <CreateTime>1710000000</CreateTime>
      <MsgType><![CDATA[text]]></MsgType>
      <Content><![CDATA[iphone17promax]]></Content>
      <MsgId>msg-001</MsgId>
    </xml>
    """.strip()
    wrapped_xml, signature = _wrap_encrypted_callback(service, plain_xml)

    result = service.handle_callback(
        raw_body=wrapped_xml,
        msg_signature=signature,
        timestamp="1710000000",
        nonce="nonce-1",
    )

    assert result["ok"] is True
    assert "iPhone 17 Pro Max" in result["reply_text"]
    assert "现在大概区间：9540-9560" in result["reply_text"]
    decrypted_reply = _decrypt_reply_xml(service, str(result["response_xml"]))
    assert "<MsgType><![CDATA[text]]></MsgType>" in decrypted_reply
    assert "现在大概区间：9540-9560" in decrypted_reply

    trace_status = service.trace.status_payload()
    assert trace_status["event_files"] == 1
    assert trace_status["conversation_files"] == 1
    event_file = Path(str(trace_status["latest_event_file"]))
    events = _read_jsonl(event_file)
    assert [event["category"] for event in events] == ["inbound", "reply:passive"]
    assert Path(service.config.state_dir / "last_callback.xml").exists()


def test_workflow_supports_ambiguous_choice_and_short_context_refinement(tmp_path: Path):
    query = FakeQueryService()
    workflow = WechatOfficialWorkflow(query_service=query, state_dir=tmp_path / "workflow")

    first = workflow.handle_text_message(make_text_message("苹果17"))
    assert "我先帮你缩到这几项：" in first
    assert "3. iPhone 17 Pro Max" in first

    second = workflow.handle_text_message(make_text_message("3"))
    assert "iPhone 17 Pro Max" in second
    assert "现在大概区间：9540-9560" in second

    search_count_before_refinement = len(query.search_calls)
    third = workflow.handle_text_message(make_text_message("512G"))
    assert "你刚刚补的是：512G" in third
    assert "这一档当前参考区间：11395-11420" in third
    assert len(query.search_calls) == search_count_before_refinement

    session = workflow.session_store.load("user-open-id")
    assert session.pending_candidates == []
    assert session.last_result_title == "iPhone 17 Pro Max"
    assert session.last_candidate["family_title"] == "iPhone 17 Pro Max"

    fourth = workflow.handle_text_message(make_text_message("512"))
    assert "你刚刚补的是：512" in fourth or "你刚刚补的是：512G" in fourth
    assert "11395-11420" in fourth


def test_response_layer_refinement_consumes_backend_snapshot_contract():
    query = FakeQueryService()
    layer = WechatOfficialMarketResponseLayer(query_service=query)

    plan = layer.resolve_refinement_query(
        base_candidate=_candidate("iPhone 17 Pro Max"),
        refinement_query="512G",
    )

    assert plan.kind == "snapshot"
    assert query.snapshot_calls[-1]["refinement_query"] == "512G"
    assert plan.snapshot["resolution"]["refinement"]["applied"] is True
    assert "11395-11420" in plan.reply_text


def test_workflow_keeps_laptop_refinement_inside_previous_family(tmp_path: Path):
    query = FakeQueryService()
    workflow = WechatOfficialWorkflow(query_service=query, state_dir=tmp_path / "magicbook-workflow")

    first = workflow.handle_text_message(make_text_message("MagicBook Pro 14 2025款"))
    assert "MagicBook Pro 14 2025款" in first
    assert "现在大概区间：5380-5900" in first

    search_count_before_refinement = len(query.search_calls)
    second = workflow.handle_text_message(make_text_message("16G+1T 银色"))
    assert "MagicBook Pro 14 2025款" in second
    assert "当前这款：16G+1T 银色" in second
    assert "当前参考区间：5380-5380" in second
    assert len(query.search_calls) == search_count_before_refinement

    session = workflow.session_store.load("user-open-id")
    assert session.last_result_title == "MagicBook Pro 14 2025款"
    assert session.last_candidate["family_title"] == "MagicBook Pro 14 2025款"


def test_market_snapshot_prefers_informative_variant_label_for_laptop_groups():
    candidate = _candidate("小新14SE(14C) 2025款", brand_title="联想电脑", series_title="Lenovo 小新")
    snapshot = _snapshot_payload(
        title="小新14SE(14C) 2025款",
        price_range="4010-4030",
        market_price=4029,
        capacity_groups=[
            {
                "capacity_label": "16G",
                "market_price": 4029,
                "price_range": "4010-4030",
                "selected_gprice_labels": ["04-16"],
                "colors": [
                    {
                        "color_label": "R7-8745HS512G 深灰色",
                        "group_title": "R7-8745HS 16G 512G 深灰色",
                        "market_price": 3710,
                        "price_range": "3700-3720",
                    },
                    {
                        "color_label": "i5-13420H512G 深灰色",
                        "group_title": "i5-13420H 16G 512G 深灰色",
                        "market_price": 4029,
                        "price_range": "4010-4030",
                    },
                ],
            }
        ],
    )

    reply = format_market_snapshot(candidate=candidate, snapshot=snapshot)

    assert "常见规格：" in reply
    assert "i5-13420H 16G 512G 深灰色：4010-4030" in reply
    assert "16G：4010-4030" not in reply


def test_workflow_can_refine_laptop_by_storage_hint_from_variant_titles(tmp_path: Path):
    query = FakeQueryService()
    workflow = WechatOfficialWorkflow(query_service=query, state_dir=tmp_path / "lenovo-workflow")

    first = workflow.handle_text_message(make_text_message("小新14SE(14C) 2025款"))
    assert "小新14SE(14C) 2025款" in first
    assert "i5-13420H 16G 512G 深灰色：4010-4030" in first

    search_count_before_refinement = len(query.search_calls)
    second = workflow.handle_text_message(make_text_message("512"))

    assert "小新14SE(14C) 2025款" in second
    assert "这一档当前参考区间：3700-4030" in second
    assert "i5-13420H512G 深灰色：4010-4030" in second
    assert "R7-8745HS512G 深灰色：3700-3720" in second
    assert "你刚刚补的是：512" in second or "你刚刚补的是：512G" in second
    assert len(query.search_calls) == search_count_before_refinement


def test_watch_query_auto_refines_and_prefers_gps_without_cellular():
    query = FakeQueryService()
    layer = WechatOfficialMarketResponseLayer(query_service=query)

    plan = layer.resolve_query("Apple Watch S11 深空灰色 GPS", limit=6)

    assert plan.kind == "snapshot"
    assert plan.reply_text.splitlines()[0] == "Series 11 GPS"
    assert "深空灰" in plan.reply_text
    assert "蜂窝" not in plan.reply_text


def test_appliance_query_filters_to_detect_fluffy_variants():
    query = FakeQueryService()
    layer = WechatOfficialMarketResponseLayer(query_service=query)

    plan = layer.resolve_query("Detect Fluffy", limit=6)

    assert plan.kind == "snapshot"
    assert plan.reply_text.splitlines()[0] == "Detect Fluffy"
    assert "中间参考：¥3,274" in plan.reply_text
    assert "V15 Detect Fluffy" in plan.reply_text
    assert "V12 Detect Slim Fluffy" in plan.reply_text
    assert "WashG1 洗地机" not in plan.reply_text


def test_accessory_query_auto_enters_snapshot_and_promotes_variant_title():
    query = FakeQueryService()
    layer = WechatOfficialMarketResponseLayer(query_service=query)

    plan = layer.resolve_query("Apple 20W 充电头", limit=6)

    assert plan.kind == "snapshot"
    assert plan.reply_text.splitlines()[0] == "Apple 20W 充电头"
    assert "当前参考区间：50-50" in plan.reply_text
    assert "磁力充电器" not in plan.reply_text


def test_airpods_refinement_shows_only_matching_variant_subset():
    query = FakeQueryService()
    layer = WechatOfficialMarketResponseLayer(query_service=query)

    plan = layer.resolve_query("Air Pods 4 降噪款", limit=6)

    assert plan.kind == "snapshot"
    assert "当前这款：降噪款 白色 P93" in plan.reply_text
    assert "标准款 白色 P63" not in plan.reply_text
    assert "充电盒" not in plan.reply_text
    assert "中间参考：¥1,085" in plan.reply_text


def test_pencil_pro_query_auto_refines_to_specific_variant():
    query = FakeQueryService()
    layer = WechatOfficialMarketResponseLayer(query_service=query)

    plan = layer.resolve_query("Apple Pencil Pro", limit=6)

    assert plan.kind == "snapshot"
    assert plan.reply_text.splitlines()[0] == "Apple Pencil (手写笔)"
    assert "Pencil Pro (2024款) 2D3" in plan.reply_text
    assert "Pencil USB-C(2023款)" not in plan.reply_text


def test_image_resolution_auto_refines_snapshot_with_configuration_hints():
    query = FakeQueryService()
    layer = WechatOfficialMarketResponseLayer(query_service=query)

    resolution = layer.resolve_image_candidates(
        recognized_summary="联想 / 小新 14 SE / 16G 512G",
        candidate_queries=["联想小新14SE 16G 512G", "小新14SE(14C) 2025款"],
        preferred_brand="联想",
        preferred_family="小新 14 SE",
    )

    assert resolution.kind == "snapshot"
    assert "小新14SE(14C) 2025款" in resolution.reply_text
    assert "这档常见配置：" in resolution.reply_text
    assert "i5-13420H 16G 512G 深灰色" in resolution.reply_text
    assert "你刚刚补的是：" not in resolution.reply_text


@pytest.mark.parametrize(
    ("recognized_summary", "candidate_queries", "preferred_brand", "preferred_family", "expected_title", "expected_includes", "expected_excludes"),
    [
        (
            "苹果 / Apple Watch S11 / 深空灰色 / GPS",
            ["Apple Watch S11 深空灰色 GPS", "Series 11 GPS"],
            "苹果",
            "Apple Watch S11",
            "Series 11 GPS",
            ["深空灰", "Series 11 GPS"],
            ["蜂窝"],
        ),
        (
            "戴森 / Detect Fluffy",
            ["Detect Fluffy", "V15 Detect Fluffy"],
            "",
            "",
            "Detect Fluffy",
            ["V15 Detect Fluffy", "V12 Detect Slim Fluffy"],
            ["WashG1 洗地机"],
        ),
        (
            "苹果 / 20W 充电头",
            ["Apple 20W 充电头", "Apple 20W"],
            "苹果",
            "Apple 配件",
            "Apple 20W 充电头",
            ["50-50", "Apple 20W 充电头"],
            ["磁力充电器"],
        ),
        (
            "苹果 / AirPods 4 / 降噪款",
            ["Air Pods 4 降噪款", "Air Pods 4"],
            "苹果",
            "Air Pods 4",
            "Air Pods 4",
            ["降噪款 白色 P93"],
            ["标准款 白色 P63", "充电盒"],
        ),
    ],
)
def test_image_regression_matrix_for_watch_appliance_and_accessories(
    recognized_summary: str,
    candidate_queries: list[str],
    preferred_brand: str,
    preferred_family: str,
    expected_title: str,
    expected_includes: list[str],
    expected_excludes: list[str],
):
    layer = WechatOfficialMarketResponseLayer(query_service=FakeQueryService())

    resolution = layer.resolve_image_candidates(
        recognized_summary=recognized_summary,
        candidate_queries=candidate_queries,
        preferred_brand=preferred_brand,
        preferred_family=preferred_family,
    )

    assert resolution.kind == "snapshot"
    assert expected_title in resolution.reply_text
    for token in expected_includes:
        assert token in resolution.reply_text
    for token in expected_excludes:
        assert token not in resolution.reply_text


def test_official_image_callback_is_deduped_when_wechat_retries_same_payload(tmp_path: Path):
    service = WechatOfficialService(
        config=make_config(tmp_path),
        query_service=FakeQueryService(),
    )
    timestamp = "1710000001"
    nonce = "nonce-dup"
    plain_xml = """
    <xml>
      <ToUserName><![CDATA[gh_test]]></ToUserName>
      <FromUserName><![CDATA[user-image-open-id]]></FromUserName>
      <CreateTime>1710000001</CreateTime>
      <MsgType><![CDATA[image]]></MsgType>
      <PicUrl><![CDATA[https://example.com/test.png]]></PicUrl>
      <MediaId><![CDATA[media-dup-1]]></MediaId>
      <MsgId>msg-image-dup-1</MsgId>
    </xml>
    """.strip()
    wrapped_xml, signature = _wrap_encrypted_callback(service, plain_xml, timestamp=timestamp, nonce=nonce)

    first = service.handle_callback(
        raw_body=wrapped_xml,
        msg_signature=signature,
        timestamp=timestamp,
        nonce=nonce,
    )
    second = service.handle_callback(
        raw_body=wrapped_xml,
        msg_signature=signature,
        timestamp=timestamp,
        nonce=nonce,
    )

    assert first["ok"] is True
    assert "图片收到了" in first["reply_text"]
    assert second["ok"] is True
    assert second["deduped"] is True
    assert service.workflow.recognition_queue.status_payload()["queued"] == 1


def test_workflow_handles_image_deferred_status_and_direct_hit(tmp_path: Path):
    query = FakeQueryService()
    state_dir = tmp_path / "image-flow"
    workflow = WechatOfficialWorkflow(query_service=query, state_dir=state_dir)

    deferred_task = workflow.recognition_queue.build_image_task(make_image_message())
    deferred_outcome = WechatOfficialFastLaneOutcome(
        status="deferred",
        task=deferred_task,
        reply_text=format_image_query_deferred(
            recognized_summary="联想 / 小新14 SE / 512G",
            query_hint="联想小新14 SE 512G",
            task_id=deferred_task.task_id,
        ),
        timings_ms={"total_ms": 320.0},
    )
    workflow.fast_lane = FakeFastLane(deferred_outcome)

    deferred_reply = workflow.handle_image_message(make_image_message())
    assert "图片我先接住了，正在继续核对。" in deferred_reply
    assert workflow.recognition_queue.status_payload()["queued"] == 1

    queued_status = workflow.handle_text_message(make_text_message("进度"))
    assert "还在排队处理中" in queued_status

    claimed = workflow.recognition_queue.claim_next()
    assert claimed is not None
    processing_status = workflow.handle_text_message(make_text_message("进度"))
    assert "我正在看这张图" in processing_status

    workflow.recognition_queue.complete(
        claimed,
        WechatOfficialRecognitionResult(
            task_id=claimed.task_id,
            status="success",
            recognized_query="联想小新14 SE 512G",
            confidence="medium",
            model="openai/gpt-4.1-mini",
            candidates=["联想小新14 SE 512G"],
            summary="联想 / 小新14 SE / 512G",
            raw_payload={},
        ),
    )
    completed_status = workflow.handle_text_message(make_text_message("进度"))
    assert "这张图已经处理完了" in completed_status

    direct_hit_task = workflow.recognition_queue.build_image_task(make_image_message(open_id="user-direct"))
    direct_hit_outcome = WechatOfficialFastLaneOutcome(
        status="direct_hit",
        task=direct_hit_task,
        reply_text="iPhone 17 Pro Max\n现在大概区间：9540-9560",
        recognition_result=WechatOfficialRecognitionResult(
            task_id=direct_hit_task.task_id,
            status="success",
            recognized_query="iPhone 17 Pro Max 256G",
            confidence="high",
            model="openai/gpt-4.1-mini",
            candidates=["iPhone 17 Pro Max 256G"],
            summary="苹果 / iPhone 17 Pro Max / 256G",
            raw_payload={},
        ),
        resolution=WechatOfficialImageCandidateResolution(
            kind="snapshot",
            reply_text="iPhone 17 Pro Max\n现在大概区间：9540-9560",
            resolved_query="iPhone 17 Pro Max 256G",
            resolved_title="iPhone 17 Pro Max",
            matched_query="iPhone 17 Pro Max 256G",
        ),
        timings_ms={"total_ms": 180.0},
    )
    workflow.fast_lane = FakeFastLane(direct_hit_outcome)

    direct_reply = workflow.handle_image_message(make_image_message(open_id="user-direct"))
    assert "现在大概区间：9540-9560" in direct_reply
    direct_session = workflow.session_store.load("user-direct")
    assert direct_session.pending_task_id == ""
    assert direct_session.last_result_title == "iPhone 17 Pro Max"

    terminal_task = workflow.recognition_queue.build_image_task(make_image_message(open_id="user-terminal"))
    terminal_outcome = WechatOfficialFastLaneOutcome(
        status="terminal",
        task=terminal_task,
        reply_text="这张更像整张报价表，不是单个商品详情页。\n如果你要查单个型号，直接发对应商品详情截图，或者直接发机型名给我更快。",
        timings_ms={"total_ms": 120.0},
    )
    workflow.fast_lane = FakeFastLane(terminal_outcome)

    terminal_reply = workflow.handle_image_message(make_image_message(open_id="user-terminal"))
    assert "整张报价表" in terminal_reply
    assert workflow.recognition_queue.status_payload()["queued"] == 0
    terminal_session = workflow.session_store.load("user-terminal")
    assert terminal_session.pending_task_id == ""
    assert terminal_session.pending_image == {}


def test_dispatcher_failed_result_sends_natural_fallback_and_traces(tmp_path: Path):
    client = FakeWechatClient()
    trace = WechatOfficialTraceLogger(tmp_path / "trace")
    dispatcher = WechatOfficialImageResultDispatcher(
        client=client,
        response_layer=FakeQueryService(),  # not used for failed results
        session_store=WechatOfficialSessionStore(tmp_path / "sessions"),
        trace_logger=trace,
    )
    task = WechatOfficialRecognitionTask(
        task_id="task-failed",
        open_id="user-image",
        created_at=1710000000,
        updated_at=1710000000,
        status="processing",
        media_id="media-1",
        pic_url="",
        msg_id="msg-1",
    )
    result = WechatOfficialRecognitionResult(
        task_id="task-failed",
        status="failed",
        recognized_query="",
        confidence="low",
        model="openai/gpt-4.1-mini",
        candidates=[],
        summary="截图太糊了，标题没看清。",
        raw_payload={},
    )

    reply = dispatcher.handle(task, result)

    assert "这张图我这次没看明白。" in reply
    assert "优先支持商品详情页截图" in reply
    assert client.sent_messages == [("user-image", reply)]

    trace_status = trace.status_payload()
    assert trace_status["event_files"] == 1
    events = _read_jsonl(Path(str(trace_status["latest_event_file"])))
    assert events[-1]["category"] == "reply:custom"


def test_formatter_localizes_quote_sheet_and_chat_like_unsupported_replies():
    quote_reply = format_image_unsupported(
        reason="The image is a price list table with multiple products.",
        page_type="quote_sheet",
    )
    assert "整张报价表" in quote_reply
    assert "The image" not in quote_reply

    chat_reply = format_image_unsupported(
        reason="Embedded product screenshot inside a chat conversation.",
        page_type="embedded_chat_screenshot",
    )
    assert "聊天记录" in chat_reply
    assert "embedded" not in chat_reply.casefold()


def test_image_candidate_builder_adds_apple_watch_series_aliases():
    payload = {
        "brand": "Apple",
        "family": "Apple Watch S11",
        "capacity": "",
        "color": "深空灰色",
        "edition": "GPS",
        "query_candidates": [],
    }

    candidates = WechatOfficialEcommerceImageRecognizer._build_candidates(payload)

    assert "Apple Watch Series 11 GPS" in candidates
    assert "Series 11 GPS" in candidates


def test_brand_aliases_accept_mixed_chinese_english_surface():
    aliases = WechatOfficialMarketResponseLayer._brand_aliases("机械革命 (MECHREVO)")
    assert "机械革命" in aliases
    assert "mechrevo" in aliases


def test_menu_payload_and_click_routes_are_ready_for_product_use(tmp_path: Path):
    menu = build_default_menu(base_url="https://dgtdnb.com")
    assert menu["button"][0]["url"] == "https://dgtdnb.com/"
    assert menu["button"][1]["sub_button"][0]["url"].endswith("?q=iPhone17ProMax")
    assert menu["button"][2]["sub_button"][1]["key"] == "DG_IMAGE_HELP"
    assert menu["button"][2]["sub_button"][2]["key"] == "DG_CONTACT"

    workflow = WechatOfficialWorkflow(query_service=FakeQueryService(), state_dir=tmp_path / "menu-flow")
    help_reply = workflow.handle_message(make_event_message("click", event_key="DG_HELP"))
    image_help_reply = workflow.handle_message(make_event_message("click", event_key="DG_IMAGE_HELP"))
    contact_reply = workflow.handle_message(make_event_message("click", event_key="DG_CONTACT"))
    subscribe_reply = workflow.handle_message(make_event_message("subscribe"))

    assert "这样用最快：" in help_reply
    assert "截图这样发，识别会更稳：" in image_help_reply
    assert "Recho1688" in contact_reply
    assert "DG 团队行情助手" in subscribe_reply
