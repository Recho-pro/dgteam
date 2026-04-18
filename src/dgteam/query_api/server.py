from __future__ import annotations

import argparse
import base64
import binascii
import json
import logging
import re
import sys
import threading
import unicodedata
import uuid
from collections import OrderedDict, defaultdict
from copy import deepcopy
from dataclasses import dataclass
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set, Tuple
from urllib.parse import parse_qs, urlparse

from dgteam.market.market_engine import build_market_v1  # noqa: E402
from dgteam.market.price_cleaning import (
    dedupe_latest_rows,
    numeric_rows,
    percentile,
    select_recent_sample_window,
)  # noqa: E402
from dgteam.core.storage import DGTeamStorage, normalize_search_text  # noqa: E402
from dgteam.query_api.contracts import (  # noqa: E402
    API_CONTRACT_VERSION,
    DETAIL_CONTRACT_VERSION,
    DETAIL_REF_VERSION,
    MODEL_SUGGESTION_LIMIT,
    backend_owned_logic_payload,
    endpoint_contracts_payload,
)
from dgteam.query_api.error_contracts import (  # noqa: E402
    api_error_payload,
    unknown_api_payload,
    unsupported_method_payload,
)
from dgteam.query_api.http_endpoints import (  # noqa: E402
    health_payload,
    search_payload as endpoint_search_payload,
    sku_payload as endpoint_sku_payload,
)
from dgteam.query_api.branch_assembly import BranchPayloadAssembler  # noqa: E402
from dgteam.query_api.http_runtime import (  # noqa: E402
    QuietThreadingHTTPServer as RuntimeQuietThreadingHTTPServer,
    build_handler as build_runtime_handler,
    html_response as runtime_html_response,
    is_client_disconnect_error as runtime_is_client_disconnect_error,
    json_response as runtime_json_response,
)
from dgteam.query_api.search_ranking import (  # noqa: E402
    build_hot_query_candidates as build_hot_query_candidates_impl,
    candidate_bucket_priority as candidate_bucket_priority_impl,
    candidate_preference_tuple as candidate_preference_tuple_impl,
    score_candidate as score_candidate_impl,
)
from dgteam.query_api.search_pipeline import (  # noqa: E402
    build_search_payload as build_search_response_payload,
    effective_search_limit,
    search_cache_key,
)
from dgteam.query_api.snapshot_assembly import (  # noqa: E402
    legacy_snapshot_query,
    quote_resolution_payload,
    refinement_resolution_payload,
    snapshot_error_payload,
)
from dgteam.query_api.snapshot_refinement import refine_snapshot  # noqa: E402
from dgteam.query_api.static_assets import PROJECT_ROOT, cache_headers_for_static_path, utf8_content_type  # noqa: E402
from dgteam.query_api.ui_assets import render_index_html, resolve_release_ui_asset_dir  # noqa: E402


DEFAULT_DB_PATH = PROJECT_ROOT / "runtime" / "local" / "data" / "dgteam.db"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
SEARCH_CACHE_SIZE = 256
CLIENT_DISCONNECT_ERRORS = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)
QUIET_404_PREFIXES: Tuple[str, ...] = (
    "/wp-admin/",
    "/wordpress/",
    "/SDK/",
    "/.well-known/",
    "/favicon.ico",
    "/security.txt",
    "/info.php",
    "/phpinfo.php",
    "/boaform/",
    "/cgi-bin/",
)
QUIET_METHODS: Tuple[str, ...] = ("PROPFIND", "OPTIONS")
CAPACITY_TOKEN_RE = re.compile(r"(?i)(\d+(?:GB|G)?\+\d+(?:GB|G|TB|T)|\d+(?:GB|G|TB|T))")
CHINESE_NUMERAL_RE = re.compile(r"[零〇一二两三四五六七八九十]+")
BRAND_QUERY_ALIASES: Dict[str, Tuple[str, ...]] = {
    "苹果": ("iphone", "apple iphone", "apple"),
    "iphone": ("苹果",),
    "apple": ("苹果", "iphone"),
    "红米": ("redmi",),
    "redmi": ("红米",),
    "小米": ("xiaomi",),
    "xiaomi": ("小米",),
    "华为": ("huawei",),
    "huawei": ("华为",),
    "荣耀": ("honor",),
    "honor": ("荣耀",),
    "一加": ("oneplus",),
    "oneplus": ("一加",),
    "三星": ("samsung",),
    "samsung": ("三星",),
    "联想": ("lenovo",),
    "lenovo": ("联想",),
    "联想电脑": ("lenovo", "联想"),
    "华硕": ("asus",),
    "asus": ("华硕",),
    "华硕电脑": ("asus", "华硕"),
    "戴尔": ("dell",),
    "dell": ("戴尔",),
    "戴尔电脑": ("dell", "戴尔"),
}
PINYIN_QUERY_ALIASES: Dict[str, Tuple[str, ...]] = {
    "pingguo": ("苹果", "iphone", "apple"),
    "hongmi": ("红米", "redmi"),
    "xiaomi": ("小米",),
    "xiaoxin": ("小新", "联想", "lenovo"),
    "huawei": ("华为", "huawei"),
    "rongyao": ("荣耀", "honor"),
    "yijia": ("一加", "oneplus"),
    "sanxing": ("三星", "samsung"),
    "pingban": ("平板", "ipad", "matepad", "magicpad"),
    "erji": ("耳机", "airpods"),
    "chongdiantou": ("充电头", "charger"),
    "chongdianqi": ("充电器", "charger"),
    "shujuxian": ("数据线", "cable"),
    "baoshijie": ("保时捷", "rs", "非凡大师"),
    "dashi": ("大师", "非凡大师", "rs"),
    "feifan": ("非凡", "非凡大师", "rs"),
    "zhizunban": ("至尊版", "ultra", "u"),
    "zhizun": ("至尊", "至尊版", "ultra", "u"),
}
BRAND_HINTS: Dict[str, Tuple[str, ...]] = {
    "苹果": ("苹果", "iphone", "apple"),
    "红米": ("红米", "redmi"),
    "小米": ("小米", "xiaomi"),
    "华为": ("华为", "huawei", "mate", "pura", "nova"),
    "荣耀": ("荣耀", "honor", "magic"),
    "VIVO": ("vivo", "iqoo"),
    "OPPO": ("oppo",),
    "一加": ("一加", "oneplus"),
    "三星": ("三星", "samsung"),
    "联想": ("联想", "lenovo", "小新", "thinkbook", "thinkpad", "yoga", "拯救者"),
    "联想电脑": ("联想", "lenovo", "小新", "thinkbook", "thinkpad", "yoga", "拯救者"),
    "华硕": ("华硕", "asus", "天选", "无畏", "灵耀", "rog"),
    "华硕电脑": ("华硕", "asus", "天选", "无畏", "灵耀", "rog"),
    "戴尔": ("戴尔", "dell", "alienware", "inspiron", "xps", "latitude"),
    "戴尔电脑": ("戴尔", "dell", "alienware", "inspiron", "xps", "latitude"),
}
SHORTHAND_TOKEN_EXPANSIONS: Dict[str, Tuple[str, ...]] = {
    "pm": ("pro max",),
    "p": ("pro",),
    "plus": ("plus",),
    "u": ("ultra", "至尊版"),
    "rs": ("rs", "非凡大师"),
    "至尊": ("至尊版", "ultra", "u"),
    "至尊版": ("至尊版", "ultra", "u"),
    "保时捷": ("rs", "非凡大师"),
    "保时捷版": ("rs", "非凡大师"),
    "大师": ("非凡大师", "rs"),
    "大师版": ("非凡大师", "rs"),
    "非凡": ("非凡大师", "rs"),
}
KNOWN_QUERY_TOKENS: Tuple[str, ...] = (
    "iphone",
    "apple",
    "redmi",
    "xiaomi",
    "huawei",
    "honor",
    "iqoo",
    "vivo",
    "oppo",
    "oneplus",
    "mate",
    "pura",
    "nova",
    "magic",
    "pro",
    "max",
    "plus",
    "ultra",
    "rs",
    "pingguo",
    "hongmi",
    "huawei",
    "rongyao",
    "yijia",
    "sanxing",
    "pingban",
    "erji",
    "chongdiantou",
    "chongdianqi",
    "baoshijie",
    "dashi",
    "feifan",
    "zhizun",
    "lenovo",
    "asus",
    "dell",
    "thinkbook",
    "thinkpad",
    "yoga",
    "alienware",
    "xiaoxin",
)
FREE_SUFFIX_EXPANSIONS: Set[str] = {"至尊", "至尊版", "保时捷", "保时捷版", "大师", "大师版", "非凡"}
SERIES_HINT_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "iphone": ("iphone",),
    "ipad": ("ipad", "平板"),
    "airpods": ("airpods", "pods", "耳机"),
    "mate": ("mate",),
    "pura": ("pura",),
    "nova": ("nova",),
    "magic": ("magic",),
    "note": ("note",),
}
CHARGER_CATEGORY_KEYWORDS: Tuple[str, ...] = (
    "充电头",
    "充电器",
    "快充",
    "数据线",
    "编织线",
    "车充",
    "充电底座",
    "充电宝",
    "移动电源",
    "电源适配器",
    "无线充",
    "magsafe",
    "mag safe",
    "cable",
    "charger",
    "20w",
    "30w",
    "35w",
    "45w",
    "65w",
    "67w",
    "80w",
    "100w",
)
CASE_CATEGORY_KEYWORDS: Tuple[str, ...] = (
    "保护壳",
    "手机壳",
    "表壳",
    "保护套",
    "皮套",
    "翻盖",
    "保护夹",
    "壳",
    "case",
)
ACCESSORY_CATEGORY_KEYWORDS: Tuple[str, ...] = (
    "转换器",
    "转接器",
    "鼠标",
    "键盘",
    "手写笔",
    "触控笔",
    "配件",
    "妙控",
    "触控板",
    "扩展坞",
    "散热背夹",
    "手柄",
    "adapter",
    "mouse",
    "keyboard",
    "pencil",
    "trackpad",
    "dock",
)
QUERY_CATEGORY_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "tablet": ("ipad", "平板", "matepad", "magicpad", "pad"),
    "laptop": ("电脑", "笔记本", "macbook", "matebook", "thinkbook", "thinkpad", "yoga", "小新", "拯救者"),
    "wearable": ("watch", "手表", "gps", "蜂窝"),
    "earbuds": ("airpods", "pods", "耳机"),
    "charger": CHARGER_CATEGORY_KEYWORDS,
    "case": CASE_CATEGORY_KEYWORDS,
    "accessory": ACCESSORY_CATEGORY_KEYWORDS,
}
NON_PHONE_CATEGORIES: Set[str] = {"tablet", "laptop", "wearable", "earbuds", "charger", "case", "accessory"}
APPLE_NON_PHONE_QUERY_KEYWORDS: Tuple[str, ...] = tuple(
    dict.fromkeys(
        (
            "平板",
            "ipad",
            "airpods",
            "耳机",
            "watch",
            "手表",
            "macbook",
            "电脑",
            "笔记本",
            *CHARGER_CATEGORY_KEYWORDS,
            *CASE_CATEGORY_KEYWORDS,
            *ACCESSORY_CATEGORY_KEYWORDS,
        )
    )
)
HONOR_RSR_QUERY_KEYWORDS: Tuple[str, ...] = ("保时捷", "保时捷版", "大师", "大师版", "非凡", "rs")
COLOR_QUERY_KEYWORDS: Tuple[str, ...] = (
    "黑",
    "黑色",
    "白",
    "白色",
    "银",
    "银色",
    "蓝",
    "蓝色",
    "深蓝",
    "深蓝色",
    "青",
    "青色",
    "青雾蓝",
    "紫",
    "紫色",
    "橙",
    "橙色",
    "星宇橙",
    "粉",
    "粉色",
    "金",
    "金色",
    "灰",
    "灰色",
    "绿",
    "绿色",
    "黄",
    "黄色",
    "红",
    "红色",
    "钛",
    "钛色",
    "曜石黑",
    "雪域白",
    "苍山灰",
)
COLOR_QUERY_TOKENS_NORMALIZED: Set[str] = {
    normalize_search_text(keyword)
    for keyword in COLOR_QUERY_KEYWORDS
    if normalize_search_text(keyword)
}
SPACED_PINYIN_ALIASES: Tuple[str, ...] = tuple(
    sorted({*PINYIN_QUERY_ALIASES.keys()}, key=len, reverse=True)
)
LOGGER = logging.getLogger("dgteam.query_api.server")


