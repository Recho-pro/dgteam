from __future__ import annotations

import base64
import json
import mimetypes
import re
import urllib.error
import urllib.request
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from dgteam.core.storage import DGTeamStorage, normalize_search_text, parse_price_int


OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_OPENROUTER_MODEL = "google/gemini-3.1-pro-preview"
APP_TITLE = "DG Team Market Importer"
APP_REFERER = "https://localhost/dg-market-import"
MEMORY_SPEC_RE = re.compile(r"(\d{1,2})\s*\+\s*(\d{1,4})(?:\s*(?:g|gb|t|tb))?", re.IGNORECASE)


def _json_schema(name: str, schema: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": True,
            "schema": dict(schema),
        },
    }


def _extract_message_text(message_content: Any) -> str:
    if isinstance(message_content, str):
        return message_content
    if isinstance(message_content, list):
        parts: List[str] = []
        for item in message_content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
        return "\n".join(part for part in parts if part)
    return str(message_content or "")


def _validate_openrouter_key_format(api_key: str) -> tuple[bool, str]:
    cleaned = str(api_key or "").strip()
    if not cleaned:
        return False, "OpenRouter API key is required."
    if any(ch.isspace() for ch in cleaned):
        return False, "OpenRouter API key cannot contain whitespace."
    if len(cleaned) < 16:
        return False, "OpenRouter API key looks too short."
    return True, "OpenRouter API key format looks valid."


def _openrouter_chat(
    *,
    api_key: str,
    model: str,
    messages: Sequence[Mapping[str, Any]],
    response_format: Optional[Mapping[str, Any]] = None,
    timeout: int = 90,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
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
            "HTTP-Referer": APP_REFERER,
            "X-Title": APP_TITLE,
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
        payload = json.loads(body)
    except Exception as exc:
        raise RuntimeError(f"OpenRouter returned non-JSON response: {body[:240]}") from exc

    choices = payload.get("choices") or []
    if not choices:
        raise RuntimeError(f"OpenRouter returned no choices: {payload}")

    content = choices[0].get("message", {}).get("content")
    text = _extract_message_text(content).strip()
    if not text:
        raise RuntimeError(f"OpenRouter returned empty content: {payload}")

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


def validate_openrouter_api_key(
    api_key: str,
    *,
    model: str = DEFAULT_OPENROUTER_MODEL,
    probe: bool = False,
) -> Dict[str, Any]:
    local_ok, local_message = _validate_openrouter_key_format(api_key)
    if not local_ok:
        return {
            "ok": False,
            "message": local_message,
            "model": model,
            "validation_mode": "local",
            "remote_checked": False,
        }
    if not probe:
        return {
            "ok": True,
            "message": local_message,
            "model": model,
            "validation_mode": "local",
            "remote_checked": False,
        }

    schema = {
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "message": {"type": "string"},
        },
        "required": ["ok", "message"],
        "additionalProperties": False,
    }
    payload = _openrouter_chat(
        api_key=str(api_key or "").strip(),
        model=model,
        messages=[
            {
                "role": "system",
                "content": "You are validating API connectivity. Return a tiny JSON object only.",
            },
            {
                "role": "user",
                "content": "Return {\"ok\":true,\"message\":\"pong\"}.",
            },
        ],
        response_format=_json_schema("validation_ping", schema),
        timeout=45,
    )
    return {
        "ok": bool(payload.get("ok")),
        "message": str(payload.get("message") or ""),
        "model": model,
        "validation_mode": "remote",
        "remote_checked": True,
    }


def image_bytes_to_data_url(image_name: str, image_bytes: bytes) -> str:
    mime_type = mimetypes.guess_type(image_name)[0] or "image/png"
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def reference_core_signature(text: Any) -> str:
    raw = str(text or "").strip().lower()
    if not raw:
        return ""
    normalized_raw = (
        raw.replace("＋", "+")
        .replace("﹢", "+")
        .replace("｜", "")
        .replace("|", "")
        .replace("／", "/")
        .replace("gb", "g")
        .replace("tb", "t")
    )
    match = MEMORY_SPEC_RE.search(normalized_raw)
    if not match:
        return normalize_search_text(text)
    prefix = normalize_search_text(normalized_raw[: match.start()])
    ram = str(match.group(1) or "")
    rom = str(match.group(2) or "")
    return f"{prefix}{ram}{rom}"


