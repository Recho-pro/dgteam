from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ClawbotCommand:
    command: str
    text: str
    sender: str
    room: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ClawbotCommandResult:
    ok: bool
    command: str
    handled: bool
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
