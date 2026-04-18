from __future__ import annotations

from pathlib import Path

from PIL import Image

from dgteam.integrations.wechat_official.fast_lane import WechatOfficialImageFastLane
from dgteam.integrations.wechat_official.image_recognizer import WechatOfficialEcommerceImageRecognizer
from dgteam.integrations.wechat_official.models import (
    WechatOfficialImageCandidateResolution,
    WechatOfficialInboundMessage,
    WechatOfficialMarketReplyPlan,
    WechatOfficialRecognitionResult,
    WechatOfficialRecognitionTask,
)
from dgteam.integrations.wechat_official.recognition_queue import WechatOfficialRecognitionQueue
from dgteam.integrations.wechat_official.response_layer import WechatOfficialMarketResponseLayer
from dgteam.integrations.wechat_official.result_dispatcher import WechatOfficialImageResultDispatcher
from dgteam.integrations.wechat_official.session_store import WechatOfficialSessionStore


class FakeWechatClient:
    def __init__(self, image_bytes: bytes | None = None):
        self.download_calls = 0
        self.sent_messages: list[tuple[str, str]] = []
        self.image_bytes = image_bytes or b"fake-image"

    def download_media(self, media_id: str):
        self.download_calls += 1
        return self.image_bytes, "detail.jpg", "image/jpeg"

    def download_image_url(self, url: str):
        self.download_calls += 1
        return self.image_bytes, "detail.jpg", "image/jpeg"

    def send_custom_text(self, *, open_id: str, content: str):
        self.sent_messages.append((open_id, content))
        return {"errcode": 0, "errmsg": "ok"}


class FakeResponseLayer:
    def resolve_image_candidates(
        self,
        *,
        recognized_summary: str,
        candidate_queries: list[str],
        preferred_brand: str = "",
        preferred_family: str = "",
        query_limit: int = 3,
        max_queries: int = 4,
    ):
        return WechatOfficialImageCandidateResolution(
            kind="snapshot",
            reply_text="我看图里像是：\n苹果 / iPhone 17 Pro Max / 256G\n\niPhone 17 Pro Max\n当前参考区间：9540-9560",
            resolved_query=str(candidate_queries[0] if candidate_queries else ""),
            resolved_title="iPhone 17 Pro Max",
        )


class FakeQueryApp:
    def snapshot_payload(self, **kwargs: str) -> dict:
        family_title = str(kwargs.get("family_title") or kwargs.get("model_title") or "当前机型")
        return {
            "ok": True,
            "header": {
                "title": family_title,
                "selected_gprice_labels": ["04-16"],
            },
            "hero": {
                "market_price": 4263,
            },
            "market_v1": {
                "price_range": "4200-4300",
            },
            "branches": [
                {
                    "capacity_groups": [
                        {"capacity_label": "512G", "price_range": "4200-4300"},
                    ]
                }
            ],
        }


class FakeQueryService:
    def __init__(self, results: list[dict]):
        self._results = results
        self.app = FakeQueryApp()

    def search(self, query: str, *, limit: int = 6) -> dict:
        return {
            "ok": True,
            "query": query,
            "results": self._results[:limit],
        }


def make_task() -> WechatOfficialRecognitionTask:
    return WechatOfficialRecognitionTask(
        task_id="task-1",
        open_id="openid-image",
        created_at=1710000000,
        updated_at=1710000000,
        status="queued",
        media_id="media-1",
        pic_url="",
        msg_id="msg-1",
    )


def make_image_message() -> WechatOfficialInboundMessage:
    return WechatOfficialInboundMessage(
        msg_type="image",
        event="",
        event_key="",
        from_user="openid-image",
        to_user="gh_xxx",
        content="",
        media_id="media-1",
        pic_url="",
        msg_id="msg-1",
        raw_payload={},
    )