def reference_model_signature(text: Any) -> str:
    raw = str(text or "").strip().lower()
    if not raw:
        return ""
    normalized_raw = raw.replace("＋", "+").replace("﹢", "+").replace("／", "/")
    match = MEMORY_SPEC_RE.search(normalized_raw)
    if not match:
        return normalize_search_text(text)
    return normalize_search_text(normalized_raw[: match.start()])


def extract_reference_rows_from_image(
    *,
    api_key: str,
    image_name: str,
    image_bytes: bytes,
    model: str = DEFAULT_OPENROUTER_MODEL,
) -> Dict[str, Any]:
    schema = {
        "type": "object",
        "properties": {
            "source_title": {"type": "string"},
            "rows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "raw_title": {"type": "string"},
                        "reference_price": {"type": "integer"},
                        "ocr_confidence": {
                            "type": "string",
                            "enum": ["high", "medium", "low"],
                        },
                    },
                    "required": ["raw_title", "reference_price", "ocr_confidence"],
                    "additionalProperties": False,
                },
            },
            "warnings": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": ["source_title", "rows", "warnings"],
        "additionalProperties": False,
    }

    prompt = (
        "你是一名非常谨慎的中文表格识别助手。\n"
        "现在给你一张行情表图片，请只提取你能够清楚识别的型号和价格。\n"
        "规则如下：\n"
        "1. 只提取真正看得清的行，不要猜。\n"
        "2. 每一行必须输出 raw_title 和整数价格 reference_price。\n"
        "3. 如果图片上方有明显标题、档口名或日期，把它写到 source_title。\n"
        "4. 价格只保留纯数字，不带货币符号。\n"
        "5. 看不清的行直接忽略。\n"
        "6. ocr_confidence 只能填 high / medium / low。\n"
        "7. 不要合并两行，也不要补全你不确定的内容。"
    )

    messages = [
        {"role": "system", "content": "You extract structured rows from Chinese price sheet images."},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image_bytes_to_data_url(image_name, image_bytes)}},
            ],
        },
    ]

    try:
        payload = _openrouter_chat(
            api_key=str(api_key or "").strip(),
            model=model,
            messages=messages,
            response_format=_json_schema("price_sheet_parse", schema),
            timeout=180,
        )
    except RuntimeError:
        payload = _openrouter_chat(
            api_key=str(api_key or "").strip(),
            model=model,
            messages=[
                {"role": "system", "content": "Return JSON only. Extract structured rows from the image carefully."},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": prompt
                            + "\n请只输出 JSON：{source_title:string, rows:[{raw_title:string, reference_price:int, ocr_confidence:'high|medium|low'}], warnings:string[]}",
                        },
                        {"type": "image_url", "image_url": {"url": image_bytes_to_data_url(image_name, image_bytes)}},
                    ],
                },
            ],
            timeout=180,
        )

    rows: List[Dict[str, Any]] = []
    for item in list(payload.get("rows") or []):
        raw_title = str(item.get("raw_title") or "").strip()
        reference_price = parse_price_int(item.get("reference_price"))
        if not raw_title or reference_price is None:
            continue
        rows.append(
            {
                "raw_title": raw_title,
                "reference_price": reference_price,
                "ocr_confidence": str(item.get("ocr_confidence") or "medium").strip().lower(),
            }
        )
    return {
        "source_title": str(payload.get("source_title") or "").strip(),
        "warnings": [str(item or "").strip() for item in list(payload.get("warnings") or []) if str(item or "").strip()],
        "rows": rows,
    }


