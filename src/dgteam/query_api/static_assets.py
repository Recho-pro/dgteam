from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
WEB_DIR = PROJECT_ROOT / "web" / "query_ui"


def utf8_content_type(content_type: str) -> str:
    text = str(content_type or "").strip()
    if not text:
        return "application/octet-stream"
    lowered = text.lower()
    if "charset=" in lowered:
        return text
    if lowered.startswith("text/html") or lowered.startswith("text/css") or lowered in {
        "application/javascript",
        "text/javascript",
        "application/x-javascript",
    }:
        return f"{text}; charset=utf-8"
    return text


def cache_headers_for_static_path(request_path: str) -> dict[str, str]:
    normalized = str(request_path or "/")
    if normalized.startswith("/api/"):
        return {}
    if normalized in {"", "/", "/index.html"}:
        return {
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        }
    if normalized.endswith(".js") or normalized.endswith(".css"):
        return {"Cache-Control": "public, max-age=31536000, immutable"}
    return {}