def _large_test_image_bytes() -> bytes:
    image = Image.new("RGB", (2200, 3400), (255, 120, 40))
    for x in range(0, 2200, 120):
        for y in range(0, 3400, 120):
            image.putpixel((x, y), (10, 10, 10))
    from io import BytesIO

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_image_recognizer_extracts_candidates_and_uses_cache(tmp_path: Path, monkeypatch):
    fake_client = FakeWechatClient()
    call_counter = {"count": 0}

    def fake_openrouter_chat_json(**_: object) -> dict:
        call_counter["count"] += 1
        return {
            "supported": True,
            "page_type": "ecommerce_product_detail",
            "brand": "苹果",
            "series": "iPhone 17",
            "family": "iPhone 17 Pro Max",
            "capacity": "256GB",
            "color": "星宇橙色",
            "edition": "5G",
            "screen_price_text": "9699",
            "query_candidates": ["iPhone 17 Pro Max 256GB"],
            "confidence": "high",
            "reason": "title and selected sku are visible",
            "warnings": [],
        }

    monkeypatch.setattr(
        "dgteam.integrations.wechat_official.image_recognizer.openrouter_chat_json",
        fake_openrouter_chat_json,
    )

    recognizer = WechatOfficialEcommerceImageRecognizer(
        client=fake_client,
        api_key="test-openrouter-key",
        primary_model="qwen/qwen3.5-9b",
        fallback_model="qwen/qwen3-vl-32b-instruct",
        cache_dir=tmp_path / "cache",
    )

    first = recognizer.recognize(make_task())
    second = recognizer.recognize(make_task())

    assert first.status == "success"
    assert first.recognized_query == "iPhone 17 Pro Max 256G 星宇橙色"
    assert first.candidates[:3] == [
        "iPhone 17 Pro Max 256G 星宇橙色",
        "iPhone 17 Pro Max 256G",
        "iPhone 17 Pro Max 星宇橙色",
    ]
    assert "苹果 / iPhone 17 Pro Max / 256G / 星宇橙色" == first.summary
    assert second.recognized_query == first.recognized_query
    assert call_counter["count"] == 1
    assert fake_client.download_calls == 2


def test_image_recognizer_preprocesses_large_image_before_model_call(tmp_path: Path, monkeypatch):
    captured: dict[str, object] = {}
    fake_client = FakeWechatClient(image_bytes=_large_test_image_bytes())

    def fake_openrouter_chat_json(*, messages, **_: object) -> dict:
        image_url = messages[1]["content"][1]["image_url"]["url"]
        captured["image_url"] = image_url
        return {
            "supported": True,
            "page_type": "ecommerce_product_detail",
            "brand": "苹果",
            "series": "iPhone 17",
            "family": "iPhone 17 Pro Max",
            "capacity": "256G",
            "color": "星宇橙色",
            "edition": "国行",
            "screen_price_text": "9699",
            "query_candidates": ["iPhone 17 Pro Max 256G"],
            "confidence": "high",
            "reason": "ok",
            "warnings": [],
        }

    monkeypatch.setattr(
        "dgteam.integrations.wechat_official.image_recognizer.openrouter_chat_json",
        fake_openrouter_chat_json,
    )

    recognizer = WechatOfficialEcommerceImageRecognizer(
        client=fake_client,
        api_key="test-openrouter-key",
        primary_model="qwen/qwen3.5-9b",
        fallback_model="qwen/qwen3-vl-32b-instruct",
        cache_dir=tmp_path / "cache",
        max_edge_px=1200,
        max_bytes=280000,
        jpeg_quality=76,
    )

    result = recognizer.recognize(make_task())

    assert result.status == "success"
    preprocess = result.raw_payload["preprocess"]
    assert preprocess["original_size"] == [2200, 3400]
    assert max(preprocess["processed_size"]) <= 1200
    assert preprocess["processed_bytes"] <= 280000
    assert str(captured["image_url"]).startswith("data:image/jpeg;base64,")


def test_image_recognizer_returns_failed_result_when_model_raises(tmp_path: Path, monkeypatch):
    fake_client = FakeWechatClient()

    def fake_openrouter_chat_json(**_: object) -> dict:
        raise RuntimeError("upstream timed out")

    monkeypatch.setattr(
        "dgteam.integrations.wechat_official.image_recognizer.openrouter_chat_json",
        fake_openrouter_chat_json,
    )

    recognizer = WechatOfficialEcommerceImageRecognizer(
        client=fake_client,
        api_key="test-openrouter-key",
        primary_model="qwen/qwen3.5-9b",
        fallback_model="qwen/qwen3-vl-32b-instruct",
        cache_dir=tmp_path / "cache",
    )

    result = recognizer.recognize(make_task())

    assert result.status == "failed"
    assert "timed out" in result.summary


