from __future__ import annotations

from dataclasses import dataclass

from dgteam.core.config import WechatOfficialConfig


@dataclass(frozen=True, slots=True)
class WechatOfficialImageRuntimeProfile:
    worker_requested: bool
    has_api_key: bool
    fast_lane_enabled: bool
    worker_enabled: bool
    fast_lane_reason: str
    worker_mode: str
    worker_reason: str
    fast_lane_model: str
    worker_primary_model: str
    worker_fallback_model: str

    @classmethod
    def from_config(cls, config: WechatOfficialConfig) -> "WechatOfficialImageRuntimeProfile":
        worker_requested = bool(config.image_worker_enabled)
        has_api_key = bool(str(config.image_api_key or "").strip())
        fast_lane_model = str(config.image_fast_model or "").strip()
        worker_primary_model = str(config.image_primary_model or "").strip()
        worker_fallback_model = str(config.image_fallback_model or "").strip()

        fast_lane_enabled = has_api_key and bool(fast_lane_model)
        worker_enabled = worker_requested and has_api_key and bool(worker_primary_model)

        if fast_lane_enabled:
            fast_lane_reason = "enabled"
        elif not has_api_key:
            fast_lane_reason = "missing_api_key"
        else:
            fast_lane_reason = "missing_fast_model"

        if worker_enabled:
            worker_mode = "enabled"
            worker_reason = "enabled"
        elif not worker_requested:
            worker_mode = "manual_opt_in"
            worker_reason = "image_worker_enabled=false"
        elif not has_api_key:
            worker_mode = "guarded_off"
            worker_reason = "missing_api_key"
        else:
            worker_mode = "guarded_off"
            worker_reason = "missing_primary_model"

        return cls(
            worker_requested=worker_requested,
            has_api_key=has_api_key,
            fast_lane_enabled=fast_lane_enabled,
            worker_enabled=worker_enabled,
            fast_lane_reason=fast_lane_reason,
            worker_mode=worker_mode,
            worker_reason=worker_reason,
            fast_lane_model=fast_lane_model,
            worker_primary_model=worker_primary_model,
            worker_fallback_model=worker_fallback_model,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "worker_requested": self.worker_requested,
            "has_api_key": self.has_api_key,
            "fast_lane_enabled": self.fast_lane_enabled,
            "worker_enabled": self.worker_enabled,
            "fast_lane_reason": self.fast_lane_reason,
            "worker_mode": self.worker_mode,
            "worker_reason": self.worker_reason,
            "fast_lane_model": self.fast_lane_model,
            "worker_primary_model": self.worker_primary_model,
            "worker_fallback_model": self.worker_fallback_model,
        }
