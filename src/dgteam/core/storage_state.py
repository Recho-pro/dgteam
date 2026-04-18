from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional


def get_app_state(storage: Any, state_key: str, default: str = "") -> str:
    with storage.connect() as conn:
        row = conn.execute(
            "SELECT state_value FROM app_state WHERE state_key = ?",
            (str(state_key or "").strip(),),
        ).fetchone()
    return str(row["state_value"] or default) if row else str(default)


def set_app_state(storage: Any, state_key: str, state_value: Any, *, updated_at: Optional[str] = None) -> None:
    timestamp = str(updated_at or time.strftime("%Y-%m-%d %H:%M:%S"))
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO app_state (state_key, state_value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(state_key) DO UPDATE SET
                state_value = excluded.state_value,
                updated_at = excluded.updated_at
            """,
            (
                str(state_key or "").strip(),
                str(state_value or ""),
                timestamp,
            ),
        )


def get_live_market_state(storage: Any) -> Dict[str, Any]:
    run_key = get_app_state(storage, "live_market_run_key", "")
    published_at = get_app_state(storage, "live_market_published_at", "")
    summary_json = get_app_state(storage, "live_market_summary_json", "")
    summary: Dict[str, Any] = {}
    if summary_json:
        try:
            parsed = json.loads(summary_json)
            if isinstance(parsed, dict):
                summary = parsed
        except Exception:
            summary = {}
    return {
        "run_key": run_key,
        "published_at": published_at,
        "summary": summary,
    }