def test_image_recognizer_drops_unspecified_edition_from_candidates(tmp_path: Path, monkeypatch):
    fake_client = FakeWechatClient()

    def fake_openrouter_chat_json(**_: object) -> dict:
        return {
            "supported": True,
            "page_type": "ecommerce_product_detail",
            "brand": "联想",
            "series": "小新系列",
            "family": "联想小新14 SE",
            "capacity": "512GB",
            "color": "",
            "edition": "未指定",
            "screen_price_text": "4263",
            "query_candidates": ["联想小新14 SE 512G 未指定"],
            "confidence": "high",
            "reason": "title is visible",
            "warnings": [],
        }

    monkeypatch.setattr(
        "dgteam.integrations.wechat_official.image_recognizer.openrouter_chat_json",
        fake_openrouter_chat_json,
    )

    recognizer = WechatOfficialEcommerceImageRecognizer(
        client=fake_client,
        api_key="test-openrouter-key",
        primary_model="qwen/qwen3-vl-32b-instruct",
        fallback_model="qwen/qwen3-vl-235b-a22b-instruct",
        cache_dir=tmp_path / "cache",
    )

    result = recognizer.recognize(make_task())

    assert result.recognized_query == "联想小新14 SE 512G"
    assert "未指定" not in result.recognized_query
    assert "未指定" not in result.summary


def test_image_recognizer_keeps_plain_family_before_noisy_edition(tmp_path: Path, monkeypatch):
    fake_client = FakeWechatClient()

    def fake_openrouter_chat_json(**_: object) -> dict:
        return {
            "supported": True,
            "page_type": "ecommerce_product_detail",
            "brand": "Apple",
            "series": "iPhone 17",
            "family": "iPhone 17 Pro Max",
            "capacity": "256GB",
            "color": "星宇橙色",
            "edition": "5G双卡双待",
            "screen_price_text": "9699",
            "query_candidates": ["Apple iPhone 17 Pro Max 256GB"],
            "confidence": "high",
            "reason": "ok",
            "warnings": [],
        }

    monkeypatch.setattr(
        "dgteam.integrations.wechat_official.image_recognizer.openrouter_chat_json",
        fake_openrouter_chat_json,
    )

    recognizer = WechatOfficialEcommerceImageRecognizer(
        client=fake_client,
        api_key="test-openrouter-key",
        primary_model="google/gemini-2.5-flash",
        fallback_model="openai/gpt-4.1",
        cache_dir=tmp_path / "cache",
    )

    result = recognizer.recognize(make_task())

    assert result.candidates == [
        "iPhone 17 Pro Max 256G 星宇橙色",
        "iPhone 17 Pro Max 256G",
        "iPhone 17 Pro Max 星宇橙色",
        "iPhone 17 Pro Max",
    ]


def test_image_recognizer_drops_unknown_color_and_duplicate_capacity(tmp_path: Path, monkeypatch):
    fake_client = FakeWechatClient()

    def fake_openrouter_chat_json(**_: object) -> dict:
        return {
            "supported": True,
            "page_type": "ecommerce_product_detail",
            "brand": "Apple",
            "series": "iPhone 17",
            "family": "iPhone 17 Pro Max 256GB",
            "capacity": "256GB",
            "color": "null",
            "edition": "",
            "screen_price_text": "9699",
            "query_candidates": [],
            "confidence": "high",
            "reason": "ok",
            "warnings": [],
        }

    monkeypatch.setattr(
        "dgteam.integrations.wechat_official.image_recognizer.openrouter_chat_json",
        fake_openrouter_chat_json,
    )

    recognizer = WechatOfficialEcommerceImageRecognizer(
        client=fake_client,
        api_key="test-openrouter-key",
        primary_model="openai/gpt-4.1-mini",
        fallback_model="openai/gpt-4.1",
        cache_dir=tmp_path / "cache",
    )

    result = recognizer.recognize(make_task())

    assert result.candidates == ["iPhone 17 Pro Max 256G", "iPhone 17 Pro Max"]


