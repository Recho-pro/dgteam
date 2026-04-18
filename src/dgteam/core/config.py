from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .env import load_project_env_values


def _env(name: str, default: str, env_values: dict[str, str]) -> str:
    if name in os.environ:
        return str(os.environ.get(name, default)).strip()
    return str(env_values.get(name, default)).strip()


@dataclass(frozen=True)
class AgentConfig:
    profile_dir: Path
    runs_dir: Path
    releases_dir: Path


@dataclass(frozen=True)
class QueryApiConfig:
    host: str
    port: int


@dataclass(frozen=True)
class PublishApiConfig:
    host: str
    port: int
    shared_token: str
    uploads_dir: Path


@dataclass(frozen=True)
class WechatClawbotConfig:
    enabled: bool
    host: str
    port: int
    bridge_mode: str
    callback_path: str
    shared_secret: str
    corp_id: str
    corp_secret: str
    callback_token: str
    encoding_aes_key: str
    default_open_kfid: str
    api_base_url: str
    inbox_dir: Path
    archive_dir: Path
    state_dir: Path


@dataclass(frozen=True)
class WechatOfficialConfig:
    enabled: bool
    host: str
    port: int
    callback_path: str
    app_id: str
    app_secret: str
    callback_token: str
    encoding_aes_key: str
    api_base_url: str
    state_dir: Path
    image_worker_enabled: bool
    image_api_key: str
    image_fast_model: str
    image_fast_timeout_seconds: int
    image_fast_max_edge_px: int
    image_fast_max_bytes: int
    image_fast_jpeg_quality: int
    image_primary_model: str
    image_fallback_model: str
    image_poll_interval_seconds: float
    image_timeout_seconds: int
    image_max_edge_px: int
    image_max_bytes: int
    image_jpeg_quality: int


@dataclass(frozen=True)
class ReleaseConfig:
    current_dir: Path
    previous_dir: Path
    history_dir: Path
    staging_dir: Path
    state_dir: Path


@dataclass(frozen=True)
class StorageRetentionConfig:
    enabled: bool
    keep_local_releases: int
    keep_local_release_archives: int
    keep_integration_smoke_runs: int
    keep_cloud_releases: int
    keep_cloud_rollbacks: int
    prune_cloud_uploads: bool


@dataclass(frozen=True)
class Settings:
    env: str
    log_level: str
    project_root: Path
    local_root: Path
    cloud_root: Path
    agent: AgentConfig
    query_api: QueryApiConfig
    publish_api: PublishApiConfig
    wechat_clawbot: WechatClawbotConfig
    wechat_official: WechatOfficialConfig
    release: ReleaseConfig
    retention: StorageRetentionConfig


