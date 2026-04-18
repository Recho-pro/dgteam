from __future__ import annotations

import hashlib
import io
import logging
import mimetypes
import re
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from dgteam.core.openrouter import (
    openrouter_chat_json,
    openrouter_image_to_data_url,
    openrouter_json_schema,
)
from dgteam.core.textio import read_json_utf8, write_json_utf8
from dgteam.integrations.wechat_official.client import WechatOfficialClient
from dgteam.integrations.wechat_official.models import (
    WechatOfficialRecognitionResult,
    WechatOfficialRecognitionTask,
)

LOGGER = logging.getLogger("dgteam.wechat_official.image_recognizer")

SUPPORTED_PAGE_TYPES = {"product_detail", "sku_selector"}
TERMINAL_UNSUPPORTED_PAGE_TYPES = {
    "price_list",
    "quote_sheet",
    "chat_record",
    "embedded_chat_screenshot",
    "listing_page",
    "store_page",
    "other_unsupported",
}


class WechatOfficialEcommerceImageRecognizer:
    def __init__(
        self,
        *,
        client: WechatOfficialClient,
        api_key: str,
        primary_model: str,
        fallback_model: str,
        cache_dir: Path,
        cache_namespace: str = "default",
        recognition_profile: str = "full",
        timeout_seconds: int = 75,
        max_edge_px: int = 1600,
        max_bytes: int = 650000,
        jpeg_quality: int = 82,
    ):
        self.client = client
        self.api_key = str(api_key or "").strip()
        self.primary_model = str(primary_model or "").strip()
        self.fallback_model = str(fallback_model or "").strip()
        self.cache_namespace = str(cache_namespace or "default").strip() or "default"
        self.recognition_profile = str(recognition_profile or "full").strip().lower() or "full"
        self.cache_dir = Path(cache_dir).resolve()
        self.timeout_seconds = max(3, int(timeout_seconds or 75))
        self.max_edge_px = max(256, int(max_edge_px or 1600))
        self.max_bytes = max(40000, int(max_bytes or 650000))
        self.jpeg_quality = min(95, max(55, int(jpeg_quality or 82)))
        self.images_dir = self.cache_dir / "images"
        self.results_dir = self.cache_dir / "results"
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.results_dir.mkdir(parents=True, exist_ok=True)

    def recognize(self, task: WechatOfficialRecognitionTask) -> WechatOfficialRecognitionResult:
        if not self.api_key:
            return WechatOfficialRecognitionResult(
                task_id=task.task_id,
                status="failed",
                summary="Image recognition API key is not configured.",
                model="",
            )

        try:
            started_at = time.perf_counter()
            download_started_at = time.perf_counter()
            image_bytes, image_name, content_type = self._download_image(task)
            download_ms = round((time.perf_counter() - download_started_at) * 1000, 1)
            result = self._recognize_internal(
                task_id=task.task_id,
                image_name=image_name,
                image_bytes=image_bytes,
                content_type=content_type,
                query_hint=task.query_hint,
                telemetry={
                    "download_ms": download_ms,
                },
            )
            result.raw_payload = {
                **dict(result.raw_payload or {}),
                "timings_ms": {
                    **dict(result.raw_payload.get("timings_ms") or {}),
                    "total_ms": round((time.perf_counter() - started_at) * 1000, 1),
                },
            }
            return result
        except Exception as exc:
            return WechatOfficialRecognitionResult(
                task_id=task.task_id,
                status="failed",
                summary=str(exc or "image recognition failed").strip(),
                model=self.primary_model,
            )

    def recognize_image_blob(
        self,
        *,
        task_id: str,
        image_name: str,
        image_bytes: bytes,
        content_type: str = "",
        query_hint: str = "",
    ) -> WechatOfficialRecognitionResult:
        if not self.api_key:
            return WechatOfficialRecognitionResult(
                task_id=task_id,
                status="failed",
                summary="Image recognition API key is not configured.",
                model="",
            )

        started_at = time.perf_counter()
        result = self._recognize_internal(
            task_id=task_id,
            image_name=image_name,
            image_bytes=image_bytes,
            content_type=content_type,
            query_hint=query_hint,
            telemetry={"download_ms": 0.0},
        )
        result.raw_payload = {
            **dict(result.raw_payload or {}),
            "timings_ms": {
                **dict(result.raw_payload.get("timings_ms") or {}),
                "total_ms": round((time.perf_counter() - started_at) * 1000, 1),
            },
        }
        return result

    def _recognize_internal(
        self,
        *,
        task_id: str,
        image_name: str,
        image_bytes: bytes,
        content_type: str,
        query_hint: str,
        telemetry: dict[str, Any] | None = None,
    ) -> WechatOfficialRecognitionResult:
        image_hash = hashlib.sha256(image_bytes).hexdigest()
        cache_key = self._cache_key(image_hash, query_hint=query_hint)
        cached = self._load_cached_result(cache_key)
        if cached is not None:
            cached.raw_payload = {
                **dict(cached.raw_payload or {}),
                "timings_ms": {
                    **dict(cached.raw_payload.get("timings_ms") or {}),
                    **dict(telemetry or {}),
                    "cache_hit": True,
                },
            }
            return cached

        self._save_image_blob(image_hash, image_name, image_bytes)
        preprocess_started_at = time.perf_counter()
        processed_name, processed_bytes, preprocess_meta = self._prepare_for_model(
            image_name=image_name,
            image_bytes=image_bytes,
        )
        preprocess_ms = round((time.perf_counter() - preprocess_started_at) * 1000, 1)

        model_started_at = time.perf_counter()
        selected_model, selected_payload = self._select_model_payload(
            task_id=task_id,
            image_name=processed_name,
            image_bytes=processed_bytes,
            query_hint=query_hint,
        )
        model_ms = round((time.perf_counter() - model_started_at) * 1000, 1)

        result = self._payload_to_result(
            task_id=task_id,
            payload=selected_payload,
            model=selected_model,
            image_hash=image_hash,
            image_name=image_name,
            content_type=content_type,
            preprocess_meta=preprocess_meta,
            query_hint=query_hint,
            telemetry={
                **dict(telemetry or {}),
                "cache_hit": False,
                "preprocess_ms": preprocess_ms,
                "model_ms": model_ms,
            },
        )
        self._save_cached_result(cache_key, result)
        return result

    def _select_model_payload(
        self,
        *,
        task_id: str,
        image_name: str,
        image_bytes: bytes,
        query_hint: str,
    ) -> tuple[str, dict[str, Any]]:
        attempts: list[str] = []
        payload_candidate: dict[str, Any] | None = None
        model_candidate = self.primary_model

        for model in self._model_sequence():
            try:
                payload = self._recognize_with_model(
                    image_name=image_name,
                    image_bytes=image_bytes,
                    model=model,
                    query_hint=query_hint,
                )
            except Exception as exc:
                message = str(exc or "").strip() or f"{model} failed"
                attempts.append(f"{model}: {message}")
                LOGGER.warning(
                    "wechat official image model failed task_id=%s model=%s error=%s",
                    task_id,
                    model,
                    message,
                )
                continue

            if payload_candidate is None:
                payload_candidate = payload
                model_candidate = model
            if not self._needs_fallback(payload):
                return model, payload

            attempts.append(f"{model}: low-confidence")

        if payload_candidate is not None:
            return model_candidate, payload_candidate

        raise RuntimeError("; ".join(attempts) or "all image recognition models failed")

    def _model_sequence(self) -> list[str]:
        ordered: list[str] = []
        for item in (self.primary_model, self.fallback_model):
            clean = str(item or "").strip()
            if clean and clean not in ordered:
                ordered.append(clean)
        return ordered

    def _download_image(self, task: WechatOfficialRecognitionTask) -> tuple[bytes, str, str]:
        local_path_value = str(task.metadata.get("downloaded_image_path") or "").strip()
        if local_path_value:
            local_path = Path(local_path_value)
            if local_path.exists():
                image_name = str(task.metadata.get("downloaded_image_name") or local_path.name).strip() or local_path.name
                content_type = str(task.metadata.get("downloaded_content_type") or "").strip()
                if not content_type:
                    content_type = mimetypes.guess_type(image_name)[0] or "application/octet-stream"
                return local_path.read_bytes(), image_name, content_type
            LOGGER.warning(
                "wechat official staged image missing task_id=%s path=%s",
                task.task_id,
                local_path,
            )
        if task.media_id:
            return self.client.download_media(task.media_id)
        if task.pic_url:
            return self.client.download_image_url(task.pic_url)
        raise RuntimeError("Image task does not include a usable media_id or pic_url.")

    def _recognize_with_model(
        self,
        *,
        image_name: str,
        image_bytes: bytes,
        model: str,
        query_hint: str,
    ) -> dict[str, Any]:
        schema = {
            "type": "object",
            "properties": {
                "supported": {"type": "boolean"},
                "page_type": {
                    "type": "string",
                    "enum": [
                        "product_detail",
                        "sku_selector",
                        "price_list",
                        "quote_sheet",
                        "chat_record",
                        "embedded_chat_screenshot",
                        "listing_page",
                        "store_page",
                        "other_unsupported",
                        "unknown",
                    ],
                },
                "brand": {"type": "string"},
                "series": {"type": "string"},
                "family": {"type": "string"},
                "capacity": {"type": "string"},
                "color": {"type": "string"},
                "edition": {"type": "string"},
                "screen_price_text": {"type": "string"},
                "query_candidates": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "confidence": {
                    "type": "string",
                    "enum": ["high", "medium", "low", "unknown"],
                },
                "reason": {"type": "string"},
                "warnings": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": [
                "supported",
                "page_type",
                "brand",
                "series",
                "family",
                "capacity",
                "color",
                "edition",
                "screen_price_text",
                "query_candidates",
                "confidence",
                "reason",
                "warnings",
            ],
            "additionalProperties": False,
        }
        prompt = self._build_prompt(query_hint=query_hint)
        return openrouter_chat_json(
            api_key=self.api_key,
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Return JSON only. Extract product attributes from ecommerce detail screenshots. "
                        "Never invent attributes that are not visible."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": openrouter_image_to_data_url(image_name, image_bytes)}},
                    ],
                },
            ],
            response_format=openrouter_json_schema("wechat_official_ecommerce_screenshot", schema),
            timeout=self.timeout_seconds,
        )

    def _build_prompt(self, *, query_hint: str) -> str:
        if self.recognition_profile == "fast":
            prompt = (
                "You are running the fast lane for a Chinese ecommerce screenshot recognizer. "
                "Read only directly visible information from the product title area and currently selected SKU chips.\n"
                "Rules:\n"
                "1. Only support ecommerce detail pages or SKU-selector screenshots for a single product.\n"
                "2. Focus on family, capacity, color, edition, and a short set of search candidates.\n"
                "3. Ignore subsidy, coupon, livestream, trade-in, financing, shipping, ranking, and gift text.\n"
                "4. Return at most 2 concise query_candidates.\n"
                "5. Lower confidence instead of guessing.\n"
                "6. If the screenshot is a chat record, forwarded chat screenshot, list page, store page, quote sheet, or price table, set supported=false and choose the matching page_type.\n"
                "7. If unsupported, set supported=false.\n"
            )
        else:
            prompt = (
                "You are extracting product attributes from a Chinese ecommerce product-detail screenshot. "
                "Only use details that are clearly visible in the image. "
                "Do not guess hidden attributes. "
                "Your goal is to produce stable query candidates for a downstream market-price search engine.\n"
                "Rules:\n"
                "1. Treat the image as supported only when it looks like a single-product detail page or SKU selector.\n"
                "2. Prioritize the title area and the currently selected SKU or color option.\n"
                "3. Ignore promotion text such as coupons, subsidy, trade-in, livestream, installment, and gifts.\n"
                "4. screen_price_text should capture the most visible on-screen selling price text, but it is not the market price.\n"
                "5. Return 1 to 3 concise query_candidates that a user could type into a search box.\n"
                "6. family should be the full family name when visible, for example 'iPhone 17 Pro Max'.\n"
                "7. If details are incomplete, lower confidence and explain why in reason.\n"
                "8. If the image is a chat record, embedded screenshot inside a chat, quote sheet, price list table, product listing page, or store page, set supported=false and choose the matching page_type.\n"
                "9. If the image is not a supported product-detail screenshot, set supported=false.\n"
            )
        clean_hint = _normalize_query_candidate(query_hint)
        if clean_hint:
            prompt += (
                "\nReference hint from an earlier stage: "
                f"'{clean_hint}'. Only keep or use this hint when the screenshot itself supports it."
            )
        return prompt

    def _payload_to_result(
        self,
        *,
        task_id: str,
        payload: dict[str, Any],
        model: str,
        image_hash: str,
        image_name: str,
        content_type: str,
        preprocess_meta: dict[str, Any],
        query_hint: str,
        telemetry: dict[str, Any] | None = None,
    ) -> WechatOfficialRecognitionResult:
        page_type = _normalize_page_type(str(payload.get("page_type") or "").strip())
        payload = {
            **dict(payload or {}),
            "page_type": page_type,
        }
        supported = bool(payload.get("supported")) and page_type in SUPPORTED_PAGE_TYPES
        human_reason = _humanize_reason(
            str(payload.get("reason") or "").strip(),
            page_type=page_type,
        )
        candidates = self._build_candidates(payload)
        summary = self._build_summary(payload)
        if not supported:
            candidates = []
            summary = human_reason or summary
        status = "success" if supported and candidates else "unsupported"
        return WechatOfficialRecognitionResult(
            task_id=task_id,
            status=status,
            recognized_query=candidates[0] if candidates else "",
            confidence=str(payload.get("confidence") or "unknown").strip().lower(),
            model=model,
            candidates=candidates,
            summary=summary or str(payload.get("reason") or "").strip(),
            raw_payload={
                **dict(payload or {}),
                "image_hash": image_hash,
                "image_name": image_name,
                "content_type": content_type,
                "preprocess": dict(preprocess_meta or {}),
                "recognition_profile": self.recognition_profile,
                "cache_namespace": self.cache_namespace,
                "query_hint": str(query_hint or "").strip(),
                "terminal_unsupported": page_type in TERMINAL_UNSUPPORTED_PAGE_TYPES,
                "human_reason": human_reason,
                "timings_ms": dict(telemetry or {}),
            },
        )

    @staticmethod
    def _needs_fallback(payload: dict[str, Any]) -> bool:
        if not bool(payload.get("supported")):
            return True
        confidence = str(payload.get("confidence") or "unknown").strip().lower()
        candidates = [str(item or "").strip() for item in list(payload.get("query_candidates") or []) if str(item or "").strip()]
        family = str(payload.get("family") or "").strip()
        return confidence in {"low", "unknown"} or (not candidates and not family)

    @staticmethod
    def _build_candidates(payload: dict[str, Any]) -> list[str]:
        brand = _normalize_text_value(str(payload.get("brand") or "").strip())
        capacity = _normalize_capacity(str(payload.get("capacity") or "").strip())
        edition = _normalize_edition(str(payload.get("edition") or "").strip())
        raw_family = _normalize_text_value(str(payload.get("family") or "").strip())
        family = _normalize_family(
            raw_family,
            brand=brand,
            capacity=capacity,
        )
        color = _normalize_color(str(payload.get("color") or "").strip())
        raw_candidates = [
            _normalize_query_candidate(str(item or "").strip())
            for item in list(payload.get("query_candidates") or [])
            if str(item or "").strip()
        ]

        generated: list[str] = []
        family_with_capacity = family and capacity and _contains_capacity_tokens(family, capacity)
        if family and capacity and not family_with_capacity:
            if color:
                generated.append(f"{family} {capacity} {color}")
            generated.append(f"{family} {capacity}")
        if family and color:
            generated.append(f"{family} {color}")
        if family:
            generated.append(family)
        generated.extend(
            _expand_family_alias_candidates(
                brand=brand,
                raw_family=raw_family,
                family=family,
                capacity=capacity,
                color=color,
                edition=edition,
            )
        )

        ordered: list[str] = []
        seen: set[str] = set()
        for item in [*generated, *raw_candidates]:
            clean = _normalize_query_candidate(item)
            if not clean:
                continue
            key = clean.casefold()
            if key in seen:
                continue
            seen.add(key)
            ordered.append(clean)
        return ordered[:4]

    @staticmethod
    def _build_summary(payload: dict[str, Any]) -> str:
        parts: list[str] = []
        brand = _normalize_text_value(str(payload.get("brand") or "").strip())
        capacity = _normalize_capacity(str(payload.get("capacity") or "").strip())
        values = {
            "brand": brand,
            "family": _normalize_family(
                str(payload.get("family") or "").strip(),
                brand=brand,
                capacity=capacity,
            ),
            "capacity": capacity,
            "color": _normalize_color(str(payload.get("color") or "").strip()),
            "edition": _normalize_edition(str(payload.get("edition") or "").strip()),
        }
        for key in ("brand", "family", "capacity", "color", "edition"):
            value = values[key]
            if value:
                parts.append(value)
        return " / ".join(parts)

    def _prepare_for_model(self, *, image_name: str, image_bytes: bytes) -> tuple[str, bytes, dict[str, Any]]:
        try:
            with Image.open(io.BytesIO(image_bytes)) as source:
                image = ImageOps.exif_transpose(source)
                original_size = list(image.size)
                if image.mode not in {"RGB", "L"}:
                    flattened = Image.new("RGB", image.size, (255, 255, 255))
                    rgba = image.convert("RGBA")
                    flattened.paste(rgba, mask=rgba.getchannel("A"))
                    image = flattened
                elif image.mode == "L":
                    image = image.convert("RGB")

                if max(image.size) > self.max_edge_px:
                    image.thumbnail((self.max_edge_px, self.max_edge_px), Image.Resampling.LANCZOS)

                processed_bytes = self._encode_image_bytes(image)
                return (
                    f"{Path(image_name).stem}.jpg",
                    processed_bytes,
                    {
                        "original_size": original_size,
                        "processed_size": list(image.size),
                        "processed_bytes": len(processed_bytes),
                    },
                )
        except Exception:
            return (
                image_name,
                image_bytes,
                {
                    "original_size": [],
                    "processed_size": [],
                    "processed_bytes": len(image_bytes),
                    "passthrough": True,
                },
            )

    def _encode_image_bytes(self, image: Image.Image) -> bytes:
        quality_steps: list[int] = []
        for delta in (0, 8, 16, 24):
            quality = max(55, self.jpeg_quality - delta)
            if quality not in quality_steps:
                quality_steps.append(quality)

        for quality in quality_steps:
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=quality, optimize=True)
            encoded = buffer.getvalue()
            if len(encoded) <= self.max_bytes or quality == quality_steps[-1]:
                return encoded
        raise RuntimeError("failed to encode image for model input")

    def _save_image_blob(self, image_hash: str, image_name: str, image_bytes: bytes) -> None:
        suffix = Path(image_name).suffix or ".bin"
        path = self.images_dir / f"{image_hash}{suffix}"
        if not path.exists():
            path.write_bytes(image_bytes)

    def _cache_key(self, image_hash: str, *, query_hint: str = "") -> str:
        identity = "|".join(
            [
                image_hash,
                self.cache_namespace,
                self.recognition_profile,
                self.primary_model,
                self.fallback_model,
                str(self.max_edge_px),
                str(self.max_bytes),
                str(self.jpeg_quality),
                _normalize_query_candidate(query_hint),
            ]
        )
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    def _cache_path(self, cache_key: str) -> Path:
        return self.results_dir / f"{cache_key}.json"

    def _load_cached_result(self, cache_key: str) -> WechatOfficialRecognitionResult | None:
        path = self._cache_path(cache_key)
        if not path.exists():
            return None
        payload = read_json_utf8(path)
        if not isinstance(payload, dict):
            return None
        return WechatOfficialRecognitionResult.from_dict(payload)

    def _save_cached_result(self, cache_key: str, result: WechatOfficialRecognitionResult) -> None:
        write_json_utf8(self._cache_path(cache_key), result.to_dict())


