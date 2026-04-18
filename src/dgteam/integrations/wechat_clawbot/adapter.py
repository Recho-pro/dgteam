from __future__ import annotations

from abc import ABC, abstractmethod

from dgteam.integrations.wechat_clawbot.models import ClawbotEvent


class WechatClawbotAdapter(ABC):
    """Abstract adapter so the real clawbot transport can be plugged in later."""

    @abstractmethod
    def normalize_event(self, payload: dict) -> ClawbotEvent:
        raise NotImplementedError

    @abstractmethod
    def explain_capabilities(self) -> dict[str, object]:
        raise NotImplementedError


class DefaultWechatClawbotAdapter(WechatClawbotAdapter):
    def normalize_event(self, payload: dict) -> ClawbotEvent:
        from dgteam.integrations.wechat_clawbot.models import build_event_from_payload

        return build_event_from_payload(payload)

    def explain_capabilities(self) -> dict[str, object]:
        return {
            "webhook_ingest": True,
            "event_archive": True,
            "command_routing": True,
            "active_push": False,
        }