@dataclass(frozen=True)
class QueryContext:
    raw_query: str
    variants: List[Tuple[str, List[str], str]]
    core_variants: List[Tuple[str, List[str], str]]
    brand_hints: Set[str]
    series_hints: Set[str]
    category_intents: Set[str]
    bare_model_query: bool
    capacity_hints: Set[str]
    color_hints: Set[str]
    only_refinement: bool


@dataclass(frozen=True)
class DetailRef:
    data_source: str
    run_key: str
    brand_title: str
    series_title: str
    family_title: str
    condition_bucket: str
    branch_models: Tuple[str, ...] = ()
    external_key: str = ""


class APIError(Exception):
    def __init__(self, status: int, code: str, message: str, *, details: Dict[str, Any] | None = None):
        super().__init__(message)
        self.status = int(status)
        self.code = str(code or "api_error")
        self.message = str(message or "Request failed")
        self.details = dict(details or {})


def safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def range_text(values: Iterable[int]) -> str:
    normalized = [safe_int(value) for value in values if safe_int(value) > 0]
    if not normalized:
        return "--"
    return f"{min(normalized)}-{max(normalized)}"


def query_value(params: Dict[str, List[str]], *names: str, default: str = "") -> str:
    for name in names:
        values = params.get(name)
        if values:
            return str(values[0] or default)
    return default