def load_settings(project_root: Path | None = None) -> Settings:
    root = Path(project_root or Path(__file__).resolve().parents[3]).resolve()
    env_values = load_project_env_values(root)
    local_root = (root / _env("DGTEAM_LOCAL_ROOT", "runtime/local", env_values)).resolve()
    cloud_root = (root / _env("DGTEAM_CLOUD_ROOT", "runtime/cloud", env_values)).resolve()

    return Settings(
        env=_env("DGTEAM_ENV", "development", env_values),
        log_level=_env("DGTEAM_LOG_LEVEL", "INFO", env_values),
        project_root=root,
        local_root=local_root,
        cloud_root=cloud_root,
        agent=AgentConfig(
            profile_dir=(root / _env("DGTEAM_AGENT_PROFILE_DIR", "runtime/local/browser_profile", env_values)).resolve(),
            runs_dir=(root / _env("DGTEAM_AGENT_RUNS_DIR", "runtime/local/runs", env_values)).resolve(),
            releases_dir=(root / _env("DGTEAM_AGENT_RELEASES_DIR", "runtime/local/releases", env_values)).resolve(),
        ),
        query_api=QueryApiConfig(
            host=_env("DGTEAM_QUERY_HOST", "127.0.0.1", env_values),
            port=int(_env("DGTEAM_QUERY_PORT", "8765", env_values)),
        ),
        publish_api=PublishApiConfig(
            host=_env("DGTEAM_PUBLISH_HOST", "127.0.0.1", env_values),
            port=int(_env("DGTEAM_PUBLISH_PORT", "8865", env_values)),
            shared_token=_env("DGTEAM_PUBLISH_TOKEN", "", env_values),
            uploads_dir=(root / _env("DGTEAM_PUBLISH_UPLOADS_DIR", "runtime/cloud/uploads", env_values)).resolve(),
        ),
        wechat_clawbot=WechatClawbotConfig(
            enabled=_env("DGTEAM_WECHAT_CLAWBOT_ENABLED", "false", env_values).lower() in {"1", "true", "yes", "on"},
            host=_env("DGTEAM_WECHAT_CLAWBOT_HOST", "127.0.0.1", env_values),
            port=int(_env("DGTEAM_WECHAT_CLAWBOT_PORT", "8965", env_values)),
            bridge_mode=_env("DGTEAM_WECHAT_CLAWBOT_MODE", "wecom_customer_service", env_values),
            callback_path=_env("DGTEAM_WECHAT_CLAWBOT_CALLBACK_PATH", "/wechat/kf/callback", env_values),
            shared_secret=_env("DGTEAM_WECHAT_CLAWBOT_SECRET", "", env_values),
            corp_id=_env("DGTEAM_WECHAT_CLAWBOT_CORP_ID", "", env_values),
            corp_secret=_env("DGTEAM_WECHAT_CLAWBOT_CORP_SECRET", "", env_values),
            callback_token=_env("DGTEAM_WECHAT_CLAWBOT_TOKEN", "", env_values),
            encoding_aes_key=_env("DGTEAM_WECHAT_CLAWBOT_ENCODING_AES_KEY", "", env_values),
            default_open_kfid=_env("DGTEAM_WECHAT_CLAWBOT_OPEN_KFID", "", env_values),
            api_base_url=_env("DGTEAM_WECHAT_CLAWBOT_API_BASE_URL", "https://qyapi.weixin.qq.com", env_values),
            inbox_dir=(root / _env("DGTEAM_WECHAT_CLAWBOT_INBOX_DIR", "runtime/local/wechat_clawbot/inbox", env_values)).resolve(),
            archive_dir=(root / _env("DGTEAM_WECHAT_CLAWBOT_ARCHIVE_DIR", "runtime/local/wechat_clawbot/archive", env_values)).resolve(),
            state_dir=(root / _env("DGTEAM_WECHAT_CLAWBOT_STATE_DIR", "runtime/local/wechat_clawbot/state", env_values)).resolve(),
        ),
        wechat_official=WechatOfficialConfig(
            enabled=_env("DGTEAM_WECHAT_OFFICIAL_ENABLED", "false", env_values).lower() in {"1", "true", "yes", "on"},
            host=_env("DGTEAM_WECHAT_OFFICIAL_HOST", "127.0.0.1", env_values),
            port=int(_env("DGTEAM_WECHAT_OFFICIAL_PORT", "8975", env_values)),
            callback_path=_env("DGTEAM_WECHAT_OFFICIAL_CALLBACK_PATH", "/wechat/official/callback", env_values),
            app_id=_env("DGTEAM_WECHAT_OFFICIAL_APP_ID", "", env_values),
            app_secret=_env("DGTEAM_WECHAT_OFFICIAL_APP_SECRET", "", env_values),
            callback_token=_env("DGTEAM_WECHAT_OFFICIAL_TOKEN", "", env_values),
            encoding_aes_key=_env("DGTEAM_WECHAT_OFFICIAL_ENCODING_AES_KEY", "", env_values),
            api_base_url=_env("DGTEAM_WECHAT_OFFICIAL_API_BASE_URL", "https://api.weixin.qq.com", env_values),
            state_dir=(root / _env("DGTEAM_WECHAT_OFFICIAL_STATE_DIR", "runtime/local/wechat_official/state", env_values)).resolve(),
            image_worker_enabled=_env("DGTEAM_WECHAT_OFFICIAL_IMAGE_WORKER_ENABLED", "false", env_values).lower()
            in {"1", "true", "yes", "on"},
            image_api_key=(
                _env("DGTEAM_WECHAT_OFFICIAL_IMAGE_API_KEY", "", env_values)
                or _env("DGTEAM_OPENROUTER_API_KEY", "", env_values)
                or _env("OPENROUTER_API_KEY", "", env_values)
            ),
            image_fast_model=_env(
                "DGTEAM_WECHAT_OFFICIAL_IMAGE_FAST_MODEL",
                "openai/gpt-4.1-mini",
                env_values,
            ),
            image_fast_timeout_seconds=int(
                _env("DGTEAM_WECHAT_OFFICIAL_IMAGE_FAST_TIMEOUT_SECONDS", "4", env_values)
            ),
            image_fast_max_edge_px=int(
                _env("DGTEAM_WECHAT_OFFICIAL_IMAGE_FAST_MAX_EDGE_PX", "768", env_values)
            ),
            image_fast_max_bytes=int(
                _env("DGTEAM_WECHAT_OFFICIAL_IMAGE_FAST_MAX_BYTES", "140000", env_values)
            ),
            image_fast_jpeg_quality=int(
                _env("DGTEAM_WECHAT_OFFICIAL_IMAGE_FAST_JPEG_QUALITY", "68", env_values)
            ),
            image_primary_model=_env("DGTEAM_WECHAT_OFFICIAL_IMAGE_PRIMARY_MODEL", "qwen/qwen3-vl-32b-instruct", env_values),
            image_fallback_model=_env(
                "DGTEAM_WECHAT_OFFICIAL_IMAGE_FALLBACK_MODEL",
                "qwen/qwen3-vl-235b-a22b-instruct",
                env_values,
            ),
            image_poll_interval_seconds=float(
                _env("DGTEAM_WECHAT_OFFICIAL_IMAGE_POLL_INTERVAL_SECONDS", "2", env_values)
            ),
            image_timeout_seconds=int(_env("DGTEAM_WECHAT_OFFICIAL_IMAGE_TIMEOUT_SECONDS", "45", env_values)),
            image_max_edge_px=int(_env("DGTEAM_WECHAT_OFFICIAL_IMAGE_MAX_EDGE_PX", "832", env_values)),
            image_max_bytes=int(_env("DGTEAM_WECHAT_OFFICIAL_IMAGE_MAX_BYTES", "200000", env_values)),
            image_jpeg_quality=int(_env("DGTEAM_WECHAT_OFFICIAL_IMAGE_JPEG_QUALITY", "66", env_values)),
        ),
        release=ReleaseConfig(
            current_dir=(root / _env("DGTEAM_RELEASE_CURRENT", "runtime/cloud/current", env_values)).resolve(),
            previous_dir=(root / _env("DGTEAM_RELEASE_PREVIOUS", "runtime/cloud/previous", env_values)).resolve(),
            history_dir=(root / _env("DGTEAM_RELEASE_HISTORY", "runtime/cloud/releases", env_values)).resolve(),
            staging_dir=(root / _env("DGTEAM_RELEASE_STAGING", "runtime/cloud/staging", env_values)).resolve(),
            state_dir=(root / _env("DGTEAM_RELEASE_STATE_DIR", "runtime/cloud/deployments", env_values)).resolve(),
        ),
        retention=StorageRetentionConfig(
            enabled=_env("DGTEAM_RETENTION_ENABLED", "true", env_values).lower() in {"1", "true", "yes", "on"},
            keep_local_releases=int(_env("DGTEAM_KEEP_LOCAL_RELEASES", "1", env_values)),
            keep_local_release_archives=int(_env("DGTEAM_KEEP_LOCAL_RELEASE_ARCHIVES", "0", env_values)),
            keep_integration_smoke_runs=int(_env("DGTEAM_KEEP_INTEGRATION_SMOKE_RUNS", "1", env_values)),
            keep_cloud_releases=int(_env("DGTEAM_KEEP_CLOUD_RELEASES", "0", env_values)),
            keep_cloud_rollbacks=int(_env("DGTEAM_KEEP_CLOUD_ROLLBACKS", "1", env_values)),
            prune_cloud_uploads=_env("DGTEAM_PRUNE_CLOUD_UPLOADS", "true", env_values).lower() in {"1", "true", "yes", "on"},
        ),
    )
