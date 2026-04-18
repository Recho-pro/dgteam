from __future__ import annotations

import re
import time
from pathlib import Path

from dgteam.core.textio import read_json_utf8, write_json_utf8
from dgteam.integrations.wechat_official.models import WechatOfficialSessionState


SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_-]+")


class WechatOfficialSessionStore:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path_for(self, open_id: str) -> Path:
        safe_name = SAFE_NAME_RE.sub("_", str(open_id or "").strip()) or "anonymous"
        return self.root / f"{safe_name}.json"

    def load(self, open_id: str) -> WechatOfficialSessionState:
        path = self._path_for(open_id)
        if not path.exists():
            return WechatOfficialSessionState(open_id=str(open_id or "").strip(), updated_at=0)
        payload = read_json_utf8(path)
        if not isinstance(payload, dict):
            return WechatOfficialSessionState(open_id=str(open_id or "").strip(), updated_at=0)
        state = WechatOfficialSessionState.from_dict(payload)
        if not state.open_id:
            state.open_id = str(open_id or "").strip()
        return state

    def save(self, state: WechatOfficialSessionState) -> None:
        state.updated_at = int(time.time())
        write_json_utf8(self._path_for(state.open_id), state.to_dict())

    def clear(self, open_id: str) -> None:
        path = self._path_for(open_id)
        if path.exists():
            path.unlink()