def test_image_recognizer_simplifies_noisy_family_titles(tmp_path: Path, monkeypatch):
    fake_client = FakeWechatClient()

    def fake_openrouter_chat_json(**_: object) -> dict:
        return {
            "supported": True,
            "page_type": "ecommerce_product_detail",
            "brand": "联想",
            "series": "小新",
            "family": "联想笔记本电脑小新14SE高能本",
            "capacity": "16G 512G",
            "color": "null",
            "edition": "null",
            "screen_price_text": "4263.01",
            "query_candidates": [],
            "confidence": "high",
            "reason": "ok",
            "warnings": [],
        }

    monkeypatch.setattr(
        "dgteam.integrations.wechat_official.image_recognizer.openrouter_chat_json",
        fake_openrouter_chat_json,
    )

    recognizer = WechatOfficialEcommerceImageRecognizer(
        client=fake_client,
        api_key="test-openrouter-key",
        primary_model="google/gemini-2.5-flash",
        fallback_model="openai/gpt-4.1",
        cache_dir=tmp_path / "cache",
    )

    result = recognizer.recognize(make_task())

    assert result.candidates == ["联想 小新14SE 16G 512G", "联想 小新14SE"]


def test_result_dispatcher_sends_market_reply():
    fake_client = FakeWechatClient()
    dispatcher = WechatOfficialImageResultDispatcher(
        client=fake_client,
        response_layer=FakeResponseLayer(),
    )
    task = make_task()
    result = WechatOfficialRecognitionResult(
        task_id="task-1",
        status="success",
        recognized_query="iPhone 17 Pro Max 256G",
        confidence="high",
        model="qwen/qwen3.5-9b",
        candidates=["iPhone 17 Pro Max 256G"],
        summary="苹果 / iPhone 17 Pro Max / 256G / 星宇橙色 / 国行",
        raw_payload={},
    )

    reply_text = dispatcher.handle(task, result)

    assert "我看图里像是：" in reply_text
    assert "当前参考区间：9540-9560" in reply_text
    assert fake_client.sent_messages[0][0] == "openid-image"
    assert "iPhone 17 Pro Max" in fake_client.sent_messages[0][1]


class AmbiguousResponseLayer:
    def resolve_query(self, query: str, *, limit: int = 6, preferred_brand: str = "", preferred_family: str = ""):
        return WechatOfficialMarketReplyPlan(
            kind="ambiguous",
            query=query,
            results=[
                {"label": "iPhone 17", "model_title": "iPhone 17"},
                {"label": "iPhone 17 Pro", "model_title": "iPhone 17 Pro"},
                {"label": "iPhone 17 Pro Max", "model_title": "iPhone 17 Pro Max"},
            ],
            reply_text="",
        )


class FourthCandidateSnapshotLayer:
    def __init__(self):
        self.seen_queries: list[str] = []

    def resolve_query(self, query: str, *, limit: int = 6, preferred_brand: str = "", preferred_family: str = ""):
        self.seen_queries.append(query)
        if query == "iPhone 17 Pro Max":
            return WechatOfficialMarketReplyPlan(
                kind="snapshot",
                query=query,
                reply_text="iPhone 17 Pro Max\n当前参考区间：9480-9520",
            )
        return WechatOfficialMarketReplyPlan(
            kind="ambiguous",
            query=query,
            results=[
                {"label": "iPhone 17 Pro Max", "model_title": "iPhone 17 Pro Max"},
                {"label": "iPhone 17 Pro", "model_title": "iPhone 17 Pro"},
            ],
            reply_text="",
        )


def test_result_dispatcher_preserves_candidates_for_numeric_followup(tmp_path: Path):
    fake_client = FakeWechatClient()
    session_store = WechatOfficialSessionStore(tmp_path / "sessions")
    dispatcher = WechatOfficialImageResultDispatcher(
        client=fake_client,
        response_layer=AmbiguousResponseLayer(),
        session_store=session_store,
    )
    task = make_task()
    result = WechatOfficialRecognitionResult(
        task_id="task-2",
        status="success",
        recognized_query="苹果17",
        confidence="medium",
        model="qwen/qwen3.5-9b",
        candidates=["苹果17"],
        summary="苹果 / iPhone 17 系列",
        raw_payload={},
    )

    reply_text = dispatcher.handle(task, result)
    session = session_store.load("openid-image")

    assert "更像下面这几个：" in reply_text
    assert len(session.pending_candidates) == 3
    assert session.pending_task_id == ""


