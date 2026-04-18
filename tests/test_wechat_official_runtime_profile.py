from __future__ import annotations

from pathlib import Path

from dgteam.core.config import WechatOfficialConfig
from dgteam.integrations.wechat_official.image_runtime import WechatOfficialImageRuntimeProfile
from dgteam.integrations.wechat_official.service import WechatOfficialService


class StubQueryService:
    def status_payload(self) -> dict[str, object]:
        return {"ok": True, "run_key": "stub-run"}


def make_config(tmp_path: Path, *, image_api_key: str = "", image_worker_enabled: bool = False) -> WechatOfficialConfig:
    return WechatOfficialConfig(
        enabled=True,
        host="127.0.0.1",
        port=8975,
        callback_path="/wechat/official/callback",
        app_id="wx-test-app",
        app_secret="secret-test",
        callback_token="callback-token",
        encoding_aes_key="abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG",
        api_base_url="https://api.weixin.qq.com",
        state_dir=tmp_path / "official_state",
        image_worker_enabled=image_worker_enabled,
        image_api_key=image_api_key,
        image_fast_model="openai/gpt-4.1-mini",
        image_fast_timeout_seconds=4,
        image_fast_max_edge_px=768,
        image_fast_max_bytes=140000,
        image_fast_jpeg_quality=68,
        image_primary_model="qwen/qwen3-vl-32b-instruct",
        image_fallback_model="qwen/qwen3-vl-235b-a22b-instruct",
        image_poll_interval_seconds=2.0,
        image_timeout_seconds=45,
        image_max_edge_px=832,
        image_max_bytes=200000,
        image_jpeg_quality=66,
    )


def test_runtime_profile_guards_worker_when_api_key_is_missing(tmp_path: Path):
    profile = WechatOfficialImageRuntimeProfile.from_config(
        make_config(tmp_path, image_worker_enabled=True, image_api_key="")
    )

    assert profile.fast_lane_enabled is False
    assert profile.worker_enabled is False
    assert profile.fast_lane_reason == "missing_api_key"
    assert profile.worker_mode == "guarded_off"
    assert profile.worker_reason == "missing_api_key"


def test_runtime_profile_allows_fast_lane_without_enabling_worker(tmp_path: Path):
    profile = WechatOfficialImageRuntimeProfile.from_config(
        make_config(tmp_path, image_worker_enabled=False, image_api_key="test-openrouter-key")
    )

    assert profile.fast_lane_enabled is True
    assert profile.worker_enabled is False
    assert profile.worker_mode == "manual_opt_in"
    assert profile.worker_reason == "image_worker_enabled=false"


def test_service_health_payload_reports_runtime_truth(tmp_path: Path):
    service = WechatOfficialService(
        config=make_config(tmp_path, image_worker_enabled=True, image_api_key=""),
        query_service=StubQueryService(),
    )

    health = service.health_payload()

    assert service.fast_lane is None
    assert health["capabilities"]["image_fast_lane"] is False
    assert health["capabilities"]["image_worker"] is False
    assert health["image_runtime"]["fast_lane_reason"] == "missing_api_key"
    assert health["image_runtime"]["worker_reason"] == "missing_api_key"
    assert health["image_models"]["worker_requested"] is True
    assert health["image_models"]["worker_enabled"] is False
