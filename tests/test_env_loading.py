from __future__ import annotations

from pathlib import Path

from dgteam.core.env import ensure_project_env_loaded
from dgteam.core.config import load_settings
from dgteam.core.project_config import load_project_config


def test_load_settings_reads_project_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("DGTEAM_WECHAT_OFFICIAL_APP_ID", raising=False)
    monkeypatch.delenv("DGTEAM_WECHAT_OFFICIAL_APP_SECRET", raising=False)
    monkeypatch.delenv("DGTEAM_DB_PATH", raising=False)

    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "DGTEAM_WECHAT_OFFICIAL_APP_ID=test-app-id",
                "DGTEAM_WECHAT_OFFICIAL_APP_SECRET=test-app-secret",
                "DGTEAM_WECHAT_OFFICIAL_IMAGE_API_KEY=test-openrouter-key",
                "DGTEAM_WECHAT_OFFICIAL_IMAGE_PRIMARY_MODEL=qwen/qwen3.5-9b",
                "DGTEAM_WECHAT_OFFICIAL_IMAGE_FALLBACK_MODEL=qwen/qwen3-vl-32b-instruct",
                "DGTEAM_WECHAT_OFFICIAL_IMAGE_POLL_INTERVAL_SECONDS=0.5",
                "DGTEAM_WECHAT_OFFICIAL_IMAGE_TIMEOUT_SECONDS=77",
                "DGTEAM_WECHAT_OFFICIAL_IMAGE_MAX_EDGE_PX=1440",
                "DGTEAM_WECHAT_OFFICIAL_IMAGE_MAX_BYTES=555000",
                "DGTEAM_WECHAT_OFFICIAL_IMAGE_JPEG_QUALITY=79",
                "DGTEAM_DB_PATH=runtime/local/data/custom.db",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    settings = load_settings(tmp_path)
    project = load_project_config(tmp_path)

    assert settings.wechat_official.app_id == "test-app-id"
    assert settings.wechat_official.app_secret == "test-app-secret"
    assert settings.wechat_official.image_api_key == "test-openrouter-key"
    assert settings.wechat_official.image_primary_model == "qwen/qwen3.5-9b"
    assert settings.wechat_official.image_fallback_model == "qwen/qwen3-vl-32b-instruct"
    assert settings.wechat_official.image_poll_interval_seconds == 0.5
    assert settings.wechat_official.image_timeout_seconds == 77
    assert settings.wechat_official.image_max_edge_px == 1440
    assert settings.wechat_official.image_max_bytes == 555000
    assert settings.wechat_official.image_jpeg_quality == 79
    assert project.paths.db_path == (tmp_path / "runtime" / "local" / "data" / "custom.db").resolve()


def test_project_env_loading_does_not_leak_between_roots(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("DGTEAM_DB_PATH", raising=False)
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    (root_a / ".env").write_text("DGTEAM_DB_PATH=runtime/local/data/custom.db\n", encoding="utf-8")

    ensure_project_env_loaded(root_a)
    config_a = load_project_config(root_a)
    config_b = load_project_config(root_b)

    assert config_a.paths.db_path == (root_a / "runtime" / "local" / "data" / "custom.db").resolve()
    assert config_b.paths.db_path == (root_b / "runtime" / "local" / "data" / "dgteam.db").resolve()