def _normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _normalize_text_value(value: str) -> str:
    clean = _normalize_whitespace(value)
    if not clean or _looks_unknown_value(clean):
        return ""
    return clean


def _normalize_capacity(value: str) -> str:
    clean = _normalize_text_value(value)
    if not clean:
        return ""
    clean = re.sub(r"(?i)\b(\d+)\s*gb\b", r"\1G", clean)
    clean = re.sub(r"(?i)\b(\d+)\s*g\b", r"\1G", clean)
    clean = re.sub(r"(?i)\b(\d+)\s*tb\b", r"\1T", clean)
    clean = re.sub(r"(?i)\b(\d+)\s*t\b", r"\1T", clean)
    return clean


def _normalize_color(value: str) -> str:
    clean = _normalize_text_value(value)
    if not clean:
        return ""
    return clean


def _normalize_family(value: str, *, brand: str = "", capacity: str = "") -> str:
    clean = _normalize_text_value(value)
    if not clean:
        return ""

    for token in _extract_capacity_tokens(capacity):
        clean = re.sub(rf"(?i)\b{re.escape(token)}\b", " ", clean)
        if token.endswith("g"):
            clean = re.sub(rf"(?i)\b{re.escape(token[:-1])}\s*gb\b", " ", clean)
        if token.endswith("t"):
            clean = re.sub(rf"(?i)\b{re.escape(token[:-1])}\s*tb\b", " ", clean)

    generic_terms = (
        "笔记本电脑",
        "笔记本",
        "高能本",
        "轻薄本",
        "游戏本",
        "电竞本",
        "智能手机",
        "手机",
        "平板电脑",
        "平板",
        "官网标配",
        "官方标配",
    )
    for term in generic_terms:
        clean = clean.replace(term, " ")

    clean = _normalize_whitespace(clean)
    return "" if _looks_unknown_value(clean) else clean