def test_result_dispatcher_can_resolve_snapshot_on_fourth_candidate():
    fake_client = FakeWechatClient()
    response_layer = FourthCandidateSnapshotLayer()
    dispatcher = WechatOfficialImageResultDispatcher(
        client=fake_client,
        response_layer=response_layer,
    )
    task = make_task()
    result = WechatOfficialRecognitionResult(
        task_id="task-3",
        status="success",
        recognized_query="iPhone 17 Pro Max 256G 星宇橙色",
        confidence="high",
        model="qwen/qwen3-vl-32b-instruct",
        candidates=[
            "iPhone 17 Pro Max 256G 星宇橙色",
            "iPhone 17 Pro Max 256G",
            "iPhone 17 Pro Max 星宇橙色",
            "iPhone 17 Pro Max",
        ],
        summary="苹果 / iPhone 17 Pro Max / 256G / 星宇橙色",
        raw_payload={},
    )

    reply_text = dispatcher.handle(task, result)

    assert "当前参考区间：9480-9520" in reply_text
    assert response_layer.seen_queries[:4] == [
        "iPhone 17 Pro Max 256G 星宇橙色",
        "iPhone 17 Pro Max 256G",
        "iPhone 17 Pro Max 星宇橙色",
        "iPhone 17 Pro Max",
    ]


def test_response_layer_prefers_same_brand_and_family_for_image_query():
    response_layer = WechatOfficialMarketResponseLayer(
        query_service=FakeQueryService(
            [
                {
                    "brand_title": "华为",
                    "series_title": "MateBook D系列",
                    "model_title": "MateBook D 14 SE 2025款 Linux版",
                    "family_title": "MateBook D 14 SE 2025款 Linux版",
                    "label": "MateBook D 14 SE 2025款 Linux版",
                    "query_ref": {
                        "brand_title": "华为",
                        "series_title": "MateBook D系列",
                        "model_title": "MateBook D 14 SE 2025款 Linux版",
                        "family_title": "MateBook D 14 SE 2025款 Linux版",
                        "condition_bucket": "non_apple_allowed",
                    },
                },
                {
                    "brand_title": "联想电脑",
                    "series_title": "小新系列",
                    "model_title": "小新14SE(14C) 2025款",
                    "family_title": "联想小新14 SE",
                    "label": "小新14SE(14C) 2025款",
                    "query_ref": {
                        "brand_title": "联想电脑",
                        "series_title": "小新系列",
                        "model_title": "小新14SE(14C) 2025款",
                        "family_title": "联想小新14 SE",
                        "condition_bucket": "non_apple_allowed",
                    },
                },
            ]
        )
    )

    plan = response_layer.resolve_query(
        "联想小新14 SE 512G",
        limit=3,
        preferred_brand="联想",
        preferred_family="联想小新14 SE",
    )

    assert plan.kind == "snapshot"
    assert plan.candidate["brand_title"] == "联想电脑"
    assert plan.candidate["family_title"] == "联想小新14 SE"


def test_image_recognizer_uses_staged_local_image_without_redownload(tmp_path: Path, monkeypatch):
    fake_client = FakeWechatClient(image_bytes=b"remote-image-that-should-not-be-used")
    local_image = tmp_path / "staged.jpg"
    local_image.write_bytes(b"staged-image")

    captured: dict[str, object] = {}

    def fake_openrouter_chat_json(*, messages, **_: object) -> dict:
        captured["image_url"] = messages[1]["content"][1]["image_url"]["url"]
        return {
            "supported": True,
            "page_type": "ecommerce_product_detail",
            "brand": "苹果",
            "series": "iPhone 17",
            "family": "iPhone 17 Pro Max",
            "capacity": "256GB",
            "color": "星宇橙色",
            "edition": "国行",
            "screen_price_text": "9699",
            "query_candidates": ["iPhone 17 Pro Max 256GB"],
            "confidence": "high",
            "reason": "ok",
            "warnings": [],
        }

    monkeypatch.setattr(
        "dgteam.integrations.wechat_official.image_recognizer.openrouter_chat_json",
        fake_openrouter_chat_json,
    )

    recognizer = WechatOfficialEcommerceImageRecognizer(
        client=fake_client,
        api_key="test-openrouter-key",
        primary_model="openai/gpt-4.1-mini",
        fallback_model="",
        cache_dir=tmp_path / "cache",
    )
    task = WechatOfficialRecognitionTask(
        task_id="task-staged",
        open_id="openid-image",
        created_at=1710000000,
        updated_at=1710000000,
        status="queued",
        media_id="media-1",
        pic_url="",
        msg_id="msg-1",
        metadata={
            "downloaded_image_path": str(local_image),
            "downloaded_image_name": "detail.jpg",
            "downloaded_content_type": "image/jpeg",
        },
    )

    result = recognizer.recognize(task)

    assert result.status == "success"
    assert fake_client.download_calls == 0
    assert str(captured["image_url"]).startswith("data:image/jpeg;base64,")


