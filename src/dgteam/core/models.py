from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ReleaseManifest:
    release_id: str
    run_key: str
    published_at: str
    quote_count: int
    snapshot_count: int
    rule_version: str
    build_version: str
    source_machine: str
    files: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class HealthReport:
    ok: bool
    service: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