def score_candidate_match(query: str, candidate: Mapping[str, Any]) -> float:
    query_norm = normalize_search_text(query)
    if not query_norm:
        return -1
    full_text = str(candidate.get("search_text_normalized") or "")
    model_group = str(candidate.get("model_group_normalized") or "")
    candidate_title = " ".join(
        part for part in (candidate.get("model_title"), candidate.get("group_title")) if str(part or "").strip()
    )
    query_core = reference_core_signature(query)
    candidate_core = reference_core_signature(candidate_title)
    query_model = reference_model_signature(query)
    candidate_model = normalize_search_text(candidate.get("model_title"))
    score = 0.0

    if query_core and candidate_core:
        if candidate_core == query_core:
            score += 2600
        elif candidate_core.startswith(query_core) or query_core.startswith(candidate_core):
            score += 1900

    if query_model and candidate_model:
        if candidate_model == query_model:
            score += 700
        elif candidate_model in query_model or query_model in candidate_model:
            score += 320

    if model_group == query_norm:
        score += 3400
    if full_text == query_norm:
        score += 3000
    if model_group.startswith(query_norm):
        score += 2200
    elif query_norm in model_group:
        score += 1800
    elif full_text.startswith(query_norm):
        score += 1500
    elif query_norm in full_text:
        score += 1200

    tokens = [normalize_search_text(part) for part in str(query or "").replace("/", " ").split() if part.strip()]
    tokens = [token for token in tokens if token and token != query_norm]
    for token in tokens:
        if token in model_group:
            score += 320
        elif token in full_text:
            score += 200
        else:
            score -= 120

    score += min(int(candidate.get("row_count") or 0), 200) * 0.2
    return score


def match_reference_rows_to_candidates(
    rows: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
    *,
    run_key: str,
    threshold: float = 1900,
) -> List[Dict[str, Any]]:
    quote_candidates = [item for item in candidates if str(item.get("data_source") or "quote_rows") == "quote_rows"]
    matched_rows: List[Dict[str, Any]] = []
    for row in rows:
        raw_title = str(row.get("raw_title") or "").strip()
        normalized_title = normalize_search_text(raw_title)
        query_core = reference_core_signature(raw_title)
        query_model = reference_model_signature(raw_title)
        ranked: List[Tuple[float, Mapping[str, Any]]] = []
        for candidate in quote_candidates:
            score = score_candidate_match(raw_title, candidate)
            if score > 0:
                ranked.append((score, candidate))
        ranked.sort(key=lambda item: item[0], reverse=True)

        best_score = ranked[0][0] if ranked else 0
        second_score = ranked[1][0] if len(ranked) > 1 else 0
        best_candidate = ranked[0][1] if ranked else {}
        best_candidate_title = " ".join(
            part for part in (best_candidate.get("model_title"), best_candidate.get("group_title")) if str(part or "").strip()
        )
        best_candidate_core = reference_core_signature(best_candidate_title)
        best_candidate_model = normalize_search_text(best_candidate.get("model_title"))
        confident_match = bool(best_candidate) and best_score >= threshold and (best_score - second_score >= 120 or second_score == 0)

        same_family_top = []
        if best_candidate:
            for score, candidate in ranked:
                if score != best_score:
                    break
                if (
                    str(candidate.get("brand_title") or "") == str(best_candidate.get("brand_title") or "")
                    and str(candidate.get("series_title") or "") == str(best_candidate.get("series_title") or "")
                    and str(candidate.get("model_title") or "") == str(best_candidate.get("model_title") or "")
                ):
                    same_family_top.append(candidate)

        model_level_match = (
            not confident_match
            and bool(best_candidate)
            and best_score >= threshold
            and (
                len(same_family_top) >= 2
                or (
                    bool(query_core)
                    and best_candidate_core == query_core
                    and bool(query_model)
                    and best_candidate_model == query_model
                )
            )
        )

        matched = confident_match or model_level_match
        best_candidate_payload = {
            "brand_title": str(best_candidate.get("brand_title") or ""),
            "series_title": str(best_candidate.get("series_title") or ""),
            "model_title": str(best_candidate.get("model_title") or ""),
            "group_title": str(best_candidate.get("group_title") or ""),
            "condition_bucket": str(best_candidate.get("condition_bucket") or ""),
            "run_key": str(best_candidate.get("run_key") or ""),
            "data_source": str(best_candidate.get("data_source") or ""),
            "external_key": str(best_candidate.get("external_key") or ""),
            "external_title": str(best_candidate.get("external_title") or ""),
            "external_source_title": str(best_candidate.get("external_source_title") or ""),
            "match_score": round(best_score, 3) if best_candidate else 0,
        }
        match_level = "exact" if confident_match else ("model_only" if model_level_match else "unmatched")
        matched_rows.append(
            {
                "raw_title": raw_title,
                "normalized_title": normalized_title,
                "reference_price": int(row.get("reference_price") or 0),
                "ocr_confidence": str(row.get("ocr_confidence") or ""),
                "matched_run_key": run_key if matched else "",
                "matched_brand_title": str(best_candidate.get("brand_title") or "") if matched else "",
                "matched_series_title": str(best_candidate.get("series_title") or "") if matched else "",
                "matched_model_title": str(best_candidate.get("model_title") or "") if matched else "",
                "matched_group_title": str(best_candidate.get("group_title") or "") if confident_match else "",
                "matched_condition_bucket": str(best_candidate.get("condition_bucket") or "") if matched else "",
                "match_score": round(best_score, 3) if matched else 0,
                "match_level": match_level,
                "candidate_kind": "matched_reference_price" if matched else "external_catalog_candidate",
                "match_status": "matched" if matched else "unmatched",
                "best_candidate": best_candidate_payload,
            }
        )
    return matched_rows