class StubFastRecognizer:
    def __init__(self, result: WechatOfficialRecognitionResult):
        self.result = result
        self.calls: list[tuple[str, int]] = []

    def recognize_image_blob(self, *, task_id: str, image_name: str, image_bytes: bytes, content_type: str = "", query_hint: str = ""):
        self.calls.append((task_id, len(image_bytes)))
        return self.result


class FastLaneSnapshotResponseLayer:
    def resolve_image_candidates(self, **kwargs):
        return WechatOfficialImageCandidateResolution(
            kind="snapshot",
            reply_text="我看图里像是：\n苹果 / iPhone 17 Pro Max / 256G\n\niPhone 17 Pro Max\n当前参考区间：9540-9560",
            resolved_query="iPhone 17 Pro Max 256G",
            resolved_title="iPhone 17 Pro Max",
            matched_query="iPhone 17 Pro Max 256G",
        )


def test_fast_lane_returns_direct_hit_without_enqueue(tmp_path: Path):
    queue = WechatOfficialRecognitionQueue(tmp_path / "recognition")
    fake_client = FakeWechatClient(image_bytes=b"wechat-image")
    recognizer = StubFastRecognizer(
        WechatOfficialRecognitionResult(
            task_id="task-fast",
            status="success",
            recognized_query="iPhone 17 Pro Max 256G",
            confidence="high",
            model="openai/gpt-4.1-mini",
            candidates=["iPhone 17 Pro Max 256G"],
            summary="苹果 / iPhone 17 Pro Max / 256G",
            raw_payload={"brand": "苹果", "family": "iPhone 17 Pro Max"},
        )
    )
    fast_lane = WechatOfficialImageFastLane(
        client=fake_client,
        queue=queue,
        recognizer=recognizer,
        response_layer=FastLaneSnapshotResponseLayer(),
    )

    outcome = fast_lane.probe(make_image_message())

    assert outcome.status == "direct_hit"
    assert "当前参考区间：9540-9560" in outcome.reply_text
    assert len(list((tmp_path / "recognition" / "queued").glob("*.json"))) == 0
    assert len(list((tmp_path / "recognition" / "downloads").glob("*"))) == 1


class FastLaneNoResultResponseLayer:
    def resolve_image_candidates(self, **kwargs):
        return WechatOfficialImageCandidateResolution(
            kind="no_result",
            reply_text="no result",
        )


def test_fast_lane_deferred_keeps_query_hint_and_staged_image(tmp_path: Path):
    queue = WechatOfficialRecognitionQueue(tmp_path / "recognition")
    fake_client = FakeWechatClient(image_bytes=b"wechat-image")
    recognizer = StubFastRecognizer(
        WechatOfficialRecognitionResult(
            task_id="task-fast",
            status="success",
            recognized_query="联想小新14 SE 512G",
            confidence="medium",
            model="openai/gpt-4.1-mini",
            candidates=["联想小新14 SE 512G"],
            summary="联想 / 小新14 SE / 512G",
            raw_payload={"brand": "联想", "family": "联想小新14 SE"},
        )
    )
    fast_lane = WechatOfficialImageFastLane(
        client=fake_client,
        queue=queue,
        recognizer=recognizer,
        response_layer=FastLaneNoResultResponseLayer(),
    )

    outcome = fast_lane.probe(make_image_message())

    assert outcome.status == "deferred"
    assert "图片我先接住了，正在继续核对。" in outcome.reply_text
    assert outcome.task.query_hint == "联想小新14 SE 512G"
    staged_path = Path(str(outcome.task.metadata["downloaded_image_path"]))
    assert staged_path.exists()