def _normalize_edition(value: str) -> str:
    clean = _normalize_text_value(value)
    if not clean:
        return ""
    lower = clean.casefold()
    if lower in {"5g", "4g", "wifi", "wi-fi", "official standard"}:
        return ""
    if any(token in lower for token in ("dual sim", "sim", "coupon", "subsidy", "trade-in")):
        return ""
    if any(token in clean for token in ("双卡", "激活", "补贴", "国补", "直播", "券")):
        return ""
    if clean in {"未指定", "未知", "默认", "标准版"}:
        return ""
    return clean


def _normalize_query_candidate(value: str) -> str:
    clean = _normalize_whitespace(value)
    if not clean:
        return ""
    clean = re.sub(r"(?i)\b(\d+)\s*gb\b", r"\1G", clean)
    clean = re.sub(r"(?i)\b(\d+)\s*tb\b", r"\1T", clean)
    clean = re.sub(r"(?i)\b(unknown|unspecified|not specified|null|none|n/a|na)\b", " ", clean)
    clean = re.sub(r"\b5G\b", " ", clean, flags=re.IGNORECASE)
    clean = clean.replace("未指定", " ").replace("未知", " ")
    clean = clean.replace("双卡双待", " ").replace("双卡", " ").replace("激活", " ")
    clean = re.sub(r"\s+", " ", clean)
    clean = _dedupe_space_terms(clean.strip())
    return "" if _looks_unknown_value(clean) else clean