def query_int_value(
    params: Dict[str, List[str]],
    *names: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw_value = query_value(params, *names, default=str(default))
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def friendly_status_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "--"
    replacements = {
        "公司纯原封/特定区域销售": "公司纯原封（特定区域）",
        "公司纯原封/可出全国": "公司纯原封（可出全国）",
        "公司纯原封/不怕串": "公司纯原封（不怕串）",
        "公司纯原封/默认": "公司纯原封（默认）",
        "全国纯原不怕串": "全国纯原（不怕串）",
        "保证全国纯原": "全国纯原",
        "保证全国纯原/可出全国": "全国纯原（可出全国）",
        "保证省内纯原": "省内纯原",
    }
    if text in replacements:
        return replacements[text]
    return text.replace("/", " / ")


def normalize_variant_title(group_title: str) -> str:
    title = str(group_title or "").strip()
    return title or "未区分颜色"


def normalize_capacity_label(token: str) -> str:
    text = str(token or "").strip().upper().replace(" ", "")
    text = text.replace("GB", "G").replace("TB", "T")
    pair_match = re.fullmatch(r"(\d+)G?\+(\d+)(G|T)", text)
    if pair_match:
        return f"{pair_match.group(1)}+{pair_match.group(2)}{pair_match.group(3)}"
    return text


def chinese_numeral_to_int(token: str) -> int | None:
    digits = {
        "零": 0,
        "〇": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    text = str(token or "").strip()
    if not text:
        return None
    if any(ch not in digits and ch != "十" for ch in text):
        return None
    if "十" in text:
        if text.count("十") > 1:
            return None
        left, right = text.split("十", 1)
        tens = digits.get(left, 1 if not left else None)
        if tens is None:
            return None
        if right:
            if any(ch not in digits for ch in right):
                return None
            ones = int("".join(str(digits[ch]) for ch in right))
        else:
            ones = 0
        return tens * 10 + ones
    return int("".join(str(digits[ch]) for ch in text))


def replace_chinese_numerals(text: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        converted = chinese_numeral_to_int(match.group(0))
        return str(converted) if converted is not None else match.group(0)

    return CHINESE_NUMERAL_RE.sub(_replace, text)


def normalize_query_surface(query: str) -> str:
    text = unicodedata.normalize("NFKC", str(query or "").strip()).lower()
    if not text:
        return ""
    text = replace_chinese_numerals(text)
    text = re.sub(r"[^\w\u4e00-\u9fff]+", " ", text)
    text = re.sub(r"(?i)pro\s*max", "pro max", text)
    text = re.sub(r"(?i)([a-z]{2,})(\d)", r"\1 \2", text)
    text = re.sub(r"(?i)(\d)([a-z]+)", r"\1 \2", text)
    text = re.sub(r"([\u4e00-\u9fff])([a-z]+)", r"\1 \2", text)
    text = re.sub(r"([a-z]+)([\u4e00-\u9fff])", r"\1 \2", text)
    text = re.sub(r"([\u4e00-\u9fff])(\d)", r"\1 \2", text)
    text = re.sub(r"(\d)([\u4e00-\u9fff])", r"\1 \2", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_query_compact_surface(query: str) -> str:
    text = unicodedata.normalize("NFKC", str(query or "").strip()).lower()
    if not text:
        return ""
    text = replace_chinese_numerals(text)
    text = re.sub(r"[^\w\u4e00-\u9fff]+", " ", text)
    text = re.sub(r"(?i)pro\s*max", "promax", text)
    text = re.sub(r"\s+", "", text)
    return text


def merge_spaced_pinyin_sequences(surface: str) -> List[str]:
    tokens = [token for token in normalize_query_surface(surface).split() if token]
    if len(tokens) < 2:
        return []

    expansions: List[str] = []
    seen: Set[str] = set()
    max_span = min(4, len(tokens))
    for span in range(max_span, 1, -1):
        for index in range(len(tokens) - span + 1):
            chunk_tokens = tokens[index : index + span]
            if not all(token.isalpha() for token in chunk_tokens):
                continue
            merged = "".join(chunk_tokens)
            if merged not in SPACED_PINYIN_ALIASES:
                continue
            merged_tokens = tokens[:index] + [merged] + tokens[index + span :]
            candidate = " ".join(merged_tokens)
            if candidate and candidate not in seen:
                seen.add(candidate)
                expansions.append(candidate)
    return expansions


def is_capacity_like_token(token: str) -> bool:
    raw_text = str(token or "").strip()
    if not raw_text:
        return False

    normalized = normalize_capacity_label(raw_text)
    if re.fullmatch(r"\d+\+\d+(G|T)", normalized):
        return True
    if re.fullmatch(r"\d+(G|T)", normalized):
        return True
    return raw_text.isdigit() and int(raw_text) in {64, 128, 256, 512, 1024, 2048}


def is_color_like_token(token: str) -> bool:
    normalized = normalize_search_text(token)
    return bool(normalized) and normalized in COLOR_QUERY_TOKENS_NORMALIZED


def strip_refinement_tokens(surface: str) -> Tuple[str, Set[str], Set[str]]:
    stripped_tokens: List[str] = []
    capacity_hints: Set[str] = set()
    color_hints: Set[str] = set()

    for token in normalize_query_surface(surface).split():
        normalized_token = normalize_search_text(token)
        if not normalized_token:
            continue
        if is_capacity_like_token(token):
            capacity_hints.add(normalized_token)
            continue
        if is_color_like_token(token):
            color_hints.add(normalized_token)
            continue
        stripped_tokens.append(token)

    return " ".join(stripped_tokens).strip(), capacity_hints, color_hints


def build_core_query_variants(
    query_variants: List[Tuple[str, List[str], str]],
) -> Tuple[List[Tuple[str, List[str], str]], Set[str], Set[str]]:
    core_variants: List[Tuple[str, List[str], str]] = []
    seen_norm: Set[str] = set()
    capacity_hints: Set[str] = set()
    color_hints: Set[str] = set()

    for _query_norm, _query_tokens, surface in query_variants:
        stripped_surface, stripped_capacity_hints, stripped_color_hints = strip_refinement_tokens(surface)
        capacity_hints.update(stripped_capacity_hints)
        color_hints.update(stripped_color_hints)
        if not stripped_surface:
            continue

        normalized_surface = normalize_query_surface(stripped_surface)
        normalized = normalize_search_text(normalized_surface)
        if not normalized or normalized in seen_norm:
            continue
        seen_norm.add(normalized)

        tokens = [
            normalize_search_text(part)
            for part in normalized_surface.replace("/", " ").split()
            if normalize_search_text(part)
        ]
        core_variants.append((normalized, tokens, normalized_surface))

    return core_variants, capacity_hints, color_hints


def damerau_levenshtein_distance(a: str, b: str, *, max_distance: int = 2) -> int:
    if a == b:
        return 0
    if not a or not b:
        return max(len(a), len(b))
    if abs(len(a) - len(b)) > max_distance:
        return max_distance + 1

    len_a = len(a)
    len_b = len(b)
    dp = [[0] * (len_b + 1) for _ in range(len_a + 1)]
    for i in range(len_a + 1):
        dp[i][0] = i
    for j in range(len_b + 1):
        dp[0][j] = j

    for i in range(1, len_a + 1):
        row_min = max_distance + 1
        for j in range(1, len_b + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            dp[i][j] = min(
                dp[i - 1][j] + 1,
                dp[i][j - 1] + 1,
                dp[i - 1][j - 1] + cost,
            )
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                dp[i][j] = min(dp[i][j], dp[i - 2][j - 2] + cost)
            row_min = min(row_min, dp[i][j])
        if row_min > max_distance:
            return max_distance + 1
    return dp[len_a][len_b]


def correct_query_token(token: str) -> str:
    text = str(token or "").strip().lower()
    if not text:
        return ""
    if any(ch.isdigit() for ch in text):
        corrected = re.sub(
            r"(?:(?<=\d)[oil]|[oil](?=\d))",
            lambda match: "0" if match.group(0) == "o" else "1",
            text,
        )
        if corrected != text:
            return corrected
    if not text.isalpha() or len(text) < 3:
        return text

    best = text
    best_distance = 10
    for candidate in KNOWN_QUERY_TOKENS:
        threshold = 1 if len(candidate) <= 5 else 2
        distance = damerau_levenshtein_distance(text, candidate, max_distance=threshold)
        if distance <= threshold and distance < best_distance:
            best = candidate
            best_distance = distance
    return best


def expand_category_bridges(surface: str) -> List[str]:
    normalized = normalize_query_surface(surface)
    expansions: List[str] = []

    def add(value: str) -> None:
        text = normalize_query_surface(value)
        if text and text != normalized and text not in expansions:
            expansions.append(text)

    apple_like = any(keyword in normalized for keyword in ("苹果", "iphone", "apple"))
    huawei_like = any(keyword in normalized for keyword in ("华为", "huawei"))
    honor_like = any(keyword in normalized for keyword in ("荣耀", "honor"))

    if "触控笔" in normalized:
        add(normalized.replace("触控笔", "手写笔"))
    if "手写笔" in normalized:
        add(normalized.replace("手写笔", "触控笔"))
    if "转接器" in normalized:
        add(normalized.replace("转接器", "转换器"))
    if "转换器" in normalized:
        add(normalized.replace("转换器", "转接器"))
    if "手机壳" in normalized:
        add(normalized.replace("手机壳", "保护壳"))
    elif normalized.endswith("壳") and "保护壳" not in normalized:
        add(normalized.replace("壳", "保护壳"))

    if apple_like and "平板" in normalized:
        add(normalized.replace("平板", "ipad"))
    if huawei_like and "平板" in normalized:
        add(normalized.replace("平板", "matepad"))
    if honor_like and "平板" in normalized:
        add(normalized.replace("平板", "magicpad"))
    if apple_like and "耳机" in normalized:
        add(normalized.replace("耳机", "airpods"))
    if apple_like and ("触控笔" in normalized or "手写笔" in normalized):
        add(normalized.replace("触控笔", "apple pencil").replace("手写笔", "apple pencil"))
        add(normalized.replace("触控笔", "apple 手写笔").replace("手写笔", "apple 手写笔"))
    if apple_like and ("保护壳" in normalized or "手机壳" in normalized or normalized.endswith("壳")):
        add("苹果 保护壳")
        add("apple 保护壳")
    if apple_like and ("充电头" in normalized or "充电器" in normalized):
        add(f"{normalized} apple charger")
        add(normalized.replace("充电头", "apple 20w charger").replace("充电器", "apple 20w charger"))
    if apple_like and re.search(r"\b\d+\s*w\b", normalized):
        watt_match = re.search(r"\b(\d+)\s*w\b", normalized)
        watt = watt_match.group(1) if watt_match else ""
        add(f"{normalized} 充电头")
        if watt:
            add(f"apple {watt}w charger")
            add(f"apple {watt}w 充电头")
            add(f"苹果 {watt}w 充电头")
        add(normalized.replace(" ", ""))
    if honor_like and any(keyword in normalized for keyword in HONOR_RSR_QUERY_KEYWORDS) and "magic" not in normalized:
        add(normalized.replace("荣耀", "荣耀 magic"))
        add(normalized.replace("honor", "honor magic"))
    return expansions


def expand_model_code_bridges(surface: str) -> List[str]:
    normalized = normalize_query_surface(surface)
    expansions: List[str] = []

    def add(value: str) -> None:
        text = normalize_query_surface(value)
        if text and text != normalized and text not in expansions:
            expansions.append(text)

    add(re.sub(r"\b([a-z])\s+(\d{1,4})(?=\b)", r"\1\2", normalized))
    add(re.sub(r"\b([a-z]{2,})\s+(\d{1,4})(?=\s+(?:pro|max|plus|pm|ultra|rs|mini|e)\b)", r"\1\2", normalized))
    add(re.sub(r"\b([a-z]{2,})\s+(\d+w)\b", r"\1\2", normalized))
    return expansions


def filter_brand_alias_replacements(source: str, replacements: Tuple[str, ...], surface: str) -> Tuple[str, ...]:
    if source not in {"苹果", "apple", "iphone"}:
        return replacements
    surface_normalized = normalize_search_text(surface)
    if any(keyword in surface for keyword in APPLE_NON_PHONE_QUERY_KEYWORDS) or any(
        normalize_search_text(keyword) in surface_normalized for keyword in APPLE_NON_PHONE_QUERY_KEYWORDS
    ):
        return tuple(
            replacement
            for replacement in replacements
            if replacement not in {"iphone", "apple iphone", "苹果"}
        )
    return replacements


def apply_alias_replacements(
    surface: str,
    alias_table: Dict[str, Tuple[str, ...]],
    *,
    filter_fn: Any = None,
) -> List[str]:
    expanded: List[str] = []
    for source, replacements in alias_table.items():
        if source not in surface:
            continue
        current_replacements = filter_fn(source, replacements, surface) if filter_fn else replacements
        for replacement in current_replacements:
            replaced = normalize_query_surface(surface.replace(source, replacement))
            if replaced:
                expanded.append(replaced)
    return expanded


def build_query_variants(query: str) -> List[Tuple[str, List[str], str]]:
    surfaces: List[str] = []
    seen_surface: set[str] = set()

    def add_prepared_surface(value: str) -> None:
        normalized = re.sub(r"\s+", " ", str(value or "").strip()).strip()
        if normalized and normalized not in seen_surface:
            seen_surface.add(normalized)
            surfaces.append(normalized)

    def add_surface(value: str) -> None:
        add_prepared_surface(normalize_query_surface(value))

    add_surface(query)
    compact_surface = normalize_query_compact_surface(query)
    if compact_surface:
        add_prepared_surface(compact_surface)
    base_surfaces = list(surfaces)
    for surface in base_surfaces:
        for merged in merge_spaced_pinyin_sequences(surface):
            add_surface(merged)
        tokens = surface.split()
        corrected_tokens = [correct_query_token(token) for token in tokens]
        if corrected_tokens != tokens:
            add_surface(" ".join(corrected_tokens))

    base_surfaces = list(surfaces)
    stage_one_surfaces: List[str] = list(base_surfaces)
    for surface in base_surfaces:
        for bridge in expand_model_code_bridges(surface):
            add_surface(bridge)
            stage_one_surfaces.append(bridge)
        for bridge in expand_category_bridges(surface):
            add_surface(bridge)
            stage_one_surfaces.append(bridge)
        for replaced in apply_alias_replacements(surface, PINYIN_QUERY_ALIASES):
            add_surface(replaced)
            stage_one_surfaces.append(replaced)
        for replaced in apply_alias_replacements(
            surface,
            BRAND_QUERY_ALIASES,
            filter_fn=filter_brand_alias_replacements,
        ):
            add_surface(replaced)
            stage_one_surfaces.append(replaced)

    enriched_surfaces = list(stage_one_surfaces)
    for surface in enriched_surfaces:
        for bridge in expand_model_code_bridges(surface):
            add_surface(bridge)
            stage_one_surfaces.append(bridge)
        for bridge in expand_category_bridges(surface):
            add_surface(bridge)
            stage_one_surfaces.append(bridge)
        for replaced in apply_alias_replacements(surface, PINYIN_QUERY_ALIASES):
            add_surface(replaced)
            stage_one_surfaces.append(replaced)

    for surface in stage_one_surfaces:
        tokens = surface.split()
        for index, token in enumerate(tokens):
            if token not in SHORTHAND_TOKEN_EXPANSIONS:
                continue
            previous = tokens[index - 1] if index > 0 else ""
            if token not in FREE_SUFFIX_EXPANSIONS and not re.search(r"\d", previous):
                continue
            for replacement in SHORTHAND_TOKEN_EXPANSIONS[token]:
                expanded = tokens[:index] + replacement.split() + tokens[index + 1 :]
                add_surface(" ".join(expanded))

    variants: List[Tuple[str, List[str], str]] = []
    seen_norm: set[str] = set()
    for surface in surfaces:
        normalized = normalize_search_text(surface)
        if not normalized or normalized in seen_norm:
            continue
        tokens: List[str] = []
        for part in surface.replace("/", " ").split():
            token = normalize_search_text(part)
            if not token:
                continue
            if len(token) == 1 and token.isalpha():
                continue
            tokens.append(token)
        seen_norm.add(normalized)
        variants.append((normalized, tokens, surface))
    return variants


def detect_brand_hints(query_variants: List[Tuple[str, List[str], str]]) -> set[str]:
    combined = " ".join(surface for _, _, surface in query_variants)
    hints: set[str] = set()
    for brand_title, keywords in BRAND_HINTS.items():
        if any(keyword in combined for keyword in keywords):
            hints.add(brand_title)
    return hints


def detect_series_hints(query_variants: List[Tuple[str, List[str], str]]) -> Set[str]:
    combined = " ".join(surface for _, _, surface in query_variants)
    hints: Set[str] = set()
    for series_key, keywords in SERIES_HINT_KEYWORDS.items():
        if any(keyword in combined for keyword in keywords):
            hints.add(series_key)
    return hints


def detect_category_intents(query_variants: List[Tuple[str, List[str], str]]) -> Set[str]:
    combined = " ".join(surface for _, _, surface in query_variants)
    combined_norm = normalize_search_text(combined)
    intents: Set[str] = set()
    for category, keywords in QUERY_CATEGORY_KEYWORDS.items():
        if any(keyword in combined for keyword in keywords) or any(normalize_search_text(keyword) in combined_norm for keyword in keywords):
            intents.add(category)
    return intents


def is_bare_model_query(query_variants: List[Tuple[str, List[str], str]], brand_hints: Set[str], category_intents: Set[str]) -> bool:
    if brand_hints or category_intents:
        return False
    if not query_variants:
        return False
    primary_norm, tokens, _surface = query_variants[0]
    if not primary_norm:
        return False
    return bool(re.fullmatch(r"[a-z0-9]+", primary_norm)) and len(tokens) <= 3


def prepare_query_context(query: str) -> QueryContext:
    variants = build_query_variants(query)
    core_variants, capacity_hints, color_hints = build_core_query_variants(variants)
    brand_hints = detect_brand_hints(variants)
    category_intents = detect_category_intents(variants)
    series_hints = detect_series_hints(variants)
    if category_intents & NON_PHONE_CATEGORIES:
        series_hints.discard("iphone")
    return QueryContext(
        raw_query=str(query or "").strip(),
        variants=variants,
        core_variants=core_variants,
        brand_hints=brand_hints,
        series_hints=series_hints,
        category_intents=category_intents,
        bare_model_query=is_bare_model_query(variants, brand_hints, category_intents),
        capacity_hints=capacity_hints,
        color_hints=color_hints,
        only_refinement=bool((capacity_hints or color_hints) and not core_variants),
    )


def contains_any_keyword(text: str, keywords: Iterable[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def candidate_keyword_text(candidate: Dict[str, Any]) -> str:
    return " ".join(
        str(candidate.get(field) or "").strip().lower()
        for field in ("brand_title", "series_title", "family_title", "model_title")
    )


def candidate_matches_category_intents(candidate: Dict[str, Any], intents: Set[str]) -> Tuple[bool, bool]:
    if not intents:
        return True, False

    category_kind = str(candidate.get("category_kind") or "phone")
    if category_kind in intents:
        return True, True

    text = candidate_keyword_text(candidate)
    soft_match = (
        ("charger" in intents and contains_any_keyword(text, CHARGER_CATEGORY_KEYWORDS))
        or ("case" in intents and contains_any_keyword(text, CASE_CATEGORY_KEYWORDS))
        or ("accessory" in intents and contains_any_keyword(text, ACCESSORY_CATEGORY_KEYWORDS))
    )
    return soft_match, False


def compact_unique_texts(values: Iterable[Any]) -> Tuple[str, ...]:
    seen: Set[str] = set()
    results: List[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        results.append(text)
    return tuple(results)


def detail_ref_from_candidate(run_key: str, candidate: Dict[str, Any]) -> DetailRef:
    data_source = str(candidate.get("data_source") or "quote_rows").strip() or "quote_rows"
    if data_source == "external_reference":
        return DetailRef(
            data_source=data_source,
            run_key="",
            brand_title="",
            series_title="",
            family_title=str(candidate.get("external_title") or candidate.get("model_title") or "").strip(),
            condition_bucket="",
            branch_models=(),
            external_key=str(candidate.get("external_key") or "").strip(),
        )

    branch_models = compact_unique_texts(
        list(candidate.get("model_titles") or [])
        + [candidate.get("representative_model_title") or ""]
        + [candidate.get("model_title") or ""]
    )
    return DetailRef(
        data_source="quote_rows",
        run_key=str(run_key or "").strip(),
        brand_title=str(candidate.get("brand_title") or "").strip(),
        series_title=str(candidate.get("series_title") or "").strip(),
        family_title=str(candidate.get("family_title") or candidate.get("model_title") or "").strip(),
        condition_bucket=str(candidate.get("condition_bucket") or "").strip(),
        branch_models=branch_models,
        external_key="",
    )


def encode_detail_key(detail_ref: DetailRef) -> str:
    payload = {
        "v": DETAIL_REF_VERSION,
        "data_source": detail_ref.data_source,
        "run_key": detail_ref.run_key,
        "brand_title": detail_ref.brand_title,
        "series_title": detail_ref.series_title,
        "family_title": detail_ref.family_title,
        "condition_bucket": detail_ref.condition_bucket,
        "branch_models": list(detail_ref.branch_models),
        "external_key": detail_ref.external_key,
    }
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_detail_key(value: str) -> DetailRef | None:
    token = str(value or "").strip()
    if not token:
        return None
    padded = token + ("=" * (-len(token) % 4))
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error):
        return None

    if safe_int(payload.get("v")) != DETAIL_REF_VERSION:
        return None
    return DetailRef(
        data_source=str(payload.get("data_source") or "quote_rows").strip() or "quote_rows",
        run_key=str(payload.get("run_key") or "").strip(),
        brand_title=str(payload.get("brand_title") or "").strip(),
        series_title=str(payload.get("series_title") or "").strip(),
        family_title=str(payload.get("family_title") or "").strip(),
        condition_bucket=str(payload.get("condition_bucket") or "").strip(),
        branch_models=compact_unique_texts(payload.get("branch_models") or []),
        external_key=str(payload.get("external_key") or "").strip(),
    )


def detail_ref_query_payload(
    detail_ref: DetailRef,
    *,
    detail_key: str | None = None,
    refinement_query: str = "",
) -> Dict[str, Any]:
    payload = {
        "data_source": detail_ref.data_source,
        "run_key": detail_ref.run_key,
        "brand_title": detail_ref.brand_title,
        "series_title": detail_ref.series_title,
        "model_title": detail_ref.family_title,
        "family_title": detail_ref.family_title,
        "group_title": "",
        "condition_bucket": detail_ref.condition_bucket,
        "external_key": detail_ref.external_key,
        "branch_models": list(detail_ref.branch_models),
        "contract_version": DETAIL_CONTRACT_VERSION,
    }
    if detail_key:
        payload["detail_key"] = detail_key
    if str(refinement_query or "").strip():
        payload["refinement_query"] = str(refinement_query or "").strip()
    return payload


def derive_family_title(brand_title: str, series_title: str, model_title: str) -> str:
    brand = str(brand_title or "").strip()
    series = str(series_title or "").strip()
    model = str(model_title or "").strip()
    if not model:
        return model

    cleaned = re.sub(r"\s+\d+(?:\.\d+)?寸.*$", "", model).strip()
    cleaned = re.sub(r"\s+(国行|港版|美版|欧版|日版|韩版|台版|国际版|海外版).*$", "", cleaned).strip()
    if not cleaned:
        cleaned = model

    if brand == "苹果" and series.startswith("iPhone "):
        if re.match(r"^\d+", cleaned):
            return re.sub(r"^\s*iPhone\s*", "iPhone ", f"iPhone {cleaned}").strip()
        return series.strip()

    return cleaned


def split_group_spec(group_title: str) -> Tuple[str, str]:
    title = str(group_title or "").strip()
    if not title:
        return "默认规格", "未区分颜色"

    match = CAPACITY_TOKEN_RE.search(title)
    if not match:
        return "默认规格", title

    capacity = normalize_capacity_label(match.group(1))
    before = title[: match.start()].strip(" /-_+")
    after = title[match.end() :].strip(" /-_+")
    variant = f"{before}{after}".strip()
    variant = re.sub(r"^[\\/|,.;:+-]+|[\\/|,.;:+-]+$", "", variant).strip()
    variant = re.sub(r"\s{2,}", " ", variant)
    return capacity or "默认规格", variant or "标准版"


def capacity_sort_key(capacity_label: str) -> Tuple[int, int]:
    label = normalize_capacity_label(capacity_label)
    if not label:
        return (10**9, 10**9)

    match = re.match(r"^(?:(\d+)\+)?(\d+)(G|T)$", label)
    if not match:
        return (10**9 - 1, 10**9 - 1)

    memory_value = int(match.group(1) or 0)
    storage_value = int(match.group(2) or 0)
    storage_unit = match.group(3)
    storage_gb = storage_value * (1024 if storage_unit == "T" else 1)
    return (memory_value, storage_gb)


def candidate_bucket_priority(candidate: Dict[str, Any]) -> int:
    return candidate_bucket_priority_impl(candidate)


def family_identity_key(candidate: Dict[str, Any]) -> Tuple[str, str, str, str, str]:
    return (
        str(candidate.get("data_source") or "quote_rows").strip(),
        str(candidate.get("brand_title") or "").strip(),
        str(candidate.get("series_title") or "").strip(),
        str(candidate.get("family_title") or "").strip(),
        str(candidate.get("condition_bucket") or "").strip(),
    )


def model_identity_key(candidate: Dict[str, Any]) -> Tuple[str, str, str, str]:
    return (
        str(candidate.get("data_source") or "quote_rows").strip(),
        str(candidate.get("brand_title") or "").strip(),
        str(candidate.get("series_title") or "").strip(),
        str(candidate.get("family_title") or "").strip(),
    )


def candidate_preference_tuple(candidate: Dict[str, Any]) -> Tuple[int, int, int, int, str]:
    return candidate_preference_tuple_impl(candidate, safe_int=safe_int)


def hot_candidate_priority(candidate: Dict[str, Any]) -> Tuple[int, int, int, int, int, str]:
    category_kind = str(candidate.get("category_kind") or "phone")
    if category_kind == "phone":
        category_score = 500
    elif category_kind in {"tablet", "laptop", "wearable", "earbuds"}:
        category_score = 250
    else:
        category_score = 100
    return (
        category_score,
        candidate_bucket_priority(candidate),
        safe_int(candidate.get("row_count")),
        safe_int(candidate.get("variant_count")),
        safe_int(candidate.get("source_count")),
        str(candidate.get("latest_imported_at") or ""),
    )


def build_hot_query_candidates(candidates: List[Dict[str, Any]], *, limit: int = 8) -> List[Dict[str, Any]]:
    return build_hot_query_candidates_impl(candidates, limit=limit, safe_int=safe_int)


def classify_candidate_category(candidate: Dict[str, Any]) -> str:
    text = candidate_keyword_text(candidate)
    if contains_any_keyword(text, CASE_CATEGORY_KEYWORDS):
        return "case"
    if contains_any_keyword(text, CHARGER_CATEGORY_KEYWORDS):
        return "charger"
    if contains_any_keyword(text, ACCESSORY_CATEGORY_KEYWORDS):
        return "accessory"
    if any(keyword in text for keyword in ("airpods", "air pods", "耳机")):
        return "earbuds"
    if any(keyword in text for keyword in ("ipad", "matepad", "magicpad", "平板", "tab")):
        return "tablet"
    if any(keyword in text for keyword in ("macbook", "matebook", "thinkbook", "thinkpad", "yoga", "小新", "拯救者", "电脑", "笔记本")):
        return "laptop"
    if any(keyword in text for keyword in ("watch", "手表", "gps", "蜂窝")):
        return "wearable"
    return "phone"


def aggregate_model_candidates(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    aggregated: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}
    for candidate in candidates:
        if str(candidate.get("data_source") or "quote_rows").strip() != "quote_rows":
            continue
        family_title = derive_family_title(
            str(candidate.get("brand_title") or ""),
            str(candidate.get("series_title") or ""),
            str(candidate.get("model_title") or ""),
        )
        normalized_candidate = dict(candidate)
        normalized_candidate["family_title"] = family_title
        identity = model_identity_key(normalized_candidate)
        current = aggregated.get(identity)
        group_title = str(candidate.get("group_title") or "").strip()
        model_title = str(candidate.get("model_title") or "").strip()
        candidate_rank = candidate_preference_tuple(candidate)
        if current is None:
            current = {
                "data_source": "quote_rows",
                "external_key": "",
                "external_title": "",
                "external_source_title": "",
                "brand_title": str(candidate.get("brand_title") or "").strip(),
                "series_title": str(candidate.get("series_title") or "").strip(),
                "model_title": family_title,
                "family_title": family_title,
                "group_title": "",
                "condition_bucket": str(candidate.get("condition_bucket") or "").strip(),
                "row_count": 0,
                "source_count": 0,
                "min_price": 0,
                "max_price": 0,
                "latest_imported_at": "",
                "latest_gprice": "",
                "variant_titles": [],
                "variant_count": 0,
                "model_titles": [],
                "branch_count": 0,
                "representative_group_title": group_title,
                "representative_model_title": model_title,
                "representative_row_count": safe_int(candidate.get("row_count")),
                "representative_source_count": safe_int(candidate.get("source_count")),
                "representative_latest_imported_at": str(candidate.get("latest_imported_at") or ""),
                "representative_variant_count": safe_int(candidate.get("variant_count")),
                "representative_bucket_priority": candidate_bucket_priority(candidate),
                "search_text_normalized": "",
                "model_group_normalized": "",
                "family_only_normalized": "",
                "family_aliases_normalized": [],
                "branch_titles_normalized": [],
                "variant_texts_normalized": [],
                "series_only_normalized": "",
                "category_kind": "phone",
            }
            aggregated[identity] = current

        current["row_count"] += safe_int(candidate.get("row_count"))
        current["source_count"] = max(current["source_count"], safe_int(candidate.get("source_count")))
        current["latest_imported_at"] = max(str(current.get("latest_imported_at") or ""), str(candidate.get("latest_imported_at") or ""))
        current["latest_gprice"] = max(str(current.get("latest_gprice") or ""), str(candidate.get("latest_gprice") or ""))

        min_price = safe_int(candidate.get("min_price"))
        max_price = safe_int(candidate.get("max_price"))
        if min_price > 0:
            current["min_price"] = min_price if current["min_price"] <= 0 else min(current["min_price"], min_price)
        if max_price > 0:
            current["max_price"] = max(current["max_price"], max_price)

        variant_titles = current["variant_titles"]
        if group_title and group_title not in variant_titles:
            variant_titles.append(group_title)
        current["variant_count"] = len(variant_titles)
        model_titles = current["model_titles"]
        if model_title and model_title not in model_titles:
            model_titles.append(model_title)
        current["branch_count"] = len(model_titles)

        current_rank = (
            safe_int(current.get("representative_bucket_priority")),
            safe_int(current.get("representative_row_count")),
            safe_int(current.get("representative_variant_count")),
            safe_int(current.get("representative_source_count")),
            str(current.get("representative_latest_imported_at") or ""),
        )
        if candidate_rank >= current_rank:
            current["condition_bucket"] = str(candidate.get("condition_bucket") or "").strip()
            current["representative_group_title"] = group_title
            current["representative_model_title"] = model_title
            current["representative_row_count"] = safe_int(candidate.get("row_count"))
            current["representative_source_count"] = safe_int(candidate.get("source_count"))
            current["representative_latest_imported_at"] = str(candidate.get("latest_imported_at") or "")
            current["representative_variant_count"] = safe_int(candidate.get("variant_count"))
            current["representative_bucket_priority"] = candidate_bucket_priority(candidate)

    results: List[Dict[str, Any]] = []
    for item in aggregated.values():
        family_surface = normalize_query_surface(item["family_title"])
        family_alias_surfaces = [family_surface]
        compact_family_surface = normalize_query_compact_surface(item["family_title"])
        if compact_family_surface:
            family_alias_surfaces.append(compact_family_surface)
        if item["brand_title"] == "苹果" and family_surface.startswith("iphone "):
            shorthand_surface = family_surface.removeprefix("iphone ").strip()
            if shorthand_surface:
                family_alias_surfaces.append(shorthand_surface)
                compact_shorthand_surface = normalize_query_compact_surface(shorthand_surface)
                if compact_shorthand_surface:
                    family_alias_surfaces.append(compact_shorthand_surface)
            if shorthand_surface and "pro max" in shorthand_surface:
                family_alias_surfaces.append(shorthand_surface.replace("pro max", "pm"))
                family_alias_surfaces.append(shorthand_surface.replace("pro max", "pm").replace(" ", ""))
            if shorthand_surface and shorthand_surface.endswith(" pro"):
                family_alias_surfaces.append(shorthand_surface.replace(" pro", " p"))
                family_alias_surfaces.append(shorthand_surface.replace(" pro", "p"))
        if "pro max" in family_surface:
            family_alias_surfaces.append(family_surface.replace("pro max", "pm"))
            family_alias_surfaces.append(family_surface.replace("pro max", "pm").replace(" ", ""))
        if "至尊版" in family_surface:
            family_alias_surfaces.append(family_surface.replace("至尊版", "ultra"))
            family_alias_surfaces.append(family_surface.replace("至尊版", "u"))
        if "ultra" in family_surface:
            family_alias_surfaces.append(family_surface.replace("ultra", "u"))
            family_alias_surfaces.append(family_surface.replace("ultra", "u").replace(" ", ""))
        if "非凡大师" in family_surface:
            family_alias_surfaces.append(family_surface.replace("非凡大师", "rs"))
            family_alias_surfaces.append(family_surface.replace("非凡大师", "rs").replace(" ", ""))

        branch_surfaces = [
            normalize_query_surface(model_title)
            for model_title in item.get("model_titles") or []
            if normalize_query_surface(model_title)
        ]
        branch_alias_surfaces = list(branch_surfaces)
        for branch_surface in branch_surfaces:
            compact_branch_surface = normalize_query_compact_surface(branch_surface)
            if compact_branch_surface:
                branch_alias_surfaces.append(compact_branch_surface)

        variant_surfaces = [
            normalize_query_surface(variant_title)
            for variant_title in item.get("variant_titles") or []
            if normalize_query_surface(variant_title)
        ]
        item["search_text_normalized"] = normalize_search_text(
            " ".join(part for part in [item["brand_title"], item["series_title"], item["family_title"]] if part)
        )
        item["model_group_normalized"] = normalize_search_text(
            " ".join(part for part in [item["series_title"], item["family_title"]] if part)
        )
        item["family_only_normalized"] = normalize_search_text(item["family_title"])
        item["series_only_normalized"] = normalize_search_text(item["series_title"])
        item["family_aliases_normalized"] = sorted(
            {
                normalize_search_text(surface)
                for surface in family_alias_surfaces
                if normalize_search_text(surface)
            }
        )
        item["branch_titles_normalized"] = sorted(
            {
                normalize_search_text(surface)
                for surface in branch_alias_surfaces
                if normalize_search_text(surface)
            }
        )
        item["variant_texts_normalized"] = sorted(
            {
                normalize_search_text(surface)
                for surface in variant_surfaces
                if normalize_search_text(surface)
            }
        )
        item["category_kind"] = classify_candidate_category(item)
        results.append(item)
    return results


def _score_query_variant(
    query_norm: str,
    query_tokens: List[str],
    *,
    label_text: str,
    series_text: str,
    alias_texts: List[str],
    primary_texts: List[str],
    secondary_texts: List[str],
) -> int:
    score = 0

    if label_text == query_norm:
        score = max(score, 4200)
    elif label_text.endswith(query_norm):
        score = max(score, 3200)
    elif label_text.startswith(query_norm):
        score = max(score, 2850)
    elif query_norm in label_text:
        score = max(score, 2250)
    if any(text == query_norm for text in alias_texts):
        score = max(score, 3600)
    elif any(text.endswith(query_norm) for text in alias_texts):
        score = max(score, 2900)
    elif any(query_norm in text for text in alias_texts):
        score = max(score, 2100)
    if series_text == query_norm:
        score = max(score, 3000)
    elif series_text.startswith(query_norm):
        score = max(score, 2100)
    elif query_norm in series_text:
        score = max(score, 1600)

    if any(text == query_norm for text in primary_texts):
        score = max(score, 3400)
    if any(text == query_norm for text in secondary_texts):
        score = max(score, 3000)
    if any(text.startswith(query_norm) for text in primary_texts):
        score = max(score, 2500)
    elif any(query_norm in text for text in primary_texts):
        score = max(score, 2050)
    elif any(text.startswith(query_norm) for text in secondary_texts):
        score = max(score, 1650)
    elif any(query_norm in text for text in secondary_texts):
        score = max(score, 1350)

    tokens = [token for token in query_tokens if token and token != query_norm]
    if tokens:
        matched = 0
        for token in tokens:
            if any(token in text for text in primary_texts):
                score += 440
                matched += 1
            elif any(token in text for text in secondary_texts):
                score += 260
                matched += 1
        if matched == len(tokens):
            score += 320
        elif matched > 0:
            score -= (len(tokens) - matched) * 260
        elif matched == 0 and score <= 0:
            return -1
    elif score <= 0:
        return -1
    return score


def score_candidate(query: str, candidate: Dict[str, Any], context: QueryContext | None = None) -> int:
    resolved_context = context or prepare_query_context(query)
    return score_candidate_impl(
        candidate,
        context=resolved_context,
        safe_int=safe_int,
        charger_keywords=CHARGER_CATEGORY_KEYWORDS,
        case_keywords=CASE_CATEGORY_KEYWORDS,
        accessory_keywords=ACCESSORY_CATEGORY_KEYWORDS,
    )


def group_rows_by_variant(rows: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get("group_title") or "").strip()].append(row)
    return groups


def selected_window_labels(rows: List[Dict[str, Any]]) -> List[str]:
    deduped_rows = dedupe_latest_rows(rows)
    selected_rows, labels = select_recent_sample_window(numeric_rows(deduped_rows), min_samples=10, max_labels=3)
    if labels:
        return list(labels)
    fallback = sorted(
        {
            str(row.get("gprice") or "").strip()
            for row in selected_rows
            if str(row.get("gprice") or "").strip()
        },
        reverse=True,
    )
    return fallback[:3]


class QueryIndexCache:
    def __init__(self, storage: DGTeamStorage):
        self.storage = storage
        self._lock = threading.Lock()
        self._marker: Tuple[str, int, int, str, str, str, int] = ("", 0, 0, "", "", "", 0)
        self._meta: Dict[str, Any] = {}
        self._candidates: List[Dict[str, Any]] = []
        self._hot_queries: List[Dict[str, Any]] = []
        self._raw_candidates: List[Dict[str, Any]] = []

    def get_state(self) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        marker = self.storage.get_live_marker()
        latest_reference = self.storage.get_latest_reference_import_run()
        marker_key = (
            str(marker.get("run_key") or ""),
            safe_int(marker.get("quote_count")),
            safe_int(marker.get("market_snapshot_count")),
            str(marker.get("latest_imported_at") or ""),
            str(marker.get("published_at") or ""),
            str(marker.get("latest_run_event") or marker.get("latest_task_event") or ""),
            safe_int(latest_reference.get("import_id")),
        )
        with self._lock:
            if marker_key != self._marker:
                raw_candidates = self.storage.list_live_sku_candidates(marker_key[0])
                candidates = aggregate_model_candidates(raw_candidates)
                hot = build_hot_query_candidates(candidates, limit=8)
                self._marker = marker_key
                self._meta = marker
                self._candidates = candidates
                self._hot_queries = hot
                self._raw_candidates = raw_candidates
            return dict(self._meta), list(self._candidates), list(self._hot_queries), list(self._raw_candidates)


class QueryApp:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path).expanduser().resolve()
        self.storage = DGTeamStorage(self.db_path)
        self.storage.init_db()
        self.cache = QueryIndexCache(self.storage)
        self._search_cache: OrderedDict[Tuple[str, str, str, int], Dict[str, Any]] = OrderedDict()
        self._search_cache_lock = threading.Lock()
        self._branch_assembler = BranchPayloadAssembler(
            storage=self.storage,
            safe_int=safe_int,
            range_text=range_text,
            capacity_sort_key=capacity_sort_key,
            split_group_spec=split_group_spec,
            normalize_variant_title=normalize_variant_title,
            friendly_status_text=friendly_status_text,
        )

    def resolve_ui_asset_dir(self) -> Path:
        return resolve_release_ui_asset_dir(self.db_path)

    def _reference_context(self) -> Dict[str, Any]:
        latest_import = self.storage.get_latest_reference_import_run()
        if not latest_import:
            return {}
        source_hint = str(latest_import.get("source_hint") or "").strip()
        return {
            "import_id": safe_int(latest_import.get("import_id")),
            "source_name": source_hint or "外部行情图",
            "fetched_at": str(latest_import.get("finished_at") or latest_import.get("created_at") or ""),
            "source_hint": source_hint,
            "ai_model": str(latest_import.get("ai_model") or ""),
            "summary": dict(latest_import.get("summary") or {}),
        }

    def _detail_ref_for_candidate(self, run_key: str, candidate: Dict[str, Any]) -> Tuple[DetailRef, str]:
        detail_ref = detail_ref_from_candidate(run_key, candidate)
        return detail_ref, encode_detail_key(detail_ref)

    @staticmethod
    def _candidate_explain(candidate: Dict[str, Any], *, run_key: str) -> Dict[str, Any]:
        return {
            "category_kind": str(candidate.get("category_kind") or "phone"),
            "branch_count": safe_int(candidate.get("branch_count")),
            "variant_count": safe_int(candidate.get("variant_count")),
            "source_count": safe_int(candidate.get("source_count")),
            "run_key": str(run_key or ""),
        }

    def status_payload(self) -> Dict[str, Any]:
        meta, _, hot_queries, _ = self.cache.get_state()
        summary = self.storage.summary(meta.get("run_key") or "")
        run_key = str(meta.get("run_key") or "")
        return {
            "ok": True,
            "contract_version": API_CONTRACT_VERSION,
            "live": meta,
            "summary": {
                "run_key": summary.get("run_key", ""),
                "task_count": summary.get("task_count", 0),
                "quote_count": summary.get("quote_count", 0),
                "top_brands": summary.get("top_brands", {}),
            },
            "reference_import": self._reference_context(),
            "endpoint_contracts": endpoint_contracts_payload(),
            "backend_owned_logic": backend_owned_logic_payload(),
            "hot_queries": [
                {
                    "brand_title": item["brand_title"],
                    "series_title": item["series_title"],
                    "model_title": item["model_title"],
                    "condition_bucket": item["condition_bucket"],
                    "row_count": item["row_count"],
                    "variant_count": item["variant_count"],
                    "label": self._candidate_label(item),
                    "meta": self._candidate_meta(item),
                    "detail_key": self._detail_ref_for_candidate(run_key, item)[1],
                    "explain": self._candidate_explain(item, run_key=run_key),
                }
                for item in hot_queries
            ],
        }

    def search_payload(self, query: str, *, limit: int = MODEL_SUGGESTION_LIMIT) -> Dict[str, Any]:
        meta, candidates, _, _ = self.cache.get_state()
        trimmed = str(query or "").strip()
        run_key = str(meta.get("run_key") or "")
        latest_imported_at = str(meta.get("latest_imported_at") or "")
        effective_limit = effective_search_limit(limit)
        cache_key = search_cache_key(
            run_key=run_key,
            latest_imported_at=latest_imported_at,
            query=trimmed,
            limit=effective_limit,
            normalize_search_text=normalize_search_text,
        )
        with self._search_cache_lock:
            cached = self._search_cache.get(cache_key)
            if cached is not None:
                self._search_cache.move_to_end(cache_key)
                return deepcopy(cached)

        payload = build_search_response_payload(
            trimmed,
            limit=effective_limit,
            meta=meta,
            candidates=candidates,
            prepare_query_context=prepare_query_context,
            score_candidate=score_candidate,
            candidate_bucket_priority=candidate_bucket_priority,
            detail_ref_for_candidate=self._detail_ref_for_candidate,
            detail_ref_query_payload=detail_ref_query_payload,
            candidate_label=self._candidate_label,
            candidate_meta=self._candidate_meta,
            candidate_explain=self._candidate_explain,
            safe_int=safe_int,
        )
        with self._search_cache_lock:
            self._search_cache[cache_key] = deepcopy(payload)
            self._search_cache.move_to_end(cache_key)
            while len(self._search_cache) > SEARCH_CACHE_SIZE:
                self._search_cache.popitem(last=False)
        return payload

    def snapshot_payload(
        self,
        *,
        data_source: str = "quote_rows",
        external_key: str = "",
        detail_key: str = "",
        brand_title: str,
        series_title: str,
        model_title: str,
        family_title: str = "",
        group_title: str = "",
        condition_bucket: str = "",
        refinement_query: str = "",
    ) -> Dict[str, Any]:
        refinement_query = str(refinement_query or "").strip()
        detail_ref = decode_detail_key(detail_key) if detail_key else None
        if detail_key and not detail_ref:
            return snapshot_error_payload(
                code="invalid_detail_key",
                message="The requested detail key is invalid or expired.",
                query=legacy_snapshot_query(
                    data_source=data_source,
                    brand_title=brand_title,
                    series_title=series_title,
                    model_title=model_title,
                    family_title=family_title,
                    group_title=group_title,
                    condition_bucket=condition_bucket,
                    external_key=external_key,
                    detail_key=detail_key,
                    refinement_query=refinement_query,
                ),
            )

        effective_source = str(detail_ref.data_source if detail_ref else (data_source or "quote_rows")).strip() or "quote_rows"
        if effective_source == "external_reference":
            return self._external_snapshot_payload(
                external_key=str(detail_ref.external_key if detail_ref else external_key or "").strip(),
                detail_ref=detail_ref,
                refinement_query=refinement_query,
            )

        if not detail_ref and not any(str(value or "").strip() for value in (brand_title, series_title, model_title, family_title)):
            return snapshot_error_payload(
                code="missing_model_identifier",
                message="Missing model identifier for detail lookup.",
                query=legacy_snapshot_query(
                    data_source=effective_source,
                    brand_title=brand_title,
                    series_title=series_title,
                    model_title=model_title,
                    family_title=family_title,
                    group_title=group_title,
                    condition_bucket=condition_bucket,
                    external_key=external_key,
                    refinement_query=refinement_query,
                ),
            )

        return self._quote_snapshot_payload(
            brand_title=brand_title,
            series_title=series_title,
            model_title=model_title,
            family_title=family_title,
            group_title=group_title,
            condition_bucket=condition_bucket,
            detail_ref=detail_ref,
            refinement_query=refinement_query,
        )

    def _quote_snapshot_payload(
        self,
        *,
        brand_title: str,
        series_title: str,
        model_title: str,
        family_title: str = "",
        group_title: str = "",
        condition_bucket: str = "",
        detail_ref: DetailRef | None = None,
        refinement_query: str = "",
    ) -> Dict[str, Any]:
        meta, candidates, _, raw_candidates = self.cache.get_state()
        active_run_key = str(meta.get("run_key") or "")
        requested_ref = detail_ref or DetailRef(
            data_source="quote_rows",
            run_key=active_run_key,
            brand_title=str(brand_title or "").strip(),
            series_title=str(series_title or "").strip(),
            family_title=str(family_title or model_title or "").strip(),
            condition_bucket=str(condition_bucket or "").strip(),
            branch_models=(),
            external_key="",
        )
        requested_detail_key = encode_detail_key(requested_ref)
        run_key = str(requested_ref.run_key or active_run_key).strip()
        effective_brand_title = str(requested_ref.brand_title or brand_title or "").strip()
        effective_series_title = str(requested_ref.series_title or series_title or "").strip()
        effective_family_title = str(requested_ref.family_title or family_title or model_title or "").strip()
        preferred_candidate: Dict[str, Any] = {}
        if not detail_ref:
            preferred_candidate = self._resolve_preferred_quote_candidate(
                candidates,
                brand_title=effective_brand_title,
                series_title=effective_series_title,
                family_title=effective_family_title,
                condition_bucket=requested_ref.condition_bucket or condition_bucket,
            )
        effective_bucket = (
            str(requested_ref.condition_bucket or "").strip()
            or str(preferred_candidate.get("condition_bucket") or "").strip()
            or str(condition_bucket or "").strip()
        )

        matching_models = list(requested_ref.branch_models) if requested_ref.branch_models else self._resolve_family_models(
            raw_candidates,
            brand_title=effective_brand_title,
            series_title=effective_series_title,
            family_title=effective_family_title,
            condition_bucket=effective_bucket,
        )
        branch_resolution_source = "detail_key" if requested_ref.branch_models else "family_lookup"
        fallback_to_live_run = False
        if not matching_models and run_key and active_run_key and run_key != active_run_key:
            run_key = active_run_key
            fallback_to_live_run = True
            if not requested_ref.branch_models:
                matching_models = self._resolve_family_models(
                    raw_candidates,
                    brand_title=effective_brand_title,
                    series_title=effective_series_title,
                    family_title=effective_family_title,
                    condition_bucket=effective_bucket,
                )
        if not matching_models:
            return snapshot_error_payload(
                code="model_not_found",
                message="No data found for the requested model.",
                query=detail_ref_query_payload(
                    requested_ref,
                    detail_key=requested_detail_key,
                    refinement_query=refinement_query,
                ),
                resolution=quote_resolution_payload(
                    used_detail_key=bool(detail_ref),
                    requested_run_key=str(requested_ref.run_key or ""),
                    effective_run_key=run_key,
                    resolved_family_title=effective_family_title,
                    resolved_condition_bucket=effective_bucket,
                    branch_resolution_source=branch_resolution_source,
                    fallback_to_live_run=fallback_to_live_run,
                    resolved_branch_models=[],
                ),
            )

        reference_context = self._reference_context()
        reference_map = self.storage.get_external_reference_map()
        branches: List[Dict[str, Any]] = []
        all_capacity_prices: List[int] = []
        overall_latest_imported_at = ""
        selected_label_pool: List[str] = []

        for branch_model_title in matching_models:
            branch_snapshot_candidates = self._resolve_branch_snapshot_candidates(
                raw_candidates,
                run_key=run_key,
                brand_title=effective_brand_title,
                series_title=effective_series_title,
                model_title=branch_model_title,
                condition_bucket=effective_bucket,
            )
            if branch_snapshot_candidates:
                branch_payload = self._build_branch_payload_from_snapshot_candidates(
                    snapshot_candidates=branch_snapshot_candidates,
                    reference_context=reference_context,
                )
            else:
                branch_rows = self.storage.get_model_family_rows(
                    run_key=run_key,
                    brand_title=effective_brand_title,
                    series_title=effective_series_title,
                    model_title=branch_model_title,
                    condition_bucket=effective_bucket,
                )
                if not branch_rows and effective_bucket:
                    branch_rows = self.storage.get_model_family_rows(
                        run_key=run_key,
                        brand_title=effective_brand_title,
                        series_title=effective_series_title,
                        model_title=branch_model_title,
                        condition_bucket="",
                    )
                if not branch_rows:
                    continue
                branch_payload = self._build_branch_payload(
                    run_key=run_key,
                    brand_title=effective_brand_title,
                    series_title=effective_series_title,
                    model_title=branch_model_title,
                    condition_bucket=effective_bucket,
                    rows=branch_rows,
                    reference_map=reference_map,
                    reference_context=reference_context,
                )
            if not branch_payload:
                continue
            selected_label_pool.extend(branch_payload.get("selected_gprice_labels") or [])
            overall_latest_imported_at = max(overall_latest_imported_at, str(branch_payload.get("latest_imported_at") or ""))
            all_capacity_prices.extend(
                safe_int(item.get("market_price"))
                for item in branch_payload.get("capacity_groups", [])
                if safe_int(item.get("market_price")) > 0
            )
            branches.append(branch_payload)

        if not branches:
            return snapshot_error_payload(
                code="no_usable_price",
                message="No usable price is available for this model yet.",
                query=detail_ref_query_payload(
                    requested_ref,
                    detail_key=requested_detail_key,
                    refinement_query=refinement_query,
                ),
                resolution=quote_resolution_payload(
                    used_detail_key=bool(detail_ref),
                    requested_run_key=str(requested_ref.run_key or ""),
                    effective_run_key=run_key,
                    resolved_family_title=effective_family_title,
                    resolved_condition_bucket=effective_bucket,
                    branch_resolution_source=branch_resolution_source,
                    fallback_to_live_run=fallback_to_live_run,
                    resolved_branch_models=list(matching_models),
                ),
            )

        branches.sort(
            key=lambda item: (
                safe_int(item.get("sample_count")),
                safe_int(item.get("branch_rank")),
                str(item.get("latest_imported_at") or ""),
            ),
            reverse=True,
        )

        default_capacity = None
        if branches and branches[0].get("capacity_groups"):
            default_capacity = branches[0]["capacity_groups"][0]
        selected_labels = sorted({label for label in selected_label_pool if label}, reverse=True)[:3]
        reference_capacity = next(
            (
                group
                for branch in branches
                for group in branch.get("capacity_groups", [])
                if safe_int(group.get("reference_price")) > 0
            ),
            None,
        )
        resolved_ref = DetailRef(
            data_source="quote_rows",
            run_key=run_key,
            brand_title=effective_brand_title,
            series_title=effective_series_title,
            family_title=effective_family_title,
            condition_bucket=effective_bucket,
            branch_models=compact_unique_texts(branch.get("branch_title") or "" for branch in branches),
            external_key="",
        )
        resolved_detail_key = encode_detail_key(resolved_ref)

        payload = {
            "ok": True,
            "contract_version": API_CONTRACT_VERSION,
            "run_key": run_key,
            "query": detail_ref_query_payload(
                resolved_ref,
                detail_key=resolved_detail_key,
                refinement_query=refinement_query,
            ),
            "header": {
                "title": effective_family_title,
                "latest_imported_at": overall_latest_imported_at,
                "selected_gprice_labels": selected_labels,
            },
            "hero": {
                "market_price": safe_int(default_capacity.get("market_price")) if default_capacity else percentile(all_capacity_prices, 0.5),
                "sample_count": safe_int(default_capacity.get("sample_count")) if default_capacity else sum(safe_int(item.get("sample_count")) for item in branches),
                "independent_source_count": safe_int(default_capacity.get("seller_count")) if default_capacity else max(safe_int(item.get("seller_count")) for item in branches),
            },
            "market_v1": {
                "ok": True,
                "price_range": str(default_capacity.get("price_range") or range_text(all_capacity_prices)) if default_capacity else range_text(all_capacity_prices),
                "trusted_offer": {
                    "label": friendly_status_text(default_capacity.get("raw_status") or "") if default_capacity else "--",
                    "raw_status": str(default_capacity.get("raw_status") or "") if default_capacity else "",
                    "sample_count": safe_int(default_capacity.get("sample_count")) if default_capacity else 0,
                    "seller_count": safe_int(default_capacity.get("seller_count")) if default_capacity else 0,
                },
                "reference_market": {
                    "price": safe_int(reference_capacity.get("reference_price")) if reference_capacity else 0,
                    "source_name": str(reference_capacity.get("reference_source_name") or "") if reference_capacity else "",
                    "fetched_at": str(reference_capacity.get("reference_fetched_at") or "") if reference_capacity else "",
                    "sheet_name": "",
                },
                "flags": {},
            },
            "resolution": {
                "contract_version": DETAIL_CONTRACT_VERSION,
                "used_detail_key": bool(detail_ref),
                "requested_run_key": str(requested_ref.run_key or ""),
                "effective_run_key": run_key,
                "resolved_family_title": effective_family_title,
                "resolved_condition_bucket": effective_bucket,
                "branch_resolution_source": branch_resolution_source,
                "fallback_to_live_run": fallback_to_live_run,
                "resolved_branch_models": [str(branch.get("branch_title") or "") for branch in branches],
            },
            "branches": branches,
            "default_capacity": default_capacity or {},
        }
        refinement = refine_snapshot(payload, refinement_query)
        payload = refinement.snapshot
        resolution = dict(payload.get("resolution") or {})
        resolution["refinement"] = refinement_resolution_payload(refinement, refinement_query)
        payload["resolution"] = resolution
        if refinement_query:
            payload["query"] = detail_ref_query_payload(
                resolved_ref,
                detail_key=resolved_detail_key,
                refinement_query=refinement_query,
            )
        return payload

    def _resolve_preferred_quote_candidate(
        self,
        candidates: List[Dict[str, Any]],
        *,
        brand_title: str,
        series_title: str,
        family_title: str,
        condition_bucket: str = "",
    ) -> Dict[str, Any]:
        matches = [
            item
            for item in candidates
            if str(item.get("data_source") or "quote_rows").strip() == "quote_rows"
            and str(item.get("brand_title") or "").strip() == str(brand_title or "").strip()
            and str(item.get("series_title") or "").strip() == str(series_title or "").strip()
            and str(item.get("family_title") or item.get("model_title") or "").strip() == str(family_title or "").strip()
            and (
                not str(condition_bucket or "").strip()
                or str(item.get("condition_bucket") or "").strip() == str(condition_bucket or "").strip()
            )
        ]
        if not matches:
            return {}
        matches.sort(key=candidate_preference_tuple, reverse=True)
        return dict(matches[0])

    def _resolve_family_models(
        self,
        raw_candidates: List[Dict[str, Any]],
        *,
        brand_title: str,
        series_title: str,
        family_title: str,
        condition_bucket: str = "",
    ) -> List[str]:
        matched = []
        fallback = []
        for item in raw_candidates:
            if str(item.get("data_source") or "quote_rows").strip() != "quote_rows":
                continue
            if str(item.get("brand_title") or "").strip() != str(brand_title or "").strip():
                continue
            if str(item.get("series_title") or "").strip() != str(series_title or "").strip():
                continue
            item_family = derive_family_title(
                str(item.get("brand_title") or ""),
                str(item.get("series_title") or ""),
                str(item.get("model_title") or ""),
            )
            if item_family != str(family_title or "").strip():
                continue
            model_name = str(item.get("model_title") or "").strip()
            if not model_name:
                continue
            if str(item.get("condition_bucket") or "").strip() == str(condition_bucket or "").strip():
                matched.append((safe_int(item.get("row_count")), str(item.get("latest_imported_at") or ""), model_name))
            fallback.append((safe_int(item.get("row_count")), str(item.get("latest_imported_at") or ""), model_name))

        source = matched or fallback
        source.sort(key=lambda item: (item[0], item[1]), reverse=True)
        results = []
        for _, __, model_name in source:
            if model_name not in results:
                results.append(model_name)
        return results

    def _resolve_branch_snapshot_candidates(
        self,
        raw_candidates: List[Dict[str, Any]],
        *,
        run_key: str,
        brand_title: str,
        series_title: str,
        model_title: str,
        condition_bucket: str = "",
    ) -> List[Dict[str, Any]]:
        matched: List[Tuple[int, str, Dict[str, Any]]] = []
        fallback: List[Tuple[int, str, Dict[str, Any]]] = []
        for item in raw_candidates:
            if str(item.get("data_source") or "quote_rows").strip() != "quote_rows":
                continue
            item_run_key = str(item.get("run_key") or "").strip()
            if item_run_key and str(run_key or "").strip() and item_run_key != str(run_key or "").strip():
                continue
            if str(item.get("brand_title") or "").strip() != str(brand_title or "").strip():
                continue
            if str(item.get("series_title") or "").strip() != str(series_title or "").strip():
                continue
            if str(item.get("model_title") or "").strip() != str(model_title or "").strip():
                continue
            if not str(item.get("group_title") or "").strip():
                continue
            candidate = dict(item)
            rank = (safe_int(candidate.get("row_count")), str(candidate.get("latest_imported_at") or ""))
            if str(candidate.get("condition_bucket") or "").strip() == str(condition_bucket or "").strip():
                matched.append((rank[0], rank[1], candidate))
            fallback.append((rank[0], rank[1], candidate))

        source = matched or fallback
        source.sort(key=lambda item: (item[0], item[1]), reverse=True)
        results: List[Dict[str, Any]] = []
        seen_groups: Set[str] = set()
        for _, __, candidate in source:
            group_title = str(candidate.get("group_title") or "").strip()
            if group_title in seen_groups:
                continue
            seen_groups.add(group_title)
            results.append(candidate)
        return results

    def _build_branch_payload_from_snapshot_candidates(
        self,
        *,
        snapshot_candidates: List[Dict[str, Any]],
        reference_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        return self._branch_assembler.build_from_snapshot_candidates(
            snapshot_candidates=snapshot_candidates,
            reference_context=reference_context,
        )

    def _build_branch_payload(
        self,
        *,
        run_key: str,
        brand_title: str,
        series_title: str,
        model_title: str,
        condition_bucket: str,
        rows: List[Dict[str, Any]],
        reference_map: Dict[str, Any],
        reference_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        return self._branch_assembler.build_from_rows(
            run_key=run_key,
            brand_title=brand_title,
            series_title=series_title,
            model_title=model_title,
            condition_bucket=condition_bucket,
            rows=rows,
            reference_map=reference_map,
            reference_context=reference_context,
        )

    def _external_snapshot_payload(
        self,
        *,
        external_key: str,
        detail_ref: DetailRef | None = None,
        refinement_query: str = "",
    ) -> Dict[str, Any]:
        rows = self.storage.get_external_reference_rows(external_key)
        if not rows:
            return {
                "ok": False,
                "contract_version": API_CONTRACT_VERSION,
                "error": "No external reference record was found.",
                "error_code": "external_reference_not_found",
            }

        prices = [safe_int(row.get("reference_price")) for row in rows if safe_int(row.get("reference_price")) > 0]
        if not prices:
            return {
                "ok": False,
                "contract_version": API_CONTRACT_VERSION,
                "error": "The external reference record does not contain a usable price.",
                "error_code": "external_reference_no_price",
            }

        reference_context = self._reference_context()
        raw_title = str(rows[0].get("raw_title") or "").strip()
        source_title = str(rows[0].get("source_title") or "").strip()
        latest_imported_at = max((str(row.get("imported_at") or "") for row in rows), default="")
        resolved_ref = detail_ref or DetailRef(
            data_source="external_reference",
            run_key="",
            brand_title="",
            series_title=source_title,
            family_title=raw_title,
            condition_bucket="",
            branch_models=(),
            external_key=external_key,
        )
        resolved_detail_key = encode_detail_key(resolved_ref)

        return {
            "ok": True,
            "contract_version": API_CONTRACT_VERSION,
            "run_key": "",
            "query": detail_ref_query_payload(
                resolved_ref,
                detail_key=resolved_detail_key,
                refinement_query=refinement_query,
            ),
            "header": {
                "title": raw_title or "外部参考",
                "latest_imported_at": latest_imported_at,
                "selected_gprice_labels": [],
            },
            "hero": {
                "market_price": percentile(prices, 0.5),
                "sample_count": len(rows),
                "independent_source_count": len(rows),
            },
            "market_v1": {
                "ok": True,
                "price_range": range_text(prices),
                "trusted_offer": {
                    "label": source_title or "外部行情图",
                    "raw_status": "",
                    "sample_count": len(rows),
                    "seller_count": len(rows),
                },
                "reference_market": {
                    "price": percentile(prices, 0.5),
                    "source_name": source_title or str(reference_context.get("source_name") or "外部行情图"),
                    "fetched_at": latest_imported_at,
                    "sheet_name": source_title,
                },
                "flags": {},
            },
            "resolution": {
                "contract_version": DETAIL_CONTRACT_VERSION,
                "used_detail_key": bool(detail_ref),
                "requested_run_key": "",
                "effective_run_key": "",
                "resolved_family_title": raw_title,
                "resolved_condition_bucket": "",
                "branch_resolution_source": "external_reference",
                "fallback_to_live_run": False,
                "resolved_branch_models": [],
                "refinement": {
                    "requested_query": refinement_query,
                    "applied": False,
                    "reason": "unsupported_data_source" if refinement_query else "empty_query",
                    "summary": "",
                    "matched_branch_count": 0,
                    "matched_capacity_group_count": 0,
                    "matched_color_count": 0,
                },
            },
            "branches": [],
            "variants": [],
        }

    @staticmethod
    def _candidate_label(candidate: Dict[str, Any]) -> str:
        if str(candidate.get("data_source") or "") == "external_reference":
            return str(candidate.get("external_title") or candidate.get("model_title") or "").strip()
        model_title = str(candidate.get("family_title") or candidate.get("model_title") or "").strip()
        return model_title or "未命名机型"

    @staticmethod
    def _candidate_meta(candidate: Dict[str, Any]) -> str:
        if str(candidate.get("data_source") or "") == "external_reference":
            source_title = str(candidate.get("external_source_title") or "").strip() or "外部行情图"
            count = safe_int(candidate.get("row_count"))
            return f"{source_title} / {count} 条参考"

        brand_title = str(candidate.get("brand_title") or "").strip()
        series_title = str(candidate.get("series_title") or "").strip()
        return " / ".join(part for part in [brand_title, series_title] if part)


def json_response(
    handler: SimpleHTTPRequestHandler,
    payload: Dict[str, Any],
    *,
    status: int = 200,
    headers: Dict[str, str] | None = None,
) -> None:
    return runtime_json_response(handler, payload, status=status, headers=headers)


def html_response(
    handler: SimpleHTTPRequestHandler,
    body_text: str,
    *,
    status: int = 200,
    headers: Dict[str, str] | None = None,
) -> None:
    return runtime_html_response(handler, body_text, status=status, headers=headers)


def is_client_disconnect_error(exc: BaseException | None) -> bool:
    return runtime_is_client_disconnect_error(exc)


def is_quiet_404_path(path: str) -> bool:
    normalized = str(path or "").strip()
    return any(normalized.startswith(prefix) for prefix in QUIET_404_PREFIXES)


def build_handler(app: QueryApp):
    return build_runtime_handler(app, api_error_cls=APIError)


class QuietThreadingHTTPServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address) -> None:  # type: ignore[override]
        return RuntimeQuietThreadingHTTPServer.handle_error(self, request, client_address)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DGTEAM local market query service")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    app = QueryApp(Path(args.db_path).expanduser().resolve())
    handler = build_handler(app)
    server = QuietThreadingHTTPServer((args.host, int(args.port)), handler)
    LOGGER.info("query api serving on http://%s:%s", args.host, args.port)
    LOGGER.info("query api using db: %s", Path(args.db_path).expanduser().resolve())
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
