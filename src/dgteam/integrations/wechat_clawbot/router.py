from __future__ import annotations

from dgteam.integrations.wechat_clawbot.commands import ClawbotCommand, ClawbotCommandResult


class ClawbotCommandRouter:
    """Command routing boundary for future WeChat bot control flows."""

    def route(self, command: ClawbotCommand) -> ClawbotCommandResult:
        normalized = command.command.strip().lower()
        if normalized in {"status", "health"}:
            return ClawbotCommandResult(
                ok=True,
                command=normalized,
                handled=True,
                message="DGTEAM clawbot bridge is online.",
            )
        return ClawbotCommandResult(
            ok=True,
            command=normalized,
            handled=False,
            message="Command route is reserved but not wired yet.",
        )