def _normalize_page_type(value: str) -> str:
    compact = _normalize_whitespace(value).casefold().replace("-", "_").replace(" ", "_")
    if not compact or compact == "unknown":
        return "unknown"
    if compact in {
        "product_detail",
        "sku_selector",
        "price_list",
        "quote_sheet",
        "chat_record",
        "embedded_chat_screenshot",
        "listing_page",
        "store_page",
        "other_unsupported",
    }:
        return compact
    if any(token in compact for token in ("detail", "详情", "sku", "spec")):
        return "product_detail" if "sku" not in compact else "sku_selector"
    if any(token in compact for token in ("price_list", "quote", "table", "报价", "行情表", "表格")):
        return "quote_sheet" if "quote" in compact or "报价" in compact or "行情" in compact else "price_list"
    if any(token in compact for token in ("chat", "conversation", "wechat")):
        return "chat_record"
    if "embedded" in compact or "thumbnail" in compact:
        return "embedded_chat_screenshot"
    if any(token in compact for token in ("listing", "list_page", "search_result")):
        return "listing_page"
    if any(token in compact for token in ("store", "shop", "homepage")):
        return "store_page"
    return "other_unsupported"


def _humanize_reason(reason: str, *, page_type: str) -> str:
    clean_reason = _normalize_whitespace(reason)
    if page_type in {"price_list", "quote_sheet"}:
        return "这张图更像整张报价表，不是单个商品详情页。"
    if page_type in {"chat_record", "embedded_chat_screenshot"}:
        return "这张图更像聊天记录，商品信息不够集中。"
    if page_type in {"listing_page", "store_page"}:
        return "这张图还停留在列表页或店铺页，没有落到单个商品详情。"
    lowered = clean_reason.casefold()
    if "price list" in lowered or "quote sheet" in lowered or "table" in lowered:
        return "这张图更像整张报价表，不是单个商品详情页。"
    if "chat" in lowered or "conversation" in lowered:
        return "这张图更像聊天记录，商品信息不够集中。"
    if "blurry" in lowered or "unclear" in lowered or "not enough" in lowered:
        return "这张图里的标题或规格不够清楚，我没法稳妥地给你对行情。"
    return clean_reason