def _flatten_preview_rows(preview_payload: Mapping[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    top_level_rows = list(preview_payload.get("rows") or [])
    if top_level_rows:
        for item in top_level_rows:
            rows.append(dict(item))
        return rows

    for image in list(preview_payload.get("images") or []):
        for item in list(image.get("rows") or []):
            rows.append(dict(item))
    return rows


def _compact_preview_summary(preview_payload: Mapping[str, Any]) -> Dict[str, Any]:
    images: List[Dict[str, Any]] = []
    for image in list(preview_payload.get("images") or []):
        images.append(
            {
                "image_name": str(image.get("image_name") or ""),
                "source_title": str(image.get("source_title") or ""),
                "row_count": int(image.get("row_count") or 0),
                "matched_reference_count": int(image.get("matched_reference_count") or 0),
                "external_catalog_count": int(image.get("external_catalog_count") or 0),
                "warnings": [str(item or "").strip() for item in list(image.get("warnings") or []) if str(item or "").strip()],
            }
        )

    return {
        "preview_id": str(preview_payload.get("preview_id") or ""),
        "run_key": str(preview_payload.get("run_key") or ""),
        "ai_model": str(preview_payload.get("ai_model") or ""),
        "source_hint": str(preview_payload.get("source_hint") or ""),
        "image_count": int(preview_payload.get("image_count") or 0),
        "row_count": int(preview_payload.get("row_count") or 0),
        "matched_reference_count": int(preview_payload.get("matched_reference_count") or 0),
        "external_catalog_count": int(preview_payload.get("external_catalog_count") or 0),
        "images": images,
    }


def preview_reference_import(
    *,
    storage: DGTeamStorage,
    run_key: str,
    api_key: str,
    images: Sequence[Mapping[str, Any]],
    model: str = DEFAULT_OPENROUTER_MODEL,
    source_hint: str = "",
) -> Dict[str, Any]:
    candidates = storage.list_sku_candidates(run_key)
    image_reports: List[Dict[str, Any]] = []
    flattened_rows: List[Dict[str, Any]] = []
    total_rows = 0
    total_matched = 0
    total_unmatched = 0

    for index, image in enumerate(images):
        image_name = str(image.get("name") or "").strip() or "upload.png"
        image_bytes = bytes(image.get("bytes") or b"")
        parsed = extract_reference_rows_from_image(
            api_key=api_key,
            image_name=image_name,
            image_bytes=image_bytes,
            model=model,
        )
        matched_rows = match_reference_rows_to_candidates(parsed["rows"], candidates, run_key=run_key)

        image_rows: List[Dict[str, Any]] = []
        image_matched: List[Dict[str, Any]] = []
        image_external: List[Dict[str, Any]] = []
        for order, item in enumerate(matched_rows, start=1):
            enriched = {
                "image_index": index,
                "image_name": image_name,
                "image_row_index": order,
                "source_title": str(parsed.get("source_title") or source_hint or ""),
                **item,
            }
            image_rows.append(enriched)
            flattened_rows.append(enriched)
            if str(enriched.get("candidate_kind") or "") == "external_catalog_candidate":
                image_external.append(enriched)
            else:
                image_matched.append(enriched)

        row_count = len(image_rows)
        matched_count = len(image_matched)
        unmatched_count = len(image_external)
        total_rows += row_count
        total_matched += matched_count
        total_unmatched += unmatched_count

        image_reports.append(
            {
                "image_index": index,
                "image_name": image_name,
                "source_title": str(parsed.get("source_title") or source_hint or ""),
                "row_count": row_count,
                "matched_reference_count": matched_count,
                "external_catalog_count": unmatched_count,
                "warnings": list(parsed.get("warnings") or []),
                "rows": image_rows,
                "matched_reference_rows": image_matched,
                "external_catalog_rows": image_external,
            }
        )

    return {
        "run_key": run_key,
        "ai_model": model,
        "source_hint": source_hint,
        "image_count": len(images),
        "row_count": total_rows,
        "matched_reference_count": total_matched,
        "external_catalog_count": total_unmatched,
        "candidate_count": len(candidates),
        "images": image_reports,
        "rows": flattened_rows,
    }


def run_reference_import(
    *,
    storage: DGTeamStorage,
    run_key: str,
    api_key: str,
    images: Sequence[Mapping[str, Any]],
    model: str = DEFAULT_OPENROUTER_MODEL,
    source_hint: str = "",
    preview_payload: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    if preview_payload is None:
        preview_payload = preview_reference_import(
            storage=storage,
            run_key=run_key,
            api_key=api_key,
            images=images,
            model=model,
            source_hint=source_hint,
        )

    persisted_rows = _flatten_preview_rows(preview_payload)
    import_id = storage.create_reference_import_run(
        ai_model=str(preview_payload.get("ai_model") or model),
        image_count=int(preview_payload.get("image_count") or len(images)),
        source_hint=str(preview_payload.get("source_hint") or source_hint),
        status="running",
        summary_json=json.dumps(_compact_preview_summary(preview_payload), ensure_ascii=False),
    )

    try:
        storage.replace_reference_import_rows(import_id, persisted_rows)
        summary = {
            "run_key": run_key,
            "import_id": import_id,
            "ai_model": str(preview_payload.get("ai_model") or model),
            "image_count": int(preview_payload.get("image_count") or len(images)),
            "row_count": int(preview_payload.get("row_count") or len(persisted_rows)),
            "matched_count": int(preview_payload.get("matched_reference_count") or 0),
            "unmatched_count": int(preview_payload.get("external_catalog_count") or 0),
            "preview": _compact_preview_summary(preview_payload),
        }
        storage.finalize_reference_import_run(import_id, status="completed", summary=summary)
        return summary
    except Exception as exc:
        storage.finalize_reference_import_run(
            import_id,
            status="failed",
            summary={
                "run_key": run_key,
                "import_id": import_id,
                "ai_model": str(preview_payload.get("ai_model") or model),
                "image_count": int(preview_payload.get("image_count") or len(images)),
                "error": str(exc),
                "preview": _compact_preview_summary(preview_payload),
            },
        )
        raise
