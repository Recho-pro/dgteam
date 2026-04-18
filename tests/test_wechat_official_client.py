from __future__ import annotations

import json
from pathlib import Path

from dgteam.core.config import WechatOfficialConfig
from dgteam.integrations.wechat_official.client import WechatOfficialClient


class FakeResponse:
    def __init__(
        self,
        payload: dict | None = None,
        *,
        content: bytes = b"",
        headers: dict | None = None,
        url: str = "https://api.weixin.qq.com/mock",
    ):
        self._payload = payload or {}
        self.content = content
        self.headers = headers or {}
        self.url = url

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class FakeSession:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        if url.endswith("/cgi-bin/token"):
            return FakeResponse({"access_token": "token-123", "expires_in": 7200}, url=url)
        if url.endswith("/cgi-bin/media/get"):
            return FakeResponse(
                {"errcode": 0},
                content=b"image-bytes",
                headers={
                    "Content-Type": "image/jpeg",
                    "Content-Disposition": 'attachment; filename="sku.jpg"',
                },
                url=url,
            )
        return FakeResponse(
            {"errcode": 0},
            content=b"external-image",
            headers={"Content-Type": "image/png"},
            url=url,
        )

    def post(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse({"errcode": 0, "errmsg": "ok"}, url=url)


def make_config(tmp_path: Path) -> WechatOfficialConfig:
    return WechatOfficialConfig(
        enabled=True,
        host="127.0.0.1",
        port=8975,
        callback_path="/wechat/official/callback",
        app_id="wx8635bd6606152c36",
        app_secret="secret-demo",
        callback_token="6iRnMhCOB9hV4JiDF9pDPxaDvGIpFh",
        encoding_aes_key="HsY2uOYVxCIBJvImClyoJgFqcAoj7GKOl34XmQv8CvO",
        api_base_url="https://api.weixin.qq.com",
        state_dir=tmp_path / "state",
        image_worker_enabled=False,
        image_api_key="test-openrouter-key",
        image_fast_model="openai/gpt-4.1-mini",
        image_fast_timeout_seconds=4,
        image_fast_max_edge_px=768,
        image_fast_max_bytes=140000,
        image_fast_jpeg_quality=68,
        image_primary_model="qwen/qwen3.5-9b",
        image_fallback_model="qwen/qwen3-vl-32b-instruct",
        image_poll_interval_seconds=8,
        image_timeout_seconds=75,
        image_max_edge_px=1600,
        image_max_bytes=650000,
        image_jpeg_quality=82,
    )


def test_client_fetches_and_caches_access_token(tmp_path: Path):
    session = FakeSession()
    client = WechatOfficialClient(config=make_config(tmp_path), session=session)

    first = client.get_access_token()
    second = client.get_access_token()

    assert first == "token-123"
    assert second == "token-123"
    assert len(session.calls) == 1
    assert client.token_cache_path.exists()


def test_client_send_custom_text_posts_json_payload(tmp_path: Path):
    session = FakeSession()
    client = WechatOfficialClient(config=make_config(tmp_path), session=session)

    result = client.send_custom_text(open_id="openid-1", content="hello dgteam")

    assert result["errmsg"] == "ok"
    _, kwargs = session.calls[-1]
    payload = json.loads(kwargs["data"].decode("utf-8"))
    assert payload["touser"] == "openid-1"
    assert payload["text"]["content"] == "hello dgteam"


def test_client_download_media_returns_bytes_and_filename(tmp_path: Path):
    session = FakeSession()
    client = WechatOfficialClient(config=make_config(tmp_path), session=session)

    content, filename, content_type = client.download_media("media-123")

    assert content == b"image-bytes"
    assert filename == "sku.jpg"
    assert content_type == "image/jpeg"


def test_client_download_image_url_uses_response_headers(tmp_path: Path):
    session = FakeSession()
    client = WechatOfficialClient(config=make_config(tmp_path), session=session)

    content, filename, content_type = client.download_image_url("https://example.com/detail.png")

    assert content == b"external-image"
    assert filename.endswith(".png")
    assert content_type == "image/png"
