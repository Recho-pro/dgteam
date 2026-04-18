from __future__ import annotations

import base64
import json
import mimetypes
import urllib.error
import urllib.request
from typing import Any, Mapping, Optional, Sequence


OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_APP_TITLE = "DGTEAM WeChat Official Image Worker"
OPENROUTER_APP_REFERER = "https://dgtdnb.com"


def openrouter_json_schema(name: str, schema: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": True,
            "schema": dict(schema),
        },
    }


def openrouter_image_to_data_url(image_name: str, image_bytes: bytes) -> str:
    mime_type = mimetypes.guess_type(image_name)[0] or "image/png"
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def openrouter_chat_json(
    *,
    api_key: str,
    model: str,
    messages: Sequence[Mapping[str, Any]],
    response_format: Optional[Mapping[str, Any]] = None,
    timeout: int = 90,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "temperature": 0,
        "messages": list(messages),
    }
    if response_format:
        payload["response_format"] = dict(response_format)

    request = urllib.request.Request(
        OPENROUTER_API_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": OPENROUTER_APP_REFERER,
            "X-Title": OPENROUTER_APP_TITLE,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"OpenRouter HTTP {exc.code}: {raw}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"OpenRouter request failed: {exc}") from exc

    try:
        completion = json.loads(body)
    except Exception as exc:
        raise RuntimeError(f"OpenRouter returned non-JSON response: {body[:240]}") from exc

    choices = completion.get("choices") or []
    if not choices:
        raise RuntimeError(f"OpenRouter returned no choices: {completion}")

    content = choices[0].get("message", {}).get("content")
    text = _extract_message_text(content).strip()
    if not text:
        raise RuntimeError(f"OpenRouter returned empty content: {completion}")

    try:
        return json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except Exception:
                pass
        raise RuntimeError(f"OpenRouter did not return valid JSON: {text[:280]}")


def _extract_message_text(message_content: Any) -> str:
    if isinstance(message_content, str):
        return message_content
    if isinstance(message_content, list):
        parts: list[str] = []
        for item in message_content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
        return "\n".join(part for part in parts if part)
    return str(message_content or "")
