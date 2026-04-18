from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from dgteam.core.textio import ensure_parent_dir, read_json_utf8, write_json_utf8, write_text_utf8


def deployment_timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H-%M-%S")


def deployment_id_for_release(release_id: str, *, prefix: str = "deploy") -> str:
    cleaned = str(release_id or "").strip().replace(" ", "_")
    slug = cleaned or "release"
    return f"{prefix}_{slug}_{deployment_timestamp()}"


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class DeploymentJournal:
    def __init__(self, root: Path, deployment_id: str):
        self.root = Path(root).expanduser().resolve()
        self.deployment_id = str(deployment_id or "").strip() or deployment_id_for_release("", prefix="deploy")
        self.run_dir = self.root / self.deployment_id
        self.status_path = self.run_dir / "status.json"
        self.events_path = self.run_dir / "events.jsonl"
        self.run_dir.mkdir(parents=True, exist_ok=True)

    def initialize(self, *, role: str, release_id: str = "", metadata: Dict[str, Any] | None = None) -> Dict[str, Any]:
        payload = {
            "deployment_id": self.deployment_id,
            "role": str(role or "").strip(),
            "release_id": str(release_id or "").strip(),
            "status": "created",
            "step": "created",
            "ok": False,
            "started_at": now_text(),
            "updated_at": now_text(),
            "finished_at": "",
            "metadata": dict(metadata or {}),
            "last_error": {},
        }
        write_json_utf8(self.status_path, payload)
        return payload

    def load(self) -> Dict[str, Any]:
        if not self.status_path.exists():
            return {}
        loaded = read_json_utf8(self.status_path)
        return dict(loaded) if isinstance(loaded, dict) else {}

    def update(
        self,
        *,
        status: str | None = None,
        step: str | None = None,
        ok: bool | None = None,
        finished: bool = False,
        last_error: Dict[str, Any] | None = None,
        metadata: Dict[str, Any] | None = None,
        **extra: Any,
    ) -> Dict[str, Any]:
        payload = self.load()
        if not payload:
            payload = self.initialize(role=str(extra.pop("role", "unspecified") or "unspecified"))
        if status is not None:
            payload["status"] = str(status or "").strip()
        if step is not None:
            payload["step"] = str(step or "").strip()
        if ok is not None:
            payload["ok"] = bool(ok)
        if last_error is not None:
            payload["last_error"] = dict(last_error)
        if metadata:
            merged_metadata = dict(payload.get("metadata") or {})
            merged_metadata.update(dict(metadata))
            payload["metadata"] = merged_metadata
        for key, value in extra.items():
            payload[key] = value
        payload["updated_at"] = now_text()
        if finished:
            payload["finished_at"] = payload["updated_at"]
        write_json_utf8(self.status_path, payload)
        return payload

    def event(self, event_type: str, *, message: str = "", level: str = "info", **details: Any) -> Dict[str, Any]:
        payload = {
            "time": now_text(),
            "event": str(event_type or "").strip(),
            "level": str(level or "info").strip(),
            "message": str(message or "").strip(),
            "details": dict(details),
        }
        ensure_parent_dir(self.events_path)
        with self.events_path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return payload

    def note_paths(self) -> Dict[str, str]:
        return {
            "run_dir": str(self.run_dir),
            "status_path": str(self.status_path),
            "events_path": str(self.events_path),
        }

