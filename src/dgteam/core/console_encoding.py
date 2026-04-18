from __future__ import annotations

import os
import sys
from typing import Any


def _reconfigure_stream(stream: Any) -> None:
    reconfigure = getattr(stream, "reconfigure", None)
    if not callable(reconfigure):
        return
    try:
        reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        return


def configure_utf8_stdio() -> None:
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONLEGACYWINDOWSSTDIO", "0")

    for stream in (sys.stdin, sys.stdout, sys.stderr):
        _reconfigure_stream(stream)
