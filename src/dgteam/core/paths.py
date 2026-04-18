from __future__ import annotations

from pathlib import Path

from dgteam.core.config import Settings


def ensure_runtime_dirs(settings: Settings) -> None:
    required_paths = [
        settings.local_root,
        settings.cloud_root,
        settings.agent.profile_dir,
        settings.agent.runs_dir,
        settings.agent.releases_dir,
        settings.publish_api.uploads_dir,
        settings.wechat_clawbot.inbox_dir,
        settings.wechat_clawbot.archive_dir,
        settings.wechat_clawbot.state_dir,
        settings.wechat_official.state_dir,
        settings.release.current_dir,
        settings.release.previous_dir,
        settings.release.history_dir,
        settings.release.staging_dir,
        settings.release.state_dir,
    ]
    for path in required_paths:
        Path(path).mkdir(parents=True, exist_ok=True)