def _expand_family_alias_candidates(
    *,
    brand: str,
    raw_family: str,
    family: str,
    capacity: str,
    color: str,
    edition: str,
) -> list[str]:
    candidates: list[str] = []
    family_lower = raw_family.casefold()
    if brand.casefold() in {"apple", "苹果"} and ("watch" in family_lower or "手表" in raw_family):
        match = re.search(r"(?i)(?:series\s*)?s?\s*(\d{1,2})", raw_family)
        if match:
            series_number = match.group(1)
            series_label = f"Series {series_number}"
            base_label = f"Apple Watch {series_label}"
            normalized_edition = _normalize_watch_edition(edition)
            if normalized_edition:
                candidates.append(f"{base_label} {normalized_edition}")
                candidates.append(f"{series_label} {normalized_edition}")
            candidates.append(base_label)
            candidates.append(series_label)
            if color:
                candidates.append(f"{base_label} {color}")
    if family and capacity and color:
        candidates.append(f"{family} {capacity} {color}")
    return candidates


def _normalize_watch_edition(edition: str) -> str:
    clean = _normalize_text_value(edition)
    if not clean:
        return ""
    lowered = clean.casefold()
    if "gps" in lowered and ("蜂窝" in clean or "cellular" in lowered):
        return "GPS+蜂窝"
    if "gps" in lowered:
        return "GPS"
    if "蜂窝" in clean or "cellular" in lowered:
        return "GPS+蜂窝"
    return clean


def _dedupe_space_terms(value: str) -> str:
    parts = [part for part in str(value or "").split(" ") if part]
    seen: set[str] = set()
    ordered: list[str] = []
    for part in parts:
        key = part.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(part)
    return " ".join(ordered)


def _looks_unknown_value(value: str) -> bool:
    clean = _normalize_whitespace(value)
    if not clean:
        return True
    lower = clean.casefold()
    if lower in {
        "unknown",
        "unspecified",
        "not specified",
        "null",
        "none",
        "n/a",
        "na",
    }:
        return True
    if clean in {"未指定", "未知", "默认", "无", "空"}:
        return True
    return False


def _contains_capacity_tokens(family: str, capacity: str) -> bool:
    family_tokens = _extract_capacity_tokens(family)
    capacity_tokens = _extract_capacity_tokens(capacity)
    return bool(capacity_tokens) and capacity_tokens.issubset(family_tokens)


def _extract_capacity_tokens(value: str) -> set[str]:
    normalized = _normalize_whitespace(value).casefold()
    normalized = normalized.replace("gb", "g").replace("tb", "t")
    tokens = re.findall(r"\d+(?:\.\d+)?[gt]", normalized)
    return set(tokens)
