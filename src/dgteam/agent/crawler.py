# -*- coding: utf-8 -*-
import argparse
import base64
import csv
import hashlib
import json
import os
import re
import sys
import threading
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlencode

import numpy as np
import requests
from Crypto.Cipher import DES
from Crypto.Util.Padding import pad
from PIL import Image
from playwright.sync_api import Error as PWError, sync_playwright, TimeoutError as PWTimeoutError

from dgteam.core.env import load_project_env_values

BASE = "http://web.yilaitong.net"
KEY_STR = "69cc12ce5e9d30b8a650edb5dcfbf1d5"
DES_KEY = KEY_STR[:8].encode("utf-8")
TIMEOUT_MS = 20000
SCRIPT_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = Path(__file__).resolve().parents[3]
PROJECT_ENV_VALUES = load_project_env_values(PROJECT_ROOT)


def _project_env(name: str, default: str = "") -> str:
    if name in os.environ:
        return str(os.environ.get(name, default)).strip()
    return str(PROJECT_ENV_VALUES.get(name, default)).strip()

DEFAULT_WORKSPACE_DIR = PROJECT_ROOT / "runtime" / "local" / "runs" / "dgteam_batch_output"
DEFAULT_CATALOG_CACHE_DIR = PROJECT_ROOT / "runtime" / "local" / "cache"
DEFAULT_OCR_CACHE_FILE = DEFAULT_CATALOG_CACHE_DIR / "ocr_price_cache.jsonl"
DEFAULT_PROFILE_DIR = PROJECT_ROOT / "runtime" / "local" / "browser_profile"
DEFAULT_HISTORY_DAYS = 3
DEFAULT_DELAY = 0.05
DEFAULT_LOGIN_USERNAME = ""
DEFAULT_LOGIN_PASSWORD = ""
DEFAULT_LOGIN_CONFIG_FILE = _project_env("DGTEAM_AUTH_FILE", "")
DEFAULT_MAX_LOGIN_RETRIES = 3
DEFAULT_BLACKLIST_PATH = str(PROJECT_ROOT / "config" / "model_blacklist.csv")
DEFAULT_REQUEST_WORKERS = 1
DEFAULT_REQUEST_RETRIES = 1
DEFAULT_GAP_REPLAY_PASSES = 1
DEFAULT_SKIP_EVENT_SAMPLE_SIZE = 20
DEFAULT_PER_GROUP_VALID_LIMIT = 10
DEFAULT_PROCESS_WORKERS = max(1, min(4, os.cpu_count() or 1))
DEFAULT_SYSTEM_ROOT = _project_env("DGTEAM_SYSTEM_ROOT", str(PROJECT_ROOT))
DEFAULT_RULES_PATH = str(Path(DEFAULT_SYSTEM_ROOT) / "rules" / "default_rules.json")
DEFAULT_SQLITE_DB_PATH = str(Path(DEFAULT_SYSTEM_ROOT) / "runtime" / "local" / "data" / "dgteam.db")
TASK_META_FIELDS = (
    "brand_id",
    "brand_title",
    "series_id",
    "series_title",
    "model_id",
    "model_title",
    "city_id",
    "city_title",
)
MIN_DELAY_SECONDS = 0.05
MAX_DELAY_SECONDS = 1.5
DELAY_BACKOFF_FACTOR = 2.0
DELAY_RECOVERY_FACTOR = 0.9
SUMMARY_FLUSH_EVERY = 25
SUMMARY_FLUSH_INTERVAL_SEC = 5.0
REQUEST_THREAD_LOCAL = threading.local()
SUCCESS_API_CODES = {"1", "2"}
SESSION_EXPIRED_API_CODES = {"6", "7"}
RETRYABLE_API_CODES = {"3"}

LOCAL_SOURCE_ROOT = PROJECT_ROOT / "src"
if LOCAL_SOURCE_ROOT.exists():
    local_source_root_str = str(LOCAL_SOURCE_ROOT)
    if local_source_root_str not in sys.path:
        sys.path.insert(0, local_source_root_str)

try:
    from dgteam.core.project_config import build_auth_error_message, load_auth_config as load_safe_auth_config
except Exception:
    build_auth_error_message = None
    load_safe_auth_config = None


def configure_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


configure_stdio()

APPLE_ALLOWED_DSTATUS_KEYWORD = "\u516c\u53f8\u7eaf\u539f\u5c01"
APPLE_EXCLUDED_DSTATUS_KEYWORDS = (
    "\u9884\u6fc0\u6d3b",
    "AC+",
    "\u6fc0\u6d3b",
    "\u62c6\u5c01",
    "\u5305\u88c5\u7455\u75b5",
    "\u6f14\u793a",
    "\u5b98\u6362",
    "\u8d44\u6e90\u673a",
    "\u76d1\u7ba1\u673a",
    "\u673a\u5668\u7455\u75b5",
    "\u63d0\u4f9b\u6fc0\u6d3b\u7167\u7247",
    "\u5f53\u5929\u6fc0\u6d3b\u53d1\u51fa",
    "\u5f53\u9762\u6fc0\u6d3b",
    "\u73b0\u8d27\u5f53\u9762\u6fc0\u6d3b",
    "\u76d2\u635f",
    "\u7455\u75b5",
)
NON_APPLE_EXCLUDED_DSTATUS_KEYWORDS = (
    "\u65f6\u4ef7",
    "\u6fc0\u6d3b",
    "\u9884\u6fc0\u6d3b",
    "AC+",
    "\u62c6\u5c01",
    "\u5f00\u5c01",
    "\u63d0\u4f9b\u6fc0\u6d3b\u7167\u7247",
    "\u5f53\u5929\u6fc0\u6d3b\u53d1\u51fa",
    "\u5f53\u9762\u6fc0\u6d3b",
    "\u73b0\u8d27\u5f53\u9762\u6fc0\u6d3b",
    "\u6f14\u793a",
    "\u5b98\u6362",
    "\u8d44\u6e90\u673a",
)
DEFAULT_GLOBAL_EXCLUDED_DSTATUS_KEYWORDS = APPLE_EXCLUDED_DSTATUS_KEYWORDS + NON_APPLE_EXCLUDED_DSTATUS_KEYWORDS + (
    "\u7279\u5b9a\u533a\u57df\u9500\u552e",
    "\u533a\u57df\u9500\u552e",
    "\u7701\u5185",
    "\u7981\u6b62\u51fa\u7701",
    "\u7981\u51fa\u7ebf\u4e0a",
    "\u672c\u5730",
    "\u540c\u57ce",
)

# Price images use a fixed bitmap font, so template matching is much more
# reliable than generic OCR for the tiny 16x15/24x15/32x15/40x15 PNGs.
PRICE_DIGIT_TEMPLATES = {
    "0": (
        "..###..",
        ".#####.",
        "###.###",
        "##...##",
        "##...##",
        "##...##",
        "##...##",
        "##...##",
        "##...##",
        "###.###",
        ".#####.",
        "..###..",
    ),
    "1": (
        "..##",
        ".###",
        "####",
        "####",
        "..##",
        "..##",
        "..##",
        "..##",
        "..##",
        "..##",
        "..##",
        "..##",
    ),
    "2": (
        ".#####.",
        "#######",
        "##...##",
        ".....##",
        ".....##",
        "..#####",
        ".#####.",
        "###....",
        "##.....",
        "##.....",
        "#######",
        "#######",
    ),
    "3": (
        ".#####.",
        "#######",
        "##...##",
        ".....##",
        ".....##",
        "..####.",
        "..####.",
        ".....##",
        ".....##",
        "##...##",
        "#######",
        ".#####.",
    ),
    "4": (
        ".....#.",
        "....##.",
        "...###.",
        "..####.",
        ".#####.",
        "###.##.",
        "#######",
        "#######",
        "....##.",
        "....##.",
        "....##.",
        "....##.",
    ),
    "5": (
        "#######",
        "#######",
        "##.....",
        "##.....",
        "##.....",
        "######.",
        "#######",
        ".....##",
        ".....##",
        "##...##",
        "#######",
        ".#####.",
    ),
    "6": (
        "..####.",
        ".#####.",
        "###....",
        "##.....",
        "##.....",
        "######.",
        "#######",
        "##...##",
        "##...##",
        "##...##",
        "#######",
        ".#####.",
    ),
    "7": (
        "#######",
        "#######",
        "##...##",
        "....###",
        "....##.",
        "...###.",
        "...##..",
        "..###..",
        "..##...",
        "..##...",
        "..##...",
        "..##...",
    ),
    "8": (
        ".#####.",
        "#######",
        "##...##",
        "##...##",
        "##...##",
        ".#####.",
        ".#####.",
        "##...##",
        "##...##",
        "##...##",
        "#######",
        ".#####.",
    ),
    "9": (
        ".#####.",
        "#######",
        "##...##",
        "##...##",
        "##...##",
        "#######",
        ".######",
        ".....##",
        ".....##",
        "....###",
        ".#####.",
        ".####..",
    ),
}
PRICE_DIGIT_TEMPLATE_ARRAYS = {
    digit: np.array([[ch == "#" for ch in row] for row in rows], dtype=np.uint8)
    for digit, rows in PRICE_DIGIT_TEMPLATES.items()
}


class DGTeamError(Exception):
    pass


class DGTeamFatalStop(Exception):
    def __init__(self, message: str, status: str):
        super().__init__(message)
        self.status = status


def log(*parts):
    print(time.strftime("[%Y-%m-%d %H:%M:%S]"), *parts, flush=True)


def ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)


def mask_text(value: str, keep_start: int = 6, keep_end: int = 4) -> str:
    text = normalize_text(value)
    if len(text) <= keep_start + keep_end:
        return "*" * len(text)
    return f"{text[:keep_start]}...{text[-keep_end:]}"


def fallback_auth_error_message(*, include_session_hint: bool = False) -> str:
    message = (
        "Missing DGTEAM auto-login credentials. "
        "Provide both --login-username and --login-password, or set "
        "DGTEAM_LOGIN_USERNAME and DGTEAM_LOGIN_PASSWORD, or set DGTEAM_AUTH_FILE to a JSON file "
        'containing {"auth":{"username":"...","password":"..."}}.'
    )
    if include_session_hint:
        message += " You can also sign in once in the browser profile and rerun."
    return message


def get_auth_error_message(*, include_session_hint: bool = False) -> str:
    if build_auth_error_message is not None:
        return build_auth_error_message(include_session_hint=include_session_hint)
    return fallback_auth_error_message(include_session_hint=include_session_hint)


def resolve_login_credentials(username: str, password: str, config_file: str, max_login_retries: int):
    if load_safe_auth_config is None:
        cli_username = normalize_text(username)
        cli_password = normalize_text(password)
        if cli_username or cli_password:
            if not cli_username or not cli_password:
                raise DGTeamError("Both login username and password must be provided together.")
            return {
                "username": cli_username,
                "password": cli_password,
                "source": "cli",
                "has_credentials": True,
                "max_login_retries": int(max_login_retries),
            }
        env_username = normalize_text(os.environ.get("DGTEAM_LOGIN_USERNAME", ""))
        env_password = normalize_text(os.environ.get("DGTEAM_LOGIN_PASSWORD", ""))
        if env_username or env_password:
            if not env_username or not env_password:
                raise DGTeamError(
                    "DGTEAM_LOGIN_USERNAME and DGTEAM_LOGIN_PASSWORD must both be set when using environment credentials."
                )
            return {
                "username": env_username,
                "password": env_password,
                "source": "env",
                "has_credentials": True,
                "max_login_retries": int(max_login_retries),
            }
        return {
            "username": "",
            "password": "",
            "source": "",
            "has_credentials": False,
            "max_login_retries": int(max_login_retries),
        }

    try:
        auth_config = load_safe_auth_config(
            username=username,
            password=password,
            config_file=Path(config_file).expanduser().resolve() if normalize_text(config_file) else None,
            max_login_retries=max_login_retries,
        )
    except Exception as exc:
        raise DGTeamError(str(exc)) from exc
    return {
        "username": normalize_text(getattr(auth_config, "username", "")),
        "password": normalize_text(getattr(auth_config, "password", "")),
        "source": normalize_text(getattr(auth_config, "source", "")),
        "has_credentials": bool(getattr(auth_config, "has_credentials", False)),
        "max_login_retries": int(getattr(auth_config, "max_login_retries", max_login_retries) or max_login_retries),
    }


def safe_name(text: str, max_len: int = 100) -> str:
    text = re.sub(r'[\\/:*?"<>|]+', "_", str(text))
    text = re.sub(r"\s+", "_", text).strip("._")
    return (text or "unnamed")[:max_len]


def normalize_text(s: Any) -> str:
    return str(s or "").strip()


def try_parse_json(text: str) -> Any:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def dump_debug_text(debug_dir: Path, name: str, text: str):
    ensure_dir(debug_dir)
    (debug_dir / name).write_text(text, encoding="utf-8", errors="ignore")


def cryptojs_des_ecb_base64(plain: str) -> str:
    cipher = DES.new(DES_KEY, DES.MODE_ECB)
    encrypted = cipher.encrypt(pad(plain.encode("utf-8"), 8))
    return base64.b64encode(encrypted).decode("ascii")


def load_price_mask_from_png_bytes(img_bytes: bytes) -> np.ndarray:
    rgba = np.array(Image.open(BytesIO(img_bytes)).convert("RGBA"))
    alpha = rgba[:, :, 3]
    if np.any(alpha < 255):
        return (alpha > 0).astype(np.uint8)
    rgb = rgba[:, :, :3]
    return (np.any(rgb < 250, axis=2)).astype(np.uint8)


def split_price_glyphs(mask: np.ndarray) -> List[np.ndarray]:
    if mask.ndim != 2 or not np.any(mask):
        return []

    cols = mask.any(axis=0)
    spans: List[Tuple[int, int]] = []
    start = None
    for idx, has_pixel in enumerate(cols.tolist() + [False]):
        if has_pixel and start is None:
            start = idx
        elif not has_pixel and start is not None:
            spans.append((start, idx))
            start = None

    glyphs: List[np.ndarray] = []
    for left, right in spans:
        sub = mask[:, left:right]
        rows = sub.any(axis=1)
        if not np.any(rows):
            continue
        top = int(np.argmax(rows))
        bottom = int(len(rows) - np.argmax(rows[::-1]))
        glyphs.append(sub[top:bottom, :].astype(np.uint8))
    return glyphs


def resize_price_mask(mask: np.ndarray, shape: Tuple[int, int]) -> np.ndarray:
    img = Image.fromarray((mask.astype(np.uint8) * 255), mode="L")
    img = img.resize((shape[1], shape[0]), Image.Resampling.NEAREST)
    return (np.array(img) > 127).astype(np.uint8)


def match_price_digit(glyph: np.ndarray) -> Tuple[str, float]:
    best_digit = ""
    best_score = -1.0
    for digit, templ in PRICE_DIGIT_TEMPLATE_ARRAYS.items():
        candidate = glyph if glyph.shape == templ.shape else resize_price_mask(glyph, templ.shape)
        diff_ratio = float(np.mean(candidate != templ))
        score = 1.0 - diff_ratio
        if score > best_score:
            best_digit = digit
            best_score = score
    return best_digit, best_score


def ocr_price_png_bytes(img_bytes: bytes) -> Tuple[str, float]:
    try:
        glyphs = split_price_glyphs(load_price_mask_from_png_bytes(img_bytes))
        if not glyphs:
            return "", 0.0
        digits: List[str] = []
        scores: List[float] = []
        for glyph in glyphs:
            digit, score = match_price_digit(glyph)
            if not digit:
                return "", 0.0
            digits.append(digit)
            scores.append(score)
        return "".join(digits), (sum(scores) / len(scores))
    except Exception:
        return "", 0.0


def build_recent_gprice_labels(history_days: int, now: Optional[datetime] = None) -> List[str]:
    now = now or datetime.now()
    day_count = max(1, int(history_days or 1))
    return [(now - timedelta(days=offset)).strftime("%m-%d") for offset in range(day_count)]


def extract_gprice_label(gprice: Any) -> str:
    text = normalize_text(gprice)
    match = re.search(r"(\d{2}-\d{2})", text)
    return match.group(1) if match else ""


def is_apple_brand(meta: Dict[str, str]) -> bool:
    brand_title = normalize_text(meta.get("brand_title")).lower()
    return "苹果" in brand_title or brand_title == "apple"


def contains_any_keyword(text: str, keywords: Tuple[str, ...]) -> bool:
    return any(keyword and keyword in text for keyword in keywords)

def should_keep_item(
    item: Dict[str, Any],
    meta: Dict[str, str],
    allowed_gprice_labels: Set[str],
    external_rules: Optional[Dict[str, Any]] = None,
    external_should_keep=None,
) -> Tuple[bool, str]:
    if external_rules and external_should_keep:
        try:
            return external_should_keep(item, meta, external_rules, allowed_gprice_labels=allowed_gprice_labels)
        except Exception:
            pass

    gprice_text = normalize_text(item.get("GPrice"))
    gprice_label = extract_gprice_label(gprice_text)
    if not gprice_label or gprice_label not in allowed_gprice_labels:
        return False, "drop_by_date"
    if contains_any_keyword(gprice_text, ("鏃犺揣",)):
        return False, "drop_out_of_stock"

    activation = normalize_text(item.get("Activation") or item.get("activation"))
    if activation:
        return False, "drop_by_activation_field"

    dstatus = normalize_text(item.get("dstatus"))
    if contains_any_keyword(dstatus, DEFAULT_GLOBAL_EXCLUDED_DSTATUS_KEYWORDS):
        return False, "drop_by_global_dstatus"

    if is_apple_brand(meta):
        if APPLE_ALLOWED_DSTATUS_KEYWORD not in dstatus:
            return False, "drop_by_apple_dstatus"
        if contains_any_keyword(dstatus, APPLE_EXCLUDED_DSTATUS_KEYWORDS):
            return False, "drop_by_apple_dstatus"
    else:
        if contains_any_keyword(dstatus, NON_APPLE_EXCLUDED_DSTATUS_KEYWORDS):
            return False, "drop_by_non_apple_dstatus"

    return True, ""


def normalize_price_text(value: Any) -> str:
    text = normalize_text(value)
    return text.replace(",", "").replace("楼", "").replace(" ", "")


def is_valid_price_text(value: str) -> bool:
    return bool(re.fullmatch(r"\d+", value)) and int(value) > 0


class PriceOcrCache:
    def __init__(self, path: Path):
        self.path = path
        ensure_dir(path.parent)
        self.lock = threading.Lock()
        self.mapping: Dict[str, str] = {}
        self.loaded_entries = 0
        self.hits = 0
        self.misses = 0
        self.stores = 0
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except Exception:
                        continue
                    digest = normalize_text(record.get("sha1"))
                    price_text = normalize_price_text(record.get("price_text"))
                    if digest and is_valid_price_text(price_text):
                        self.mapping[digest] = price_text
            self.loaded_entries = len(self.mapping)
        self.handle = path.open("a", encoding="utf-8")

    @staticmethod
    def fingerprint_price_payload(payload: str) -> str:
        normalized_payload = re.sub(r"\s+", "", payload or "")
        return hashlib.sha1(normalized_payload.encode("utf-8")).hexdigest()

    def lookup_payload(self, payload: str) -> Tuple[str, str]:
        digest = self.fingerprint_price_payload(payload)
        with self.lock:
            cached = self.mapping.get(digest, "")
            if cached:
                self.hits += 1
            else:
                self.misses += 1
        return cached, digest

    def store_digest(self, digest: str, price_text: str):
        clean_text = normalize_price_text(price_text)
        if not digest or not is_valid_price_text(clean_text):
            return
        with self.lock:
            existing = self.mapping.get(digest)
            if existing == clean_text:
                return
            if existing and existing != clean_text:
                return
            self.mapping[digest] = clean_text
            self.handle.write(json.dumps({"sha1": digest, "price_text": clean_text}, ensure_ascii=False) + "\n")
            self.handle.flush()
            self.stores += 1

    def snapshot(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "path": str(self.path),
                "entries_loaded": int(self.loaded_entries),
                "entries_current": int(len(self.mapping)),
                "hits": int(self.hits),
                "misses": int(self.misses),
                "stores": int(self.stores),
            }

    def close(self):
        with self.lock:
            try:
                self.handle.flush()
            finally:
                self.handle.close()


def clamp_delay_seconds(value: float) -> float:
    try:
        delay = float(value)
    except Exception:
        delay = DEFAULT_DELAY
    return max(MIN_DELAY_SECONDS, delay)


def clamp_process_workers(value: int) -> int:
    try:
        workers = int(value)
    except Exception:
        workers = DEFAULT_PROCESS_WORKERS
    return max(1, workers)


def clamp_request_workers(value: int) -> int:
    try:
        workers = int(value)
    except Exception:
        workers = DEFAULT_REQUEST_WORKERS
    return max(1, workers)


def clamp_request_retries(value: int) -> int:
    try:
        retries = int(value)
    except Exception:
        retries = DEFAULT_REQUEST_RETRIES
    return max(0, retries)


def clamp_gap_replay_passes(value: int) -> int:
    try:
        passes = int(value)
    except Exception:
        passes = DEFAULT_GAP_REPLAY_PASSES
    return max(0, passes)


def resolve_max_pending_process(value: int, workers: int) -> int:
    try:
        pending = int(value)
    except Exception:
        pending = 0
    if pending <= 0:
        pending = workers * 2
    return max(workers, pending)


def increase_delay(current_delay: float, base_delay: float) -> float:
    return min(MAX_DELAY_SECONDS, max(base_delay, current_delay * DELAY_BACKOFF_FACTOR))


def recover_delay(current_delay: float, base_delay: float) -> float:
    if current_delay <= base_delay:
        return base_delay
    return max(base_delay, current_delay * DELAY_RECOVERY_FACTOR)


def extract_price_fields(
    price: Any,
    image_root: Path,
    image_name: str,
    save_image: bool = False,
    ocr_cache: Optional[PriceOcrCache] = None,
) -> Tuple[str, str, str]:
    if isinstance(price, str) and len(price) > 40:
        cached_digest = ""
        cached_text = ""
        if ocr_cache:
            cached_text, cached_digest = ocr_cache.lookup_payload(price)
            cached_text = normalize_price_text(cached_text)
            if is_valid_price_text(cached_text) and not save_image:
                return cached_text, "", ""
        try:
            img_bytes = base64.b64decode(price)
        except Exception:
            return "", "", "drop_invalid_price"

        if is_valid_price_text(cached_text):
            if save_image:
                ensure_dir(image_root)
                img_path = image_root / image_name
                if not img_path.exists():
                    img_path.write_bytes(img_bytes)
                return cached_text, str(img_path), ""
            return cached_text, "", ""

        ocr_text, _ocr_score = ocr_price_png_bytes(img_bytes)
        ocr_text = normalize_price_text(ocr_text)
        if not is_valid_price_text(ocr_text):
            return "", "", "drop_invalid_price"
        if ocr_cache:
            ocr_cache.store_digest(cached_digest or ocr_cache.fingerprint_price_payload(price), ocr_text)

        if save_image:
            ensure_dir(image_root)
            img_path = image_root / image_name
            if not img_path.exists():
                img_path.write_bytes(img_bytes)
            return ocr_text, str(img_path), ""
        return ocr_text, "", ""

    price_text = normalize_price_text(price)
    if not is_valid_price_text(price_text):
        return "", "", "drop_invalid_price"
    return price_text, "", ""


def build_task_stats_payload(stats: Dict[str, int]) -> Dict[str, int]:
    return {
        "total_rows_seen": int(stats.get("total_rows_seen", 0)),
        "row_count": int(stats.get("kept_rows", 0)),
        "drop_by_date": int(stats.get("drop_by_date", 0)),
        "drop_out_of_stock": int(stats.get("drop_out_of_stock", 0)),
        "drop_by_activation_field": int(stats.get("drop_by_activation_field", 0)),
        "drop_by_global_dstatus": int(stats.get("drop_by_global_dstatus", 0)),
        "drop_by_apple_dstatus": int(stats.get("drop_by_apple_dstatus", 0)),
        "drop_by_non_apple_dstatus": int(stats.get("drop_by_non_apple_dstatus", 0)),
        "drop_invalid_price": int(stats.get("drop_invalid_price", 0)),
    }


def build_task_record(
    *,
    key: str,
    meta: Dict[str, Any],
    status: str,
    code: str = "",
    msg: str = "",
    stats: Optional[Dict[str, int]] = None,
    error: str = "",
) -> Dict[str, Any]:
    record: Dict[str, Any] = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "key": key,
        "status": status,
        **meta,
    }
    if code:
        record["code"] = code
    if msg:
        record["msg"] = msg
    if stats is not None:
        record.update(build_task_stats_payload(stats))
    if error:
        record["error"] = error
    return record


def enrich_rows_with_classification(rows: List[Dict[str, Any]], external_rules: Optional[Dict[str, Any]] = None, external_classify=None) -> List[Dict[str, Any]]:
    if not rows or not external_rules or not external_classify:
        return rows
    enriched: List[Dict[str, Any]] = []
    for row in rows:
        try:
            derived = external_classify(row, external_rules)
            enriched.append({**row, **derived})
        except Exception:
            enriched.append(dict(row))
    return enriched


def make_filter_stats() -> Dict[str, int]:
    return {
        "total_rows_seen": 0,
        "kept_rows": 0,
        "drop_by_date": 0,
        "drop_out_of_stock": 0,
        "drop_by_activation_field": 0,
        "drop_by_global_dstatus": 0,
        "drop_by_apple_dstatus": 0,
        "drop_by_non_apple_dstatus": 0,
        "drop_invalid_price": 0,
        "skip_after_valid_sample_cap": 0,
    }


def make_run_summary() -> Dict[str, Any]:
    return {
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "finished_at": "",
        "status": "running",
        "filters": {
            "history_days": 0,
            "allowed_gprice_labels": [],
            "per_group_valid_limit": 0,
            "brand_filter": "",
            "series_filter": "",
            "model_filter": "",
            "blacklist_path": "",
            "apple_strict_target": True,
        },
        "integrations": {
            "system_root": "",
            "rules_path": "",
            "sqlite_enabled": False,
            "sqlite_db": "",
            "sqlite_run_key": "",
            "auth_source": "",
            "resume_source": "",
            "catalog_source": "",
            "catalog_cache_path": "",
        },
        "task_counts": {
            "ok": 0,
            "zero_row": 0,
            "error": 0,
            "exception": 0,
            "skipped_done": 0,
            "skipped_blacklist": 0,
            "relogin": 0,
            "persistence_error": 0,
        },
        "row_counts": {
            "kept_rows": 0,
            "total_rows_seen": 0,
            "drop_by_date": 0,
            "drop_out_of_stock": 0,
            "drop_by_activation_field": 0,
            "drop_by_global_dstatus": 0,
            "drop_by_apple_dstatus": 0,
            "drop_by_non_apple_dstatus": 0,
            "drop_invalid_price": 0,
        },
        "gap_replay": {
            "detected": 0,
            "passes_started": 0,
            "scheduled": 0,
            "resolved": 0,
            "remaining": 0,
        },
        "ocr_cache": {
            "enabled": False,
            "path": "",
            "entries_loaded": 0,
            "entries_current": 0,
            "hits": 0,
            "misses": 0,
            "stores": 0,
        },
        "api_code_counts": {},
        "current": {
            "task_total": 0,
            "processed": 0,
            "last_key": "",
            "last_model": "",
            "last_status": "",
        },
        "last_error": {},
    }


def update_run_summary(summary: Dict[str, Any], *, code: str = "", status: str = "", key: str = "", model_title: str = "", stats: Optional[Dict[str, int]] = None):
    if status:
        summary["current"]["last_status"] = status
    if key:
        summary["current"]["last_key"] = key
    if model_title:
        summary["current"]["last_model"] = model_title

    if status in summary["task_counts"]:
        summary["task_counts"][status] += 1

    if code:
        code_counts = summary["api_code_counts"]
        code_counts[code] = int(code_counts.get(code, 0)) + 1

    if stats:
        for stat_key, stat_value in stats.items():
            if stat_key in summary["row_counts"]:
                summary["row_counts"][stat_key] += int(stat_value or 0)


def write_run_summary(path: Path, summary: Dict[str, Any]):
    ensure_dir(path.parent)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def extract_items_from_json_obj(obj: Any) -> List[dict]:
    found: List[dict] = []

    def add_item(item: dict):
        iid = item.get("ID")
        title = item.get("title")
        if iid is not None and title:
            found.append({"ID": str(iid), "title": str(title)})

    def visit(node: Any):
        if isinstance(node, list):
            if node and all(isinstance(x, dict) for x in node):
                for x in node:
                    iid = x.get("ID") or x.get("id") or x.get("cid") or x.get("CID")
                    title = x.get("title") or x.get("name") or x.get("Title")
                    if iid is not None and title:
                        add_item({"ID": iid, "title": title})
                for x in node:
                    visit(x)
            else:
                for x in node:
                    visit(x)
        elif isinstance(node, dict):
            for key in ["A", "data", "list", "rows", "result", "resultData", "items"]:
                if key in node:
                    visit(node[key])
            iid = node.get("ID") or node.get("id") or node.get("cid") or node.get("CID")
            title = node.get("title") or node.get("name") or node.get("Title")
            if iid is not None and title:
                add_item({"ID": iid, "title": title})
            for v in node.values():
                if isinstance(v, (list, dict)):
                    visit(v)

    visit(obj)

    uniq: List[dict] = []
    seen = set()
    for x in found:
        key = (x["ID"], x["title"])
        if key not in seen:
            seen.add(key)
            uniq.append(x)
    return uniq


def extract_items_from_html(text: str) -> List[dict]:
    out: List[dict] = []
    text = text or ""

    for m in re.finditer(r'<option[^>]*value=["\']?([^"\'>\s]+)["\']?[^>]*>(.*?)</option>', text, flags=re.I | re.S):
        iid = normalize_text(m.group(1))
        title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if iid and title and title not in {"请选择", "全部", "不限"}:
            out.append({"ID": iid, "title": title})

    js_patterns = [
        r'"ID"\s*:\s*"?(\d+)"?[^{}]{0,120}?"title"\s*:\s*"([^"]+)"',
        r'"id"\s*:\s*"?(\d+)"?[^{}]{0,120}?"name"\s*:\s*"([^"]+)"',
        r"'ID'\s*:\s*'?(\d+)'?[^{}]{0,120}?'title'\s*:\s*'([^']+)'",
        r"'id'\s*:\s*'?(\d+)'?[^{}]{0,120}?'name'\s*:\s*'([^']+)'",
    ]
    for pat in js_patterns:
        for m in re.finditer(pat, text, flags=re.I | re.S):
            iid = normalize_text(m.group(1))
            title = normalize_text(m.group(2))
            if iid and title:
                out.append({"ID": iid, "title": title})

    uniq: List[dict] = []
    seen = set()
    for x in out:
        key = (x["ID"], x["title"])
        if key not in seen:
            seen.add(key)
            uniq.append(x)
    return uniq


def parse_items_flexible(text: str) -> List[dict]:
    data = try_parse_json(text)
    if data is not None:
        items = extract_items_from_json_obj(data)
        if items:
            return items
    return extract_items_from_html(text)


def is_context_destroyed_error(exc: Exception) -> bool:
    text = str(exc)
    markers = [
        "Execution context was destroyed",
        "Cannot find context with specified id",
        "Frame was detached",
        "Target page, context or browser has been closed",
    ]
    return any(marker in text for marker in markers)


def wait_for_page_ready(page, timeout_ms: int = 5000):
    for state in ("domcontentloaded", "load"):
        try:
            page.wait_for_load_state(state, timeout=timeout_ms)
        except Exception:
            continue


def absolute_url(url_or_path: str) -> str:
    text = normalize_text(url_or_path)
    if not text:
        return BASE
    if text.startswith("http://") or text.startswith("https://"):
        return text
    if text.startswith("/"):
        return f"{BASE}{text}"
    return f"{BASE}/{text.lstrip('/')}"


def current_login_account(local_storage: Dict[str, str]) -> str:
    uname = normalize_text(local_storage.get("uname"))
    if not uname:
        return ""
    return uname.split("_", 1)[0]


def current_login_account_masked(local_storage: Dict[str, str]) -> str:
    return mask_text(current_login_account(local_storage))


def fetch_text_in_page(page, method: str, path: str, *, params=None, data=None) -> str:
    url = f"{BASE}{path}"
    if params:
        qs = urlencode(params, doseq=True)
        if qs:
            url += ("&" if "?" in url else "?") + qs

    method_upper = method.upper()
    headers = {
        "X-Requested-With": "XMLHttpRequest",
        "Origin": BASE,
        "Referer": page.url or f"{BASE}/",
    }
    form = data if method_upper != "GET" and data else None

    try:
        resp = page.context.request.fetch(
            url,
            method=method_upper,
            headers=headers,
            form=form,
            timeout=TIMEOUT_MS,
            fail_on_status_code=False,
            max_retries=2,
        )
        return resp.text()
    except PWError as e:
        raise DGTeamError(f"In-page request failed: {method} {path} -> {e}")


def fetch_json_in_page(page, method: str, path: str, *, params=None, data=None) -> Any:
    text = fetch_text_in_page(page, method, path, params=params, data=data)
    try:
        return json.loads(text)
    except Exception as e:
        raise DGTeamError(f"Response is not valid JSON: {path}\nPreview: {text[:500]}\nError: {e}")


def get_local_storage(page) -> Dict[str, str]:
    last_exc: Optional[Exception] = None
    for attempt in range(3):
        try:
            return page.evaluate(
                """
                () => {
                    const out = {};
                    for (let i = 0; i < localStorage.length; i++) {
                        const k = localStorage.key(i);
                        out[k] = localStorage.getItem(k);
                    }
                    return out;
                }
                """
            )
        except PWError as e:
            last_exc = e
            if not is_context_destroyed_error(e):
                raise
            wait_for_page_ready(page)
            time.sleep(0.5 * (attempt + 1))
    raise DGTeamError(f"Failed to read localStorage: {last_exc}")


def apply_login_storage(page, login_data: Dict[str, Any], username: str):
    payload = {
        "sid": normalize_text(login_data.get("SID")),
        "token": normalize_text(login_data.get("token")),
        "username": normalize_text(login_data.get("username")) or username,
        "vip": normalize_text(login_data.get("vip")),
        "baojiaID": normalize_text(login_data.get("baojiaID")),
        "sortNumber": normalize_text(login_data.get("sortNumber")),
    }
    page.evaluate(
        """
        ({ sid, token, username, vip, baojiaID, sortNumber }) => {
            localStorage.setItem("Automatic", "close");
            localStorage.setItem("SerialNumber", sid);
            localStorage.setItem("uname", username + "_123");
            localStorage.setItem("vip", vip);
            localStorage.setItem("sortAdmin", "1");
            localStorage.setItem("bjID", baojiaID);
            localStorage.setItem("token", token);
            localStorage.setItem("sortNumber", sortNumber);
            sessionStorage.setItem("SerialNumbers", sid);
            sessionStorage.setItem("vips", vip);
        }
        """,
        payload,
    )


def auto_login(page, context, username: str, password: str) -> Dict[str, str]:
    login_page_url = absolute_url("/index.php?s=/HomeV1/PCLogin/login")
    try:
        page.goto(login_page_url, wait_until="load", timeout=30000)
    except PWTimeoutError:
        wait_for_page_ready(page)

    page.wait_for_timeout(500)
    try:
        context.clear_cookies()
    except Exception:
        pass
    try:
        page.evaluate(
            """
            () => {
                localStorage.clear();
                sessionStorage.clear();
            }
            """
        )
    except Exception:
        pass

    resp = fetch_json_in_page(
        page,
        "POST",
        "/index.php/HomeV1/PCLogin/index",
        data={"UserName": username, "password": password},
    )
    if not isinstance(resp, dict):
        raise DGTeamError(f"Auto-login returned an exception: {resp}")
    if str(resp.get("code", "")) != "1":
        raise DGTeamError(f"Auto-login failed: code={resp.get('code')} msg={resp.get('message') or resp.get('msg')}")

    login_data = resp.get("data") or {}
    sid = normalize_text(login_data.get("SID"))
    token = normalize_text(login_data.get("token"))
    if not sid or not token:
        raise DGTeamError(f"Auto-login succeeded but SID/token is missing: {resp}")

    apply_login_storage(page, login_data, username)
    target_url = absolute_url(login_data.get("hrefRes") or "/index.php?s=/HomeV1/Index/supplyInfo")
    try:
        page.goto(target_url, wait_until="load", timeout=30000)
    except PWTimeoutError:
        wait_for_page_ready(page)
    page.wait_for_timeout(1000)
    return get_local_storage(page)


def write_session_meta(meta_path: Path, page, context, sid: str, token: str, local_storage: Dict[str, str]):
    cookies = get_cookie_map(context)
    account = current_login_account(local_storage)
    redacted_payload = {
        "sid_present": bool(sid),
        "sid_masked": mask_text(sid),
        "token_present": bool(token),
        "token_masked": mask_text(token),
        "account_present": bool(account),
        "account_masked": mask_text(account),
        "cookie_names": sorted(cookies.keys()),
        "local_storage_keys": sorted((local_storage or {}).keys()),
        "page_url": page.url,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "redacted": True,
    }
    meta_path.write_text(json.dumps(redacted_payload, ensure_ascii=False, indent=2), encoding="utf-8")


def refresh_session(page, context, meta_path: Path, username: str, password: str) -> Tuple[Dict[str, str], str, str]:
    local_storage = auto_login(page, context, username, password)
    sid = normalize_text(local_storage.get("SerialNumber"))
    token = normalize_text(local_storage.get("token"))
    if not sid or not token:
        raise DGTeamError("Still could not obtain SerialNumber/token after auto relogin")
    write_session_meta(meta_path, page, context, sid, token, local_storage)
    return local_storage, sid, token


def get_cookie_map(context, domain_filter: str = "web.yilaitong.net") -> Dict[str, str]:
    cookies = context.cookies()
    out: Dict[str, str] = {}
    for ck in cookies:
        domain = ck.get("domain", "")
        if domain_filter in domain:
            out[ck.get("name")] = ck.get("value")
    return out


def choose_item(items: List[dict], keyword: str, label: str) -> dict:
    if not items:
        raise DGTeamError(f"{label} list is empty")

    keyword = normalize_text(keyword)
    if not keyword:
        raise DGTeamError(f"A {label} name")

    kw = keyword.lower()
    exact_id = [x for x in items if normalize_text(x.get("ID")).lower() == kw]
    if len(exact_id) == 1:
        return exact_id[0]
    if len(exact_id) > 1:
        raise DGTeamError(f"{label} ID matched multiple results")

    exact_title = [x for x in items if normalize_text(x.get("title") or x.get("name")).lower() == kw]
    if len(exact_title) == 1:
        return exact_title[0]
    if len(exact_title) > 1:
        raise DGTeamError(f"{label} exact match returned multiple results")

    contains = [x for x in items if kw in normalize_text(x.get("title") or x.get("name")).lower()]
    if len(contains) == 1:
        return contains[0]
    if len(contains) > 1:
        raise DGTeamError(f"{label} fuzzy match returned multiple results")

    raise DGTeamError(f"Could not find {label}: {keyword}")


def fetch_brand_list(page, sid: str, token: str, debug_dir: Path) -> List[dict]:
    probes = [
        ("GET", "/index.php?s=/HomeV1/Index/topClass", {"SID": sid, "token": token}, None, "brand_topClass_get.txt"),
        ("POST", "/index.php?s=/HomeV1/Index/topClass", None, {"SID": sid, "token": token}, "brand_topClass_post.txt"),
        ("POST", "/index.php?s=/HomeV1/Index/topClassTwo", None, {"SID": sid, "token": token}, "brand_topClassTwo_post.txt"),
        ("GET", "/index.php?s=/HomeV1/Index/topClassTwo", {"SID": sid, "token": token}, None, "brand_topClassTwo_get.txt"),
        ("POST", "/index.php?s=/HomeV1/Index/selectClass_ts", None, {"SID": sid, "token": token}, "brand_selectClass_empty.txt"),
    ]

    all_items: List[dict] = []
    for method, path, params, data, fname in probes:
        try:
            text = fetch_text_in_page(page, method, path, params=params, data=data)
            dump_debug_text(debug_dir, fname, text)
            items = parse_items_flexible(text)
            if items:
                all_items.extend(items)
                log(f"Brand endpoint hit: {path} -> {len(items)} items")
        except Exception as e:
            dump_debug_text(debug_dir, fname, f"[ERROR] {type(e).__name__}: {e}")

    uniq: List[dict] = []
    seen = set()
    for x in all_items:
        key = (x["ID"], x["title"])
        if key not in seen:
            seen.add(key)
            uniq.append(x)
    return uniq


def fetch_children_list(page, sid: str, token: str, parent_id: str, level_name: str, debug_dir: Path) -> List[dict]:
    probes = [
        ("POST", "/index.php?s=/HomeV1/Index/topClassTwo", None, {"SID": sid, "token": token, "id": str(parent_id)}, f"{safe_name(level_name)}_{parent_id}_topClassTwo_post.txt"),
        ("GET", "/index.php?s=/HomeV1/Index/topClassTwo", {"SID": sid, "token": token, "id": str(parent_id)}, None, f"{safe_name(level_name)}_{parent_id}_topClassTwo_get.txt"),
        ("POST", "/index.php?s=/HomeV1/Index/selectClass_ts", None, {"SID": sid, "token": token, "id": str(parent_id)}, f"{safe_name(level_name)}_{parent_id}_selectClass.txt"),
    ]

    all_items: List[dict] = []
    for method, path, params, data, fname in probes:
        try:
            text = fetch_text_in_page(page, method, path, params=params, data=data)
            dump_debug_text(debug_dir, fname, text)
            items = parse_items_flexible(text)
            if items:
                all_items.extend(items)
        except Exception as e:
            dump_debug_text(debug_dir, fname, f"[ERROR] {type(e).__name__}: {e}")

    uniq: List[dict] = []
    seen = set()
    for x in all_items:
        key = (x["ID"], x["title"])
        if key not in seen:
            seen.add(key)
            uniq.append(x)
    return uniq


def fetch_city_list(page, sid: str) -> List[dict]:
    data = fetch_json_in_page(page, "GET", "/index.php?s=/HomeV1/Index/cityList", params={"sid": sid})
    if not isinstance(data, list):
        raise DGTeamError(f"cityList returned an error: {data}")
    return [{"ID": str(x["id"]), "title": str(x["name"])} for x in data]


def get_request_session() -> requests.Session:
    session = getattr(REQUEST_THREAD_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        session.trust_env = False
        REQUEST_THREAD_LOCAL.session = session
    return session


def build_request_auth_state(page, context, sid: str, token: str, *, generation: int = 0) -> Dict[str, Any]:
    try:
        user_agent = page.evaluate("() => navigator.userAgent")
    except Exception:
        user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        )
    return {
        "sid": sid,
        "token": token,
        "cookies": get_cookie_map(context),
        "user_agent": user_agent,
        "generation": int(generation),
    }


def fetch_quote_http(
    sid: str,
    token: str,
    final_class_id: str,
    city_id: str,
    *,
    cookies: Dict[str, str],
    user_agent: str,
) -> dict:
    ts_ms = int(time.time() * 1000)
    plain = f"{sid},{token},{final_class_id},{city_id},{ts_ms}"
    enc = cryptojs_des_ecb_base64(plain)
    resp = get_request_session().post(
        f"{BASE}/index.php?s=/HomeV1/Index/ClassListTs",
        headers={
            "X-Requested-With": "XMLHttpRequest",
            "Origin": BASE,
            "Referer": f"{BASE}/index.php?s=/HomeV1/Index/supplyInfo",
            "User-Agent": user_agent,
        },
        cookies=cookies,
        data={"ts": enc},
        timeout=TIMEOUT_MS / 1000,
    )
    try:
        data = json.loads(resp.text)
    except Exception as e:
        raise DGTeamError(f"ClassListTs response is not valid JSON: {resp.text[:500]} Error: {e}")
    if not isinstance(data, dict):
        raise DGTeamError(f"ClassListTs did not return a dict: {data}")
    return data


def append_jsonl(path: Path, obj: Dict[str, Any]):
    ensure_dir(path.parent)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def append_csv_rows(path: Path, rows: List[Dict[str, Any]], field_order: List[str]):
    ensure_dir(path.parent)
    file_exists = path.exists()
    with path.open("a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=field_order)
        if not file_exists:
            writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in field_order})


class JsonlAppender:
    def __init__(self, path: Path):
        self.path = path
        ensure_dir(path.parent)
        self.handle = path.open("a", encoding="utf-8")

    def append(self, obj: Dict[str, Any]):
        self.handle.write(json.dumps(obj, ensure_ascii=False) + "\n")

    def flush(self):
        self.handle.flush()

    def close(self):
        try:
            self.flush()
        finally:
            self.handle.close()


class CsvRowsAppender:
    def __init__(self, path: Path, field_order: List[str]):
        self.path = path
        self.field_order = field_order
        ensure_dir(path.parent)
        file_exists = path.exists() and path.stat().st_size > 0
        encoding = "utf-8" if file_exists else "utf-8-sig"
        self.handle = path.open("a", encoding=encoding, newline="")
        self.writer = csv.DictWriter(self.handle, fieldnames=field_order)
        if not file_exists:
            self.writer.writeheader()

    def append_rows(self, rows: List[Dict[str, Any]]):
        for row in rows:
            self.writer.writerow({k: row.get(k, "") for k in self.field_order})

    def flush(self):
        self.handle.flush()

    def close(self):
        try:
            self.flush()
        finally:
            self.handle.close()


class SummaryCheckpoint:
    def __init__(self, path: Path, flush_every: int = SUMMARY_FLUSH_EVERY, flush_interval_sec: float = SUMMARY_FLUSH_INTERVAL_SEC):
        self.path = path
        self.flush_every = max(1, int(flush_every))
        self.flush_interval_sec = max(0.5, float(flush_interval_sec))
        self.pending_updates = 0
        self.last_write_at = 0.0

    def maybe_write(self, summary: Dict[str, Any], force: bool = False):
        self.pending_updates += 1
        now = time.monotonic()
        if force or self.last_write_at == 0.0 or self.pending_updates >= self.flush_every or (now - self.last_write_at) >= self.flush_interval_sec:
            write_run_summary(self.path, summary)
            self.pending_updates = 0
            self.last_write_at = now

    def flush(self, summary: Dict[str, Any]):
        write_run_summary(self.path, summary)
        self.pending_updates = 0
        self.last_write_at = time.monotonic()


def flatten_quote(
    resp: dict,
    meta: Dict[str, str],
    image_root: Path,
    allowed_gprice_labels: Set[str],
    per_group_valid_limit: int = 0,
    save_price_images: bool = False,
    ocr_cache: Optional[PriceOcrCache] = None,
    external_rules: Optional[Dict[str, Any]] = None,
    external_should_keep=None,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    rows: List[Dict[str, Any]] = []
    stats = make_filter_stats()
    groups = resp.get("A") or []
    valid_limit = max(0, int(per_group_valid_limit or 0))
    for group_index, group in enumerate(groups, 1):
        group_title = group.get("title")
        items = group.get("data") or []
        if items == "":
            items = []
        if not isinstance(items, list):
            continue
        kept_valid_rows = 0
        for idx, item in enumerate(items, 1):
            stats["total_rows_seen"] += 1
            keep_item, drop_reason = should_keep_item(
                item,
                meta,
                allowed_gprice_labels,
                external_rules=external_rules,
                external_should_keep=external_should_keep,
            )
            if not keep_item:
                stats[drop_reason] += 1
                continue

            row = {
                "brand_id": meta["brand_id"],
                "brand_title": meta["brand_title"],
                "series_id": meta["series_id"],
                "series_title": meta["series_title"],
                "model_id": meta["model_id"],
                "model_title": meta["model_title"],
                "city_id": meta["city_id"],
                "city_title": meta["city_title"],
                "group_title": group_title,
                "group_order_index": group_index,
                "group_row_index": idx,
                "group_valid_rank": 0,
                "GID": item.get("GID"),
                "SID": item.get("SID"),
                "CID": item.get("CID"),
                "SNo": item.get("SNo"),
                "SName": item.get("SName"),
                "cityName": item.get("cityName"),
                "activation": item.get("activation"),
                "dstatus": item.get("dstatus"),
                "GPrice": item.get("GPrice"),
                "GPriceTwo": item.get("GPriceTwo"),
                "price_text": "",
                "price_image_file": "",
            }
            price = item.get("Price", "")
            img_name = f"{safe_name(meta['brand_title'])}__{safe_name(meta['series_title'])}__{safe_name(meta['model_title'])}__{safe_name(meta['city_title'])}__{safe_name(group_title)}__{idx}.png"
            price_text, price_image_file, price_error = extract_price_fields(
                price,
                image_root,
                img_name,
                save_image=save_price_images,
                ocr_cache=ocr_cache,
            )
            if price_error:
                stats[price_error] += 1
                continue
            kept_valid_rows += 1
            row["group_valid_rank"] = kept_valid_rows
            row["price_text"] = price_text
            row["price_image_file"] = price_image_file
            rows.append(row)
            stats["kept_rows"] += 1
            if valid_limit and kept_valid_rows >= valid_limit:
                stats["skip_after_valid_sample_cap"] += max(0, len(items) - idx)
                break
    return rows, stats


def process_quote_result(
    resp: dict,
    meta: Dict[str, str],
    image_root: Path,
    raw_root: Path,
    allowed_gprice_labels: Set[str],
    per_group_valid_limit: int = 0,
    *,
    save_price_images: bool = False,
    save_raw: bool = False,
    ocr_cache: Optional[PriceOcrCache] = None,
    external_rules: Optional[Dict[str, Any]] = None,
    external_should_keep=None,
) -> Dict[str, Any]:
    rows, stats = flatten_quote(
        resp,
        meta,
        image_root,
        allowed_gprice_labels,
        per_group_valid_limit=per_group_valid_limit,
        save_price_images=save_price_images,
        ocr_cache=ocr_cache,
        external_rules=external_rules,
        external_should_keep=external_should_keep,
    )
    if rows and save_raw:
        ensure_dir(raw_root)
        raw_name = f"{safe_name(meta['brand_title'])}__{safe_name(meta['series_title'])}__{safe_name(meta['model_title'])}__{safe_name(meta['city_title'])}.json"
        (raw_root / raw_name).write_text(json.dumps(resp, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "rows": rows,
        "stats": stats,
    }


def load_done_keys(progress_path: Path) -> set:
    done = set()
    if not progress_path.exists():
        return done
    with progress_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if obj.get("status") == "ok":
                    done.add(obj["key"])
            except Exception:
                continue
    return done


def load_done_keys_from_sqlite(storage, run_key: str) -> Set[str]:
    if not storage or not run_key:
        return set()
    try:
        return set(storage.get_task_keys_by_status(run_key, ("ok",)))
    except Exception:
        return set()


def build_planned_task(meta: Dict[str, Any], item_idx: int, run_label: str, key: str = "") -> Dict[str, Any]:
    clean_meta = {field: normalize_text(meta.get(field)) for field in TASK_META_FIELDS}
    task_key = normalize_text(key) or f"{clean_meta['model_id']}__{clean_meta['city_id']}__{run_label}"
    return {
        "item_idx": int(item_idx),
        "meta": clean_meta,
        "key": task_key,
    }


def load_schedulable_tasks_from_sqlite(storage, run_key: str, run_label: str) -> List[Dict[str, Any]]:
    if not storage or not run_key or not hasattr(storage, "get_task_payloads"):
        return []
    try:
        payloads = storage.get_task_payloads(run_key, ("pending", "error", "exception"))
    except Exception:
        return []

    tasks: List[Dict[str, Any]] = []
    for fallback_idx, payload in enumerate(payloads, 1):
        meta = {field: normalize_text(payload.get(field)) for field in TASK_META_FIELDS}
        if not meta["model_id"] or not meta["city_id"]:
            continue
        tasks.append(
            build_planned_task(
                meta,
                int(payload.get("item_idx") or fallback_idx),
                run_label,
                key=normalize_text(payload.get("key")),
            )
        )
    return tasks


def bootstrap_sqlite_progress_if_needed(storage, run_key: str, progress_path: Path) -> int:
    if not storage or not run_key:
        return 0
    try:
        if int(storage.get_task_count(run_key)) > 0:
            return 0
        if not progress_path.exists():
            return 0
        return int(storage.bootstrap_tasks_from_progress_jsonl(run_key, progress_path))
    except Exception:
        return 0


def bootstrap_sqlite_run_if_needed(storage, run_key: str, source_dir: Path, external_rules: Optional[Dict[str, Any]], run_importer=None) -> Dict[str, int]:
    result = {
        "task_count": 0,
        "quote_row_count": 0,
    }
    if not storage or not run_key or not run_importer or not external_rules:
        return result
    try:
        if int(storage.get_task_count(run_key)) > 0:
            return result
        base = Path(source_dir).resolve()
        if not (base / "progress.jsonl").exists() and not (base / "all_rows.csv").exists():
            return result
        imported = run_importer(storage, base, external_rules, run_key=run_key)
        result["task_count"] = int(getattr(imported, "task_count", 0) or 0)
        result["quote_row_count"] = int(getattr(imported, "quote_row_count", 0) or 0)
        return result
    except Exception:
        return result


def load_model_blacklist(path: Optional[Path]) -> Set[str]:
    blocked: Set[str] = set()
    if not path or not path.exists():
        return blocked

    suffix = path.suffix.lower()
    if suffix == ".json":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return blocked
        if isinstance(payload, dict):
            candidates = payload.get("model_ids") or payload.get("items") or []
        else:
            candidates = payload
        if isinstance(candidates, list):
            for item in candidates:
                if isinstance(item, dict):
                    model_id = normalize_text(item.get("model_id"))
                    enabled = compact_match_text(item.get("enabled", "1"))
                    if enabled in {"0", "false", "off", "no"}:
                        continue
                else:
                    model_id = normalize_text(item)
                if model_id:
                    blocked.add(model_id)
        return blocked

    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            sample = f.read(4096)
            f.seek(0)
            try:
                has_header = csv.Sniffer().has_header(sample) if sample.strip() else False
            except csv.Error:
                has_header = True
            if has_header:
                reader = csv.DictReader(f)
                for row in reader:
                    row = row or {}
                    model_id = normalize_text(row.get("model_id"))
                    enabled = compact_match_text(row.get("enabled", "1"))
                    if enabled in {"0", "false", "off", "no"}:
                        continue
                    if model_id:
                        blocked.add(model_id)
            else:
                reader = csv.reader(f)
                for row in reader:
                    if not row:
                        continue
                    model_id = normalize_text(row[0])
                    if model_id and not model_id.startswith("#"):
                        blocked.add(model_id)
        return blocked

    for raw in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        model_id = normalize_text(line.split(",", 1)[0])
        if model_id:
            blocked.add(model_id)
    return blocked


def exclude_blacklisted_catalog_items(
    catalog: List[Dict[str, str]],
    blocked_model_ids: Set[str],
) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    if not blocked_model_ids:
        return list(catalog), []
    allowed: List[Dict[str, str]] = []
    excluded: List[Dict[str, str]] = []
    for item in catalog:
        model_id = normalize_text(item.get("model_id"))
        if model_id and model_id in blocked_model_ids:
            excluded.append(item)
        else:
            allowed.append(item)
    return allowed, excluded


def resolve_system_root(project_root: str = "") -> Optional[Path]:
    candidates: List[Path] = []
    raw_values = [
        project_root,
        os.environ.get("DGTEAM_SYSTEM_ROOT", ""),
        DEFAULT_SYSTEM_ROOT,
        str(PROJECT_ROOT),
    ]
    seen: Set[str] = set()
    for raw in raw_values:
        if not raw:
            continue
        try:
            candidate = Path(raw).expanduser().resolve()
        except Exception:
            continue
        marker = str(candidate).lower()
        if marker in seen:
            continue
        seen.add(marker)
        candidates.append(candidate)

    for candidate in candidates:
        if (candidate / "src" / "dgteam" / "market" / "rules.py").exists():
            return candidate
    return None


def load_external_runtime(system_root: str = "", rules_file: str = "", sqlite_db: str = "", enable_sqlite: bool = True) -> Dict[str, Any]:
    runtime: Dict[str, Any] = {
        "system_root": None,
        "rules": None,
        "rules_path": None,
        "db_path": None,
        "storage": None,
        "build_recent_gprice_labels": None,
        "should_keep_crawl_item": None,
        "classify_row": None,
        "publish_live_market": None,
        "error": "",
    }
    root = resolve_system_root(system_root)
    if not root:
        runtime["error"] = "Could not find the dgteam project directory"
        return runtime

    source_root = root / "src"
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))

    try:
        from dgteam.market.rules import (
            build_recent_gprice_labels as external_build_recent_gprice_labels,
            classify_row as external_classify_row,
            load_rules as external_load_rules,
            should_keep_crawl_item as external_should_keep_crawl_item,
        )
        from dgteam.core.run_import import import_run_directory as external_import_run_directory
        from dgteam.release.live_market import publish_live_market as external_publish_live_market
        from dgteam.core.storage import DGTeamStorage as ExternalDGTeamStorage
    except Exception as e:
        runtime["error"] = f"Failed to import dgteam modules: {type(e).__name__}: {e}"
        return runtime

    rules_path = Path(rules_file).expanduser().resolve() if normalize_text(rules_file) else (root / "rules" / "default_rules.json")
    try:
        rules = external_load_rules(rules_path)
    except Exception as e:
        runtime["error"] = f"Failed to load rules: {type(e).__name__}: {e}"
        return runtime

    storage = None
    db_path = None
    if enable_sqlite:
        db_path = Path(sqlite_db).expanduser().resolve() if normalize_text(sqlite_db) else (root / "runtime" / "local" / "data" / "dgteam.db")
        try:
            storage = ExternalDGTeamStorage(db_path)
            storage.init_db()
        except Exception as e:
            runtime["error"] = f"Failed to initialize SQLite: {type(e).__name__}: {e}"
            return runtime

    runtime.update(
        {
            "system_root": root,
            "rules": rules,
            "rules_path": rules_path,
            "db_path": db_path,
            "storage": storage,
            "run_importer": external_import_run_directory,
            "build_recent_gprice_labels": external_build_recent_gprice_labels,
            "should_keep_crawl_item": external_should_keep_crawl_item,
            "classify_row": external_classify_row,
            "publish_live_market": external_publish_live_market,
            "error": "",
        }
    )
    return runtime


def compact_match_text(text: Any) -> str:
    return re.sub(r"[\s_]+", "", normalize_text(text)).lower()


def build_filter_terms(text: str) -> List[str]:
    terms: List[str] = []
    for part in re.split(r"[,\n]+", text or ""):
        compact = compact_match_text(part)
        if compact:
            terms.append(compact)
    return terms


def matches_filter(candidates: List[Any], filter_text: str) -> bool:
    terms = build_filter_terms(filter_text)
    if not terms:
        return True
    normalized_candidates = [compact_match_text(candidate) for candidate in candidates]
    return any(term and (term == candidate or term in candidate) for term in terms for candidate in normalized_candidates)


def filter_catalog_items(
    catalog: List[Dict[str, str]],
    brand_filter: str = "",
    series_filter: str = "",
    model_filter: str = "",
) -> List[Dict[str, str]]:
    filtered: List[Dict[str, str]] = []
    for item in catalog:
        if not matches_filter([item.get("brand_id", ""), item.get("brand_title", "")], brand_filter):
            continue
        if not matches_filter([item.get("series_id", ""), item.get("series_title", "")], series_filter):
            continue
        if not matches_filter([item.get("model_id", ""), item.get("model_title", "")], model_filter):
            continue
        filtered.append(item)
    return filtered


def build_catalog_cache_key(brand_filter: str = "") -> str:
    brand_value = normalize_text(brand_filter)
    if not brand_value:
        return "all"
    return f"brand_{safe_name(brand_value, 80)}"


def resolve_catalog_cache_candidates(cache_dir: Path, brand_filter: str = "") -> List[Path]:
    ensure_dir(cache_dir)
    candidates: List[Path] = []
    exact_path = cache_dir / f"catalog_{build_catalog_cache_key(brand_filter)}.json"
    candidates.append(exact_path)
    if normalize_text(brand_filter):
        fallback_full = cache_dir / "catalog_all.json"
        if fallback_full != exact_path:
            candidates.append(fallback_full)
    return candidates


def load_catalog_from_candidates(paths: List[Path]) -> Tuple[List[Dict[str, str]], Optional[Path]]:
    for path in paths:
        try:
            if not path.exists():
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                return payload, path
        except Exception:
            continue
    return [], None


def write_catalog_snapshot(path: Path, catalog: List[Dict[str, str]]):
    ensure_dir(path.parent)
    path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")


def build_task_event_sample(planned: Dict[str, Any], **extra: Any) -> Dict[str, Any]:
    payload = {
        "task_key": planned["key"],
        **planned["meta"],
    }
    payload.update({k: v for k, v in extra.items() if v not in (None, "")})
    return payload


def collect_catalog(page, sid: str, token: str, debug_dir: Path, brand_filter: str = "") -> List[Dict[str, str]]:
    brands = fetch_brand_list(page, sid, token, debug_dir)
    if brand_filter:
        brands = [choose_item(brands, brand_filter, "brand")]
    catalog: List[Dict[str, str]] = []
    for bi, brand in enumerate(brands, 1):
        log(f"[{bi}/{len(brands)}] brand: {brand['title']} ({brand['ID']})")
        series_list = fetch_children_list(page, sid, token, brand["ID"], "series", debug_dir)
        if not series_list:
            catalog.append({
                "brand_id": brand["ID"],
                "brand_title": brand["title"],
                "series_id": brand["ID"],
                "series_title": brand["title"],
                "model_id": brand["ID"],
                "model_title": brand["title"],
            })
            continue
        for series in series_list:
            model_list = fetch_children_list(page, sid, token, series["ID"], "model", debug_dir)
            if not model_list:
                catalog.append({
                    "brand_id": brand["ID"],
                    "brand_title": brand["title"],
                    "series_id": series["ID"],
                    "series_title": series["title"],
                    "model_id": series["ID"],
                    "model_title": series["title"],
                })
            else:
                for model in model_list:
                    catalog.append({
                        "brand_id": brand["ID"],
                        "brand_title": brand["title"],
                        "series_id": series["ID"],
                        "series_title": series["title"],
                        "model_id": model["ID"],
                        "model_title": model["title"],
                    })
    return catalog


def main():
    parser = argparse.ArgumentParser(description="Full quote collection script (persistent browser + resumable runs)")
    parser.add_argument("--outdir", default=str(DEFAULT_WORKSPACE_DIR), help="Output directory")
    parser.add_argument("--profile-dir", default=str(DEFAULT_PROFILE_DIR), help="Browser persistent profile directory")
    parser.add_argument("--catalog-cache-dir", default=str(DEFAULT_CATALOG_CACHE_DIR), help="Global shared catalog cache directory, default data/cache")
    parser.add_argument("--ocr-cache-file", default="", help="Precise OCR cache file, default data/cache/ocr_price_cache.jsonl")
    parser.add_argument("--brand-filter", default="", help="Fetch one brand only; accept a name or ID")
    parser.add_argument("--series-filter", default="", help="Fetch one series only; accept iPhone17 or iPhone 17")
    parser.add_argument("--model-filter", default="", help="Fetch one model by keyword")
    parser.add_argument("--login-username", default=DEFAULT_LOGIN_USERNAME, help="Auto-login username")
    parser.add_argument("--login-password", default=DEFAULT_LOGIN_PASSWORD, help="Auto-login password")
    parser.add_argument(
        "--login-config-file",
        default=DEFAULT_LOGIN_CONFIG_FILE,
        help="Optional JSON file containing auth.username/auth.password; defaults to DGTEAM_AUTH_FILE",
    )
    parser.add_argument("--max-login-retries", type=int, default=DEFAULT_MAX_LOGIN_RETRIES, help="Auto relogin retry count when the session is kicked out")
    parser.add_argument("--city", default="全国", help="Default city to fetch; defaults to Nationwide")
    parser.add_argument("--all-cities", action="store_true", help="Fetch all cities; this may be very large")
    parser.add_argument("--rebuild-catalog", action="store_true", help="Force rebuild of catalog.json")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY, help="Base delay in seconds after each quote request; automatically backs off when login is unstable")
    parser.add_argument("--max-tasks", type=int, default=0, help="Maximum number of model*city tasks; 0 means unlimited")
    parser.add_argument("--history-days", type=int, default=DEFAULT_HISTORY_DAYS, help="Only keep quote rows from the most recent N days")
    parser.add_argument("--run-date", default="", help="Resume a batch for a specific date in YYYY-MM-DD format; defaults to today")
    parser.add_argument("--headless", action="store_true", help="Run headless for long background jobs")
    parser.add_argument("--blacklist-file", default=DEFAULT_BLACKLIST_PATH, help="External blacklist file filtered by model_id; supports csv/json/txt")
    parser.add_argument("--save-raw", action="store_true", help="Save raw API JSON; disabled by default for speed")
    parser.add_argument("--save-price-images", action="store_true", help="Save price PNGs; disabled by default for speed")
    parser.add_argument(
        "--per-group-valid-limit",
        type=int,
        default=DEFAULT_PER_GROUP_VALID_LIMIT,
        help="For each page group/color block, stop after keeping the first N valid rows in original page order; 0 disables the cap",
    )
    parser.add_argument("--request-workers", type=int, default=DEFAULT_REQUEST_WORKERS, help="Number of workers for quote API requests; default 1")
    parser.add_argument("--request-retries", type=int, default=DEFAULT_REQUEST_RETRIES, help="Auto-retry count for request timeouts and similar transient errors")
    parser.add_argument("--gap-replay-passes", type=int, default=DEFAULT_GAP_REPLAY_PASSES, help="Number of replay passes for occasional gaps after the main batch; default 1")
    parser.add_argument("--process-workers", type=int, default=DEFAULT_PROCESS_WORKERS, help="Number of background workers after a quote is returned")
    parser.add_argument("--max-pending-process", type=int, default=0, help="Maximum number of queued background tasks; 0 means automatic")
    parser.add_argument("--system-root", default=DEFAULT_SYSTEM_ROOT, help="Production dgteam project directory; defaults to the dgteam workspace root")
    parser.add_argument("--rules-file", default="", help="External rules JSON file; defaults to dgteam/rules/default_rules.json")
    parser.add_argument("--sqlite-db", default="", help="SQLite database path; defaults to dgteam/runtime/local/data/dgteam.db")
    parser.add_argument("--disable-sqlite", action="store_true", help="Disable SQLite sync writes and keep only CSV/JSONL output")
    args = parser.parse_args()
    args.city = "全国"
    args.all_cities = False
    args.delay = clamp_delay_seconds(args.delay)
    args.request_workers = clamp_request_workers(args.request_workers)
    args.request_retries = clamp_request_retries(args.request_retries)
    args.gap_replay_passes = clamp_gap_replay_passes(args.gap_replay_passes)
    args.process_workers = clamp_process_workers(args.process_workers)
    args.max_pending_process = resolve_max_pending_process(args.max_pending_process, args.process_workers)
    resolved_auth = resolve_login_credentials(
        args.login_username,
        args.login_password,
        args.login_config_file,
        args.max_login_retries,
    )
    args.login_username = resolved_auth["username"]
    args.login_password = resolved_auth["password"]
    args.max_login_retries = int(resolved_auth["max_login_retries"])
    args.auth_source = resolved_auth["source"]
    args.has_auto_login_credentials = bool(resolved_auth["has_credentials"])

    outdir = Path(args.outdir).resolve()
    ensure_dir(outdir)
    debug_dir = outdir / "_debug"
    ensure_dir(debug_dir)
    raw_dir = outdir / "raw"
    image_dir = outdir / "price_images"
    catalog_cache_dir = Path(args.catalog_cache_dir).expanduser().resolve()
    ensure_dir(catalog_cache_dir)
    ocr_cache_path = Path(args.ocr_cache_file).expanduser().resolve() if normalize_text(args.ocr_cache_file) else (catalog_cache_dir / DEFAULT_OCR_CACHE_FILE.name)
    if args.save_raw:
        ensure_dir(raw_dir)
    if args.save_price_images:
        ensure_dir(image_dir)
    catalog_path = outdir / "catalog.json"
    progress_path = outdir / "progress.jsonl"
    all_quotes_jsonl = outdir / "all_quotes.jsonl"
    all_rows_csv = outdir / "all_rows.csv"
    meta_path = outdir / "session_meta.json"
    summary_path = outdir / "run_summary.json"
    blacklist_path = Path(args.blacklist_file).expanduser().resolve() if normalize_text(args.blacklist_file) else None
    blocked_model_ids = load_model_blacklist(blacklist_path)
    external_runtime = load_external_runtime(
        system_root=args.system_root,
        rules_file=args.rules_file,
        sqlite_db=args.sqlite_db,
        enable_sqlite=not args.disable_sqlite,
    )
    external_rules = external_runtime.get("rules")
    external_should_keep = external_runtime.get("should_keep_crawl_item")
    external_classify = external_runtime.get("classify_row")
    external_storage = external_runtime.get("storage")
    external_build_recent = external_runtime.get("build_recent_gprice_labels")
    external_run_importer = external_runtime.get("run_importer")
    external_publish_live_market = external_runtime.get("publish_live_market")
    sqlite_run_key = ""

    csv_fields = [
        "brand_id", "brand_title", "series_id", "series_title", "model_id", "model_title",
        "city_id", "city_title", "group_title", "group_order_index", "group_row_index", "group_valid_rank",
        "GID", "SID", "CID", "SNo", "SName",
        "cityName", "activation", "dstatus", "GPrice", "GPriceTwo", "price_text", "price_image_file",
    ]

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(Path(args.profile_dir).resolve()),
            headless=args.headless,
            ignore_https_errors=True,
            viewport={"width": 1440, "height": 900},
        )
        page = context.pages[0] if context.pages else context.new_page()

        log("Opening website:", BASE)
        try:
            page.goto(BASE, wait_until="load", timeout=30000)
        except PWTimeoutError:
            log("Homepage timed out, but the browser is already running. Continuing.")

        page.wait_for_timeout(1500)
        local_storage = get_local_storage(page)
        sid = normalize_text(local_storage.get("SerialNumber"))
        token = normalize_text(local_storage.get("token"))
        active_account = current_login_account(local_storage)

        if args.has_auto_login_credentials:
            if active_account != args.login_username or not sid or not token:
                log("Auto-login with the configured account")
                local_storage, sid, token = refresh_session(page, context, meta_path, args.login_username, args.login_password)
                active_account = current_login_account(local_storage)
            else:
                log("Target account session detected; skipping relogin")
        elif not sid or not token:
            print()
            print("No valid session found in the current profile.")
            print(get_auth_error_message(include_session_hint=True))
            print("Please sign in once in the opened browser if you want to continue without stored credentials.")
            print("After signing in, stay on the site, do not close the browser, and press Enter in the terminal to continue.")
            input("Press Enter to continue... ")
            page.wait_for_timeout(1500)
            local_storage = get_local_storage(page)
            sid = normalize_text(local_storage.get("SerialNumber"))
            token = normalize_text(local_storage.get("token"))
            if not sid or not token:
                raise DGTeamError("Still could not obtain SerialNumber/token after login")
        else:
            log("Existing login session detected; auto relogin is disabled because no credentials were configured")

        log("SID:", sid)
        log("token:", mask_text(token))
        if args.auth_source:
            log("Auth source:", args.auth_source)
        cookie_map = get_cookie_map(context)
        log("PHPSESSID:", mask_text(cookie_map.get("PHPSESSID", "")))
        write_session_meta(meta_path, page, context, sid, token, local_storage)
        auth_refresh_lock = threading.Lock()
        auth_generation = 0
        request_auth_state = build_request_auth_state(page, context, sid, token, generation=auth_generation)

        def refresh_request_auth_state(*, expected_generation: Optional[int] = None, phase: str = "relogin") -> Dict[str, Any]:
            nonlocal local_storage, sid, token, request_auth_state, auth_generation
            if not args.has_auto_login_credentials:
                raise DGTeamFatalStop(
                    f"Session expired during {phase}, but auto relogin is disabled. {get_auth_error_message(include_session_hint=True)}",
                    "stopped_session_expired",
                )
            with auth_refresh_lock:
                current_generation = int(request_auth_state.get("generation", 0))
                if expected_generation is not None and current_generation != int(expected_generation):
                    return request_auth_state
                try:
                    local_storage, sid, token = refresh_session(page, context, meta_path, args.login_username, args.login_password)
                except Exception as exc:
                    raise DGTeamFatalStop(
                        f"Auto relogin failed during {phase}: {type(exc).__name__}: {exc}",
                        "stopped_relogin_failed",
                    ) from exc
                auth_generation = current_generation + 1
                request_auth_state = build_request_auth_state(page, context, sid, token, generation=auth_generation)
                return request_auth_state
        if external_runtime.get("error"):
            log("Production infrastructure is not enabled:", external_runtime["error"])
        else:
            log("Production rules are enabled:", external_runtime.get("rules_path"))
            if external_storage:
                log("SQLite enabled:", external_runtime.get("db_path"))
            else:
                log("SQLite disabled, CSV/JSONL output only")

        if args.run_date:
            base_now = datetime.strptime(args.run_date, "%Y-%m-%d")
        else:
            base_now = datetime.now()

        all_cities = fetch_city_list(page, sid)
        cities = [choose_item(all_cities, "全国", "city")]
        history_days = args.history_days
        if external_rules and args.history_days == DEFAULT_HISTORY_DAYS:
            try:
                history_days = int(external_rules.get("crawler", {}).get("history_days", args.history_days))
            except Exception:
                history_days = args.history_days
        per_group_valid_limit = max(0, int(args.per_group_valid_limit or 0))
        if external_rules and args.per_group_valid_limit == DEFAULT_PER_GROUP_VALID_LIMIT:
            try:
                per_group_valid_limit = max(
                    0,
                    int(external_rules.get("crawler", {}).get("per_group_valid_limit", args.per_group_valid_limit)),
                )
            except Exception:
                per_group_valid_limit = max(0, int(args.per_group_valid_limit or 0))
        label_builder = external_build_recent or build_recent_gprice_labels
        allowed_gprice_label_list = list(label_builder(history_days, base_now))
        allowed_gprice_labels = set(allowed_gprice_label_list)
        run_label = base_now.strftime("%Y-%m-%d")
        sqlite_run_key = f"{safe_name(outdir.name)}__{run_label}"
        log("Pinned to Nationwide only; ignoring --city / --all-cities")
        log("Run batch date:", run_label)
        log("Allowed quote dates:", allowed_gprice_label_list)
        log("Per-group valid sample cap:", per_group_valid_limit if per_group_valid_limit else "disabled")
        log("City task count:", len(cities), "->", [c["title"] for c in cities[:10]])
        log("Base delay:", args.delay, " sec", "Save raw response:", args.save_raw, "Save price images:", args.save_price_images)
        log("Global catalog cache directory:", catalog_cache_dir)
        if blacklist_path and blocked_model_ids:
            log("Loaded blacklisted model_id count:", len(blocked_model_ids), "file:", blacklist_path)

        catalog_source = ""
        catalog_cache_used_path: Optional[Path] = None
        cache_candidates = resolve_catalog_cache_candidates(catalog_cache_dir, args.brand_filter)
        if catalog_path.exists() and not args.rebuild_catalog:
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            catalog_source = "outdir"
            log("Loaded catalog from current output directory:", len(catalog))
        else:
            catalog, catalog_cache_used_path = load_catalog_from_candidates(cache_candidates if not args.rebuild_catalog else [])
            if catalog:
                catalog_source = "global_cache"
                log("Loaded global shared catalog:", len(catalog), "file:", catalog_cache_used_path)
            else:
                catalog = collect_catalog(page, sid, token, debug_dir, args.brand_filter)
                catalog_source = "rebuilt"
                log("Catalog built:", len(catalog))
                primary_cache_path = cache_candidates[0]
                write_catalog_snapshot(primary_cache_path, catalog)
                catalog_cache_used_path = primary_cache_path
                log("Global shared catalog refreshed:", primary_cache_path)
            write_catalog_snapshot(catalog_path, catalog)
        summary_catalog_cache_path = str(catalog_cache_used_path or cache_candidates[0])

        catalog_before_filter = len(catalog)
        catalog = filter_catalog_items(
            catalog,
            brand_filter=args.brand_filter,
            series_filter=args.series_filter,
            model_filter=args.model_filter,
        )
        blacklisted_items: List[Dict[str, str]] = []
        if blocked_model_ids:
            catalog, blacklisted_items = exclude_blacklisted_catalog_items(catalog, blocked_model_ids)
            if blacklisted_items:
                log("catalog blacklist exclusions:", len(blacklisted_items), "file:", blacklist_path)
        if not catalog:
            raise DGTeamError("No fetchable models remain after filtering and blacklist checks. Please review --brand-filter / --series-filter / --model-filter / --blacklist-file.")
        if len(catalog) != catalog_before_filter:
            log("Catalog size after filtering:", len(catalog), "/", catalog_before_filter)

        planned_tasks: List[Dict[str, Any]] = []
        for item_idx, item in enumerate(catalog, 1):
            for city in cities:
                meta = {
                    **item,
                    "city_id": city["ID"],
                    "city_title": city["title"],
                }
                planned_tasks.append(build_planned_task(meta, item_idx, run_label))

        done_keys: Set[str] = set()
        scheduled_tasks: List[Dict[str, Any]] = []
        resume_source = "progress_jsonl"
        bootstrap_result = {"task_count": 0, "quote_row_count": 0}
        requeued_running = 0
        if external_storage:
            bootstrap_result = bootstrap_sqlite_run_if_needed(
                external_storage,
                sqlite_run_key,
                outdir,
                external_rules,
                run_importer=external_run_importer,
            )
            if bootstrap_result["task_count"] or bootstrap_result["quote_row_count"]:
                log(
                    "SQLite bootstrapped from previous output:",
                    "tasks",
                    bootstrap_result["task_count"],
                    "quote_rows",
                    bootstrap_result["quote_row_count"],
                )
                persist_run_event(
                    "bootstrap_import",
                    {
                        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "task_count": bootstrap_result["task_count"],
                        "quote_row_count": bootstrap_result["quote_row_count"],
                        "source_dir": str(outdir),
                    },
                    event_key=f"bootstrap_import:{sqlite_run_key}",
                )
            try:
                requeued_running = external_storage.requeue_running_tasks(sqlite_run_key)
                if requeued_running:
                    log("SQLite recovered running tasks:", requeued_running)
                    persist_run_event(
                        "requeue_running",
                        {
                            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "requeued_running": requeued_running,
                            "run_key": sqlite_run_key,
                        },
                        event_key=f"requeue_running:{sqlite_run_key}",
                    )
            except Exception as e:
                log("SQLite running-task recovery failed:", type(e).__name__, e)
            try:
                external_storage.ensure_run_tasks(
                    sqlite_run_key,
                    [
                        {
                            "item_idx": planned["item_idx"],
                            **planned["meta"],
                            "key": planned["key"],
                            "status": "pending",
                        }
                        for planned in planned_tasks
                    ],
                )
            except Exception as e:
                log("SQLite task initialization failed:", type(e).__name__, e)
            done_keys = load_done_keys_from_sqlite(external_storage, sqlite_run_key)
            scheduled_tasks = load_schedulable_tasks_from_sqlite(external_storage, sqlite_run_key, run_label)
            if scheduled_tasks or done_keys or not progress_path.exists():
                resume_source = "sqlite"
            else:
                done_keys = load_done_keys(progress_path)
                resume_source = "progress_jsonl_fallback"
        else:
            done_keys = load_done_keys(progress_path)
        if not scheduled_tasks:
            scheduled_tasks = [planned for planned in planned_tasks if planned["key"] not in done_keys]
        skipped_done_tasks = [planned for planned in planned_tasks if planned["key"] in done_keys]
        skip_event_sample_size = DEFAULT_SKIP_EVENT_SAMPLE_SIZE
        skipped_done_samples = skipped_done_tasks[:skip_event_sample_size]
        blacklisted_total_tasks = len(blacklisted_items) * len(cities)
        blacklisted_event_samples: List[Dict[str, Any]] = []
        if blacklisted_items:
            for item in blacklisted_items[:skip_event_sample_size]:
                for city in cities:
                    if len(blacklisted_event_samples) >= skip_event_sample_size:
                        break
                    meta = {
                        **item,
                        "city_id": city["ID"],
                        "city_title": city["title"],
                    }
                    blacklisted_event_samples.append(build_planned_task(meta, 0, run_label))
                if len(blacklisted_event_samples) >= skip_event_sample_size:
                    break
        log("Resume source:", resume_source, "completed tasks:", len(done_keys))
        task_total = len(planned_tasks)
        if external_storage:
            try:
                task_total = int(external_storage.get_task_count(sqlite_run_key))
            except Exception as e:
                log("Failed to read SQLite task total; falling back to in-memory planned count:", type(e).__name__, e)
        log("Total tasks:", task_total)
        base_delay = args.delay
        current_delay = base_delay
        started_tasks = 0
        completed_tasks = 0
        summary = make_run_summary()
        summary["current"]["task_total"] = task_total
        summary["task_counts"]["skipped_done"] = len(done_keys)
        summary["task_counts"]["skipped_blacklist"] = blacklisted_total_tasks
        summary["integrations"]["system_root"] = str(external_runtime.get("system_root") or "")
        summary["integrations"]["rules_path"] = str(external_runtime.get("rules_path") or "")
        summary["integrations"]["sqlite_enabled"] = bool(external_storage)
        summary["integrations"]["sqlite_db"] = str(external_runtime.get("db_path") or "")
        summary["integrations"]["sqlite_run_key"] = sqlite_run_key
        summary["integrations"]["auth_source"] = str(getattr(args, "auth_source", "") or "")
        summary["integrations"]["resume_source"] = resume_source
        summary["integrations"]["catalog_source"] = catalog_source
        summary["integrations"]["catalog_cache_path"] = summary_catalog_cache_path
        summary["filters"].update(
            {
                "history_days": int(history_days or 0),
                "allowed_gprice_labels": allowed_gprice_label_list,
                "per_group_valid_limit": int(per_group_valid_limit or 0),
                "brand_filter": str(args.brand_filter or ""),
                "series_filter": str(args.series_filter or ""),
                "model_filter": str(args.model_filter or ""),
                "blacklist_path": str(blacklist_path or ""),
            }
        )
        summary_checkpoint = SummaryCheckpoint(summary_path)
        progress_appender = JsonlAppender(progress_path)
        quotes_appender = JsonlAppender(all_quotes_jsonl)
        rows_appender = CsvRowsAppender(all_rows_csv, csv_fields)
        gap_appender = JsonlAppender(outdir / "gap_replay.jsonl")
        ocr_cache = PriceOcrCache(ocr_cache_path)
        summary["ocr_cache"]["enabled"] = True
        summary["ocr_cache"]["path"] = str(ocr_cache_path)
        summary["ocr_cache"]["entries_loaded"] = int(ocr_cache.loaded_entries)
        summary["ocr_cache"]["entries_current"] = int(ocr_cache.loaded_entries)
        request_executor = ThreadPoolExecutor(max_workers=args.request_workers, thread_name_prefix="dgreq")
        process_executor = ThreadPoolExecutor(max_workers=args.process_workers, thread_name_prefix="dgproc")
        pending_requests: Dict[Any, Dict[str, Any]] = {}
        pending_process: Dict[Any, Dict[str, Any]] = {}
        planned_task_by_key = {planned["key"]: planned for planned in planned_tasks}
        gap_state_by_key: Dict[str, Dict[str, Any]] = {}
        summary_checkpoint.flush(summary)
        log("Request workers:", args.request_workers, "Request retries:", args.request_retries, "Base delay:", args.delay, " sec")
        log("Processing workers:", args.process_workers, "Max pending:", args.max_pending_process)
        log("Gap replay passes:", args.gap_replay_passes)
        log("Precise OCR cache file:", ocr_cache_path)
        if external_storage and blacklist_path and blacklist_path.suffix.lower() == ".csv" and blacklist_path.exists():
            try:
                imported_blacklist_count = external_storage.import_blacklist_csv(blacklist_path)
                log("SQLite blacklist synced:", imported_blacklist_count)
            except Exception as e:
                log("SQLite blacklist sync failed:", type(e).__name__, e)

        def persist_run_event(event_type: str, payload: Dict[str, Any], *, event_key: str = ""):
            if not external_storage:
                return
            try:
                external_storage.append_event(sqlite_run_key, event_type, payload, event_key=event_key)
            except Exception as e:
                log("SQLite event sync failed:", event_type, type(e).__name__, e)

        def refresh_gap_replay_summary():
            summary["gap_replay"]["remaining"] = sum(1 for state in gap_state_by_key.values() if not state.get("resolved"))

        def sync_ocr_cache_summary():
            snapshot = ocr_cache.snapshot()
            summary["ocr_cache"]["enabled"] = True
            summary["ocr_cache"]["path"] = snapshot["path"]
            summary["ocr_cache"]["entries_loaded"] = snapshot["entries_loaded"]
            summary["ocr_cache"]["entries_current"] = snapshot["entries_current"]
            summary["ocr_cache"]["hits"] = snapshot["hits"]
            summary["ocr_cache"]["misses"] = snapshot["misses"]
            summary["ocr_cache"]["stores"] = snapshot["stores"]

        def append_gap_event(event_type: str, payload: Dict[str, Any], *, event_key: str = ""):
            record = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "event": event_type, **payload}
            gap_appender.append(record)
            gap_appender.flush()
            persist_run_event(event_type, record, event_key=event_key)

        def register_gap_candidate(
            planned: Dict[str, Any],
            *,
            reason: str,
            phase: str,
            error: str = "",
            code: str = "",
            msg: str = "",
            source_status: str = "exception",
        ):
            key = planned["key"]
            meta = planned["meta"]
            state = gap_state_by_key.get(key)
            is_first = state is None
            if state is None:
                state = {
                    "task_key": key,
                    "meta": meta,
                    "resolved": False,
                    "detected_reason": reason,
                    "detected_phase": phase,
                    "detected_status": source_status,
                    "replay_attempts": 0,
                }
                gap_state_by_key[key] = state
            state.update(
                {
                    "resolved": False,
                    "last_reason": reason,
                    "last_phase": phase,
                    "last_error": error,
                    "last_code": code,
                    "last_msg": msg,
                    "last_status": source_status,
                    "last_seen_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
            if is_first:
                summary["gap_replay"]["detected"] += 1
            refresh_gap_replay_summary()
            append_gap_event(
                "gap_detected",
                {
                    "task_key": key,
                    **meta,
                    "reason": reason,
                    "phase": phase,
                    "source_status": source_status,
                    "error": error,
                    "code": code,
                    "msg": msg,
                    "first_detected": is_first,
                },
                event_key=(f"gap_detected:{key}" if is_first else ""),
            )
            sync_ocr_cache_summary()
            summary_checkpoint.maybe_write(summary)

        def mark_gap_resolved(planned: Dict[str, Any], *, pass_index: int, rows_count: int):
            key = planned["key"]
            meta = planned["meta"]
            state = gap_state_by_key.get(key)
            if state and not state.get("resolved"):
                summary["gap_replay"]["resolved"] += 1
            if state is None:
                state = {"task_key": key, "meta": meta}
                gap_state_by_key[key] = state
            state.update(
                {
                    "resolved": True,
                    "resolved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "resolved_pass": int(pass_index),
                    "resolved_rows": int(rows_count),
                }
            )
            refresh_gap_replay_summary()
            append_gap_event(
                "gap_resolved",
                {
                    "task_key": key,
                    **meta,
                    "pass_index": int(pass_index),
                    "rows_count": int(rows_count),
                },
                event_key=f"gap_resolved:{key}",
            )
            sync_ocr_cache_summary()
            summary_checkpoint.maybe_write(summary)

        def mark_gap_unresolved(
            planned: Dict[str, Any],
            *,
            pass_index: int,
            reason: str,
            phase: str,
            error: str = "",
            code: str = "",
            msg: str = "",
            source_status: str = "",
        ):
            key = planned["key"]
            meta = planned["meta"]
            state = gap_state_by_key.get(key)
            if state is None:
                state = {
                    "task_key": key,
                    "meta": meta,
                    "resolved": False,
                }
                gap_state_by_key[key] = state
            state.update(
                {
                    "resolved": False,
                    "replay_attempts": max(int(state.get("replay_attempts") or 0), int(pass_index)),
                    "last_reason": reason,
                    "last_phase": phase,
                    "last_error": error,
                    "last_code": code,
                    "last_msg": msg,
                    "last_status": source_status,
                    "last_seen_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
            refresh_gap_replay_summary()
            append_gap_event(
                "gap_unresolved",
                {
                    "task_key": key,
                    **meta,
                    "pass_index": int(pass_index),
                    "reason": reason,
                    "phase": phase,
                    "source_status": source_status,
                    "error": error,
                    "code": code,
                    "msg": msg,
                },
            )
            sync_ocr_cache_summary()
            summary_checkpoint.maybe_write(summary)

        def sync_run_record():
            if not external_storage:
                return
            try:
                external_storage.upsert_run(
                    sqlite_run_key,
                    outdir,
                    json.dumps(summary, ensure_ascii=False),
                    status=str(summary.get("status", "")),
                    started_at=str(summary.get("started_at", "")),
                    finished_at=str(summary.get("finished_at", "")),
                )
            except Exception as e:
                log("SQLite run sync failed:", type(e).__name__, e)

        def persist_task_record(record: Dict[str, Any], *, write_quotes: bool = False):
            if write_quotes:
                quotes_appender.append(record)
                quotes_appender.flush()
            progress_appender.append(record)
            progress_appender.flush()
            if external_storage:
                try:
                    external_storage.upsert_task(sqlite_run_key, record, json.dumps(record, ensure_ascii=False))
                except Exception as e:
                    log("SQLite task sync failed:", type(e).__name__, e)

        def persist_quote_rows(task_key: str, rows: List[Dict[str, Any]], *, strict_sqlite: bool = False):
            if rows:
                rows_appender.append_rows(rows)
                rows_appender.flush()
            if external_storage:
                try:
                    classified_rows = enrich_rows_with_classification(rows, external_rules=external_rules, external_classify=external_classify)
                    external_storage.replace_quote_rows_for_task(sqlite_run_key, task_key, classified_rows)
                except Exception as e:
                    if strict_sqlite:
                        raise DGTeamError(f"SQLite quote_rows sync failed: {type(e).__name__}: {e}") from e
                    log("SQLite quote_rows sync failed:", type(e).__name__, e)

        def persist_ok_result(
            key: str,
            meta: Dict[str, Any],
            code: str,
            msg: str,
            rows: List[Dict[str, Any]],
            stats: Dict[str, int],
            *,
            count_task: bool,
            count_status: bool,
        ):
            ok_record = build_task_record(key=key, meta=meta, status="ok", code=code, msg=msg, stats=stats)
            persist_quote_rows(key, rows, strict_sqlite=True)
            persist_task_record(ok_record, write_quotes=True)
            done_keys.add(key)
            if count_task:
                mark_task_finished()
            if count_status:
                update_run_summary(summary, code=code, status="ok", key=key, model_title=meta["model_title"], stats=stats)
            else:
                update_run_summary(summary, key=key, model_title=meta["model_title"], stats=stats)
            sync_ocr_cache_summary()
            summary_checkpoint.maybe_write(summary)
            sync_run_record()
            return ok_record

        def persist_zero_row_result(
            key: str,
            meta: Dict[str, Any],
            code: str,
            msg: str,
            stats: Dict[str, int],
            *,
            count_task: bool,
            count_status: bool,
        ):
            zero_record = build_task_record(key=key, meta=meta, status="zero_row", code=code, msg=msg, stats=stats)
            persist_task_record(zero_record, write_quotes=True)
            persist_run_event(
                "zero_row_result",
                {
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "task_key": key,
                    **meta,
                    "code": code,
                    "msg": msg,
                    **build_task_stats_payload(stats),
                },
                event_key=f"zero_row:{key}",
            )
            if count_task:
                mark_task_finished()
            if count_status:
                update_run_summary(summary, code=code, status="zero_row", key=key, model_title=meta["model_title"], stats=stats)
            else:
                update_run_summary(summary, key=key, model_title=meta["model_title"], stats=stats)
            sync_ocr_cache_summary()
            summary_checkpoint.maybe_write(summary)
            sync_run_record()
            return zero_record

        def mark_task_running(key: str, meta: Dict[str, Any]):
            if not external_storage:
                return
            record = build_task_record(key=key, meta=meta, status="running")
            try:
                external_storage.upsert_task(sqlite_run_key, record, json.dumps(record, ensure_ascii=False))
            except Exception as e:
                log("SQLite running mark failed:", type(e).__name__, e)

        sync_run_record()
        if bootstrap_result["task_count"] or bootstrap_result["quote_row_count"]:
            persist_run_event(
                "bootstrap_import",
                {
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "task_count": int(bootstrap_result["task_count"] or 0),
                    "quote_row_count": int(bootstrap_result["quote_row_count"] or 0),
                    "source_dir": str(outdir),
                },
                event_key=f"bootstrap_import:{sqlite_run_key}",
            )
        if requeued_running:
            persist_run_event(
                "requeue_running",
                {
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "requeued_count": int(requeued_running),
                    "source_dir": str(outdir),
                },
                event_key=f"requeue_running:{sqlite_run_key}",
            )
        if skipped_done_tasks:
            persist_run_event(
                "skipped_done",
                {
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "event_mode": "aggregate",
                    "task_count": len(skipped_done_tasks),
                    "sample_count": len(skipped_done_samples),
                    "resume_source": resume_source,
                    "sample_tasks": [build_task_event_sample(planned, resume_source=resume_source) for planned in skipped_done_samples],
                },
                event_key=f"skipped_done_summary:{sqlite_run_key}",
            )
        if blacklisted_total_tasks:
            persist_run_event(
                "skipped_blacklist",
                {
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "event_mode": "aggregate",
                    "task_count": int(blacklisted_total_tasks),
                    "sample_count": len(blacklisted_event_samples),
                    "reason": "model_blacklist",
                    "sample_tasks": [build_task_event_sample(planned, reason="model_blacklist") for planned in blacklisted_event_samples],
                },
                event_key=f"skipped_blacklist_summary:{sqlite_run_key}",
            )

        def mark_task_finished():
            nonlocal completed_tasks
            completed_tasks += 1
            summary["current"]["processed"] = completed_tasks

        def finalize_processing_future(future):
            info = pending_process.pop(future)
            key = info["key"]
            meta = info["meta"]
            code = info["code"]
            msg = info["msg"]
            try:
                result = future.result()
                rows = result["rows"]
                stats = result["stats"]
                if not rows:
                    persist_zero_row_result(key, meta, code, msg, stats, count_task=True, count_status=True)
                    register_gap_candidate(
                        planned_task_by_key[key],
                        reason="zero_rows",
                        phase="process_quote",
                        source_status="zero_row",
                    )
                    log("Zero rows after filtering; task left unresolved for replay/resume")
                    return
                persist_ok_result(key, meta, code, msg, rows, stats, count_task=True, count_status=True)
                log("Success, quote rows:", len(rows))
            except Exception as e:
                error_text = f"{type(e).__name__}: {e}"
                failure_status = "persistence_error" if "sync failed" in error_text.lower() or "persist" in error_text.lower() else "exception"
                persist_task_record(build_task_record(key=key, meta=meta, status=failure_status, error=error_text))
                summary["last_error"] = {
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "key": key,
                    "error": error_text,
                    "phase": ("persist_result" if failure_status == "persistence_error" else "process_quote"),
                }
                mark_task_finished()
                update_run_summary(summary, status=failure_status, key=key, model_title=meta["model_title"])
                summary_checkpoint.maybe_write(summary)
                sync_run_record()
                register_gap_candidate(
                    planned_task_by_key[key],
                    reason=failure_status,
                    phase=("persist_result" if failure_status == "persistence_error" else "process_quote"),
                    error=error_text,
                    source_status=failure_status,
                )
                log("Processing exception:", type(e).__name__, e)

        def drain_processing(*, wait_for_one: bool = False, wait_for_all: bool = False):
            if not pending_process:
                return
            while pending_process:
                ready = [future for future in list(pending_process.keys()) if future.done()]
                if not ready:
                    if not (wait_for_one or wait_for_all):
                        return
                    ready, _ = wait(list(pending_process.keys()), return_when=FIRST_COMPLETED)
                    ready = list(ready)
                for future in ready:
                    finalize_processing_future(future)
                if not wait_for_all:
                    return

        stop_submitting = False
        fatal_stop: Optional[DGTeamFatalStop] = None
        next_planned_index = 0

        def submit_request_future(
            planned: Dict[str, Any],
            *,
            login_retry_index: int = 0,
            request_retry_index: int = 0,
            log_submit: bool = True,
        ):
            nonlocal started_tasks, current_delay
            item_idx = planned["item_idx"]
            meta = planned["meta"]
            key = planned["key"]
            mark_task_running(key, meta)
            if log_submit:
                log(f"[{item_idx}/{task_total}] Fetching: {meta['brand_title']} / {meta['series_title']} / {meta['model_title']} / {meta['city_title']}")
                started_tasks += 1
            snapshot = {
                "sid": request_auth_state["sid"],
                "token": request_auth_state["token"],
                "cookies": dict(request_auth_state["cookies"]),
                "user_agent": request_auth_state["user_agent"],
                "generation": int(request_auth_state.get("generation", 0)),
            }
            future = request_executor.submit(
                fetch_quote_http,
                snapshot["sid"],
                snapshot["token"],
                meta["model_id"],
                meta["city_id"],
                cookies=snapshot["cookies"],
                user_agent=snapshot["user_agent"],
            )
            pending_requests[future] = {
                "planned": planned,
                "login_retry_index": login_retry_index,
                "request_retry_index": request_retry_index,
                "auth_generation": snapshot["generation"],
            }
            if log_submit:
                time.sleep(current_delay)
                current_delay = recover_delay(current_delay, base_delay)

        def finalize_request_future(future):
            nonlocal local_storage, sid, token, request_auth_state, current_delay, stop_submitting, fatal_stop
            info = pending_requests.pop(future)
            planned = info["planned"]
            meta = planned["meta"]
            key = planned["key"]
            login_retry_index = int(info.get("login_retry_index") or 0)
            request_retry_index = int(info.get("request_retry_index") or 0)
            auth_generation = int(info.get("auth_generation") or 0)

            try:
                quote = future.result()
            except Exception as e:
                error_text = f"{type(e).__name__}: {e}"
                if request_retry_index < args.request_retries and not stop_submitting:
                    current_delay = increase_delay(current_delay, base_delay)
                    log(f"Request exception, preparing retry {request_retry_index + 1}/{args.request_retries}: {meta['brand_title']} / {meta['series_title']} / {meta['model_title']} -> {error_text}")
                    submit_request_future(
                        planned,
                        login_retry_index=login_retry_index,
                        request_retry_index=request_retry_index + 1,
                        log_submit=False,
                    )
                    return
                persist_task_record(build_task_record(key=key, meta=meta, status="exception", error=error_text))
                summary["last_error"] = {
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "key": key,
                    "error": error_text,
                    "phase": "request_fetch",
                }
                mark_task_finished()
                update_run_summary(summary, status="exception", key=key, model_title=meta["model_title"])
                summary_checkpoint.maybe_write(summary)
                sync_run_record()
                current_delay = increase_delay(current_delay, base_delay)
                register_gap_candidate(
                    planned,
                    reason="request_exception",
                    phase="request_fetch",
                    error=error_text,
                    source_status="exception",
                )
                log("Request exception:", type(e).__name__, e)
                return

            code = str(quote.get("code", ""))
            msg = str(quote.get("msg", ""))

            if code in SESSION_EXPIRED_API_CODES and auth_generation < int(request_auth_state.get("generation", 0)):
                if request_retry_index < args.request_retries and not stop_submitting:
                    log(
                        f"Discarding stale auth response and retrying with refreshed session: "
                        f"{meta['brand_title']} / {meta['series_title']} / {meta['model_title']}"
                    )
                    submit_request_future(
                        planned,
                        login_retry_index=login_retry_index,
                        request_retry_index=request_retry_index + 1,
                        log_submit=False,
                    )
                    return

            if code in SESSION_EXPIRED_API_CODES and login_retry_index < args.max_login_retries:
                summary["task_counts"]["relogin"] += 1
                summary["last_error"] = {
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "key": key,
                    "code": code,
                    "msg": msg,
                    "phase": "relogin_retry",
                }
                current_delay = increase_delay(current_delay, base_delay)
                persist_run_event(
                    "relogin",
                    {
                        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "task_key": key,
                        **meta,
                        "code": code,
                        "msg": msg,
                        "retry_index": login_retry_index + 1,
                        "max_login_retries": args.max_login_retries,
                        "current_delay": current_delay,
                        "auth_generation": auth_generation,
                    },
                )
                log(f"Login session expired, preparing retry {login_retry_index + 1}/{args.max_login_retries}; current delay backed off to {current_delay:.2f} sec")
                try:
                    request_auth_state = refresh_request_auth_state(expected_generation=auth_generation, phase="quote_fetch")
                except DGTeamFatalStop as stop_exc:
                    persist_task_record(build_task_record(key=key, meta=meta, status="error", code=code, msg=msg, error=str(stop_exc)))
                    summary["last_error"] = {
                        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "key": key,
                        "code": code,
                        "msg": msg,
                        "error": str(stop_exc),
                        "phase": "relogin_retry",
                    }
                    mark_task_finished()
                    update_run_summary(summary, code=code, status="error", key=key, model_title=meta["model_title"])
                    stop_submitting = True
                    summary["status"] = stop_exc.status
                    summary["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    if fatal_stop is None:
                        fatal_stop = stop_exc
                    summary_checkpoint.maybe_write(summary, force=True)
                    sync_run_record()
                    register_gap_candidate(
                        planned,
                        reason="relogin_failed",
                        phase="relogin_retry",
                        code=code,
                        msg=msg,
                        error=str(stop_exc),
                        source_status="error",
                    )
                    return
                submit_request_future(
                    planned,
                    login_retry_index=login_retry_index + 1,
                    request_retry_index=request_retry_index,
                    log_submit=False,
                )
                return

            if code in SESSION_EXPIRED_API_CODES:
                persist_task_record(build_task_record(key=key, meta=meta, status="error", code=code, msg=msg))
                summary["last_error"] = {
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "key": key,
                    "code": code,
                    "msg": msg,
                    "phase": "quote_fetch",
                }
                mark_task_finished()
                update_run_summary(summary, code=code, status="error", key=key, model_title=meta["model_title"])
                if args.save_raw:
                    err_path = raw_dir / f"ERR__{safe_name(meta['brand_title'])}__{safe_name(meta['series_title'])}__{safe_name(meta['model_title'])}__{safe_name(meta['city_title'])}.json"
                    err_path.write_text(json.dumps(quote, ensure_ascii=False, indent=2), encoding="utf-8")
                summary_checkpoint.maybe_write(summary)
                sync_run_record()
                register_gap_candidate(
                    planned,
                    reason="login_state",
                    phase="quote_fetch",
                    code=code,
                    msg=msg,
                    source_status="error",
                )
                stop_submitting = True
                summary["status"] = "stopped_session_expired"
                summary["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                if fatal_stop is None:
                    fatal_stop = DGTeamFatalStop(
                        f"Session expired and could not be refreshed: code={code} msg={msg}",
                        "stopped_session_expired",
                    )
                summary_checkpoint.maybe_write(summary, force=True)
                sync_run_record()
                return

            if code == "0" and "Membership expired" in msg:
                persist_task_record(build_task_record(key=key, meta=meta, status="error", code=code, msg=msg))
                persist_run_event(
                    "membership_expired",
                    {
                        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "task_key": key,
                        **meta,
                        "code": code,
                        "msg": msg,
                    },
                    event_key=f"membership_expired:{key}",
                )
                summary["last_error"] = {
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "key": key,
                    "code": code,
                    "msg": msg,
                    "phase": "quote_fetch",
                }
                mark_task_finished()
                update_run_summary(summary, code=code, status="error", key=key, model_title=meta["model_title"])
                stop_submitting = True
                summary["status"] = "stopped_membership_expired"
                summary["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                if fatal_stop is None:
                    fatal_stop = DGTeamFatalStop(
                        f"Membership expired; quote API unavailable: code={code} msg={msg}",
                        "stopped_membership_expired",
                    )
                summary_checkpoint.maybe_write(summary, force=True)
                sync_run_record()
                return

            if code not in SUCCESS_API_CODES:
                persist_task_record(build_task_record(key=key, meta=meta, status="error", code=code, msg=msg))
                summary["last_error"] = {
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "key": key,
                    "code": code,
                    "msg": msg,
                    "phase": "quote_fetch",
                }
                mark_task_finished()
                update_run_summary(summary, code=code, status="error", key=key, model_title=meta["model_title"])
                if code in RETRYABLE_API_CODES:
                    persist_run_event(
                        "retryable_api_error",
                        {
                            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "task_key": key,
                            **meta,
                            "code": code,
                            "msg": msg,
                            "request_retry_index": request_retry_index,
                            "login_retry_index": login_retry_index,
                        },
                    )
                summary_checkpoint.maybe_write(summary)
                sync_run_record()
                register_gap_candidate(
                    planned,
                    reason=("retryable_api_code" if code in RETRYABLE_API_CODES else "api_code"),
                    phase="quote_fetch",
                    code=code,
                    msg=msg,
                    source_status="error",
                )
                return

            process_future = process_executor.submit(
                process_quote_result,
                quote,
                meta,
                image_dir,
                raw_dir,
                allowed_gprice_labels,
                per_group_valid_limit,
                save_price_images=args.save_price_images,
                save_raw=args.save_raw,
                ocr_cache=ocr_cache,
                external_rules=external_rules,
                external_should_keep=external_should_keep,
            )
            pending_process[process_future] = {
                "key": key,
                "meta": meta,
                "code": code,
                "msg": msg,
            }

        def drain_requests(*, wait_for_one: bool = False, wait_for_all: bool = False):
            if not pending_requests:
                return
            while pending_requests:
                ready = [future for future in list(pending_requests.keys()) if future.done()]
                if not ready:
                    if not (wait_for_one or wait_for_all):
                        return
                    ready, _ = wait(list(pending_requests.keys()), return_when=FIRST_COMPLETED)
                    ready = list(ready)
                for future in ready:
                    finalize_request_future(future)
                if not wait_for_all:
                    return

        def replay_gap_task(planned: Dict[str, Any], *, pass_index: int) -> bool:
            nonlocal local_storage, sid, token, request_auth_state, current_delay, stop_submitting, fatal_stop
            key = planned["key"]
            meta = planned["meta"]
            state = gap_state_by_key.get(key)
            if state is not None:
                state["replay_attempts"] = max(int(state.get("replay_attempts") or 0), int(pass_index))
            append_gap_event(
                "gap_replay_attempt",
                {
                    "task_key": key,
                    **meta,
                    "pass_index": int(pass_index),
                },
            )
            login_retry_index = 0
            request_retry_index = 0
            while True:
                snapshot = {
                    "sid": request_auth_state["sid"],
                    "token": request_auth_state["token"],
                    "cookies": dict(request_auth_state["cookies"]),
                    "user_agent": request_auth_state["user_agent"],
                    "generation": int(request_auth_state.get("generation", 0)),
                }
                try:
                    quote = fetch_quote_http(
                        snapshot["sid"],
                        snapshot["token"],
                        meta["model_id"],
                        meta["city_id"],
                        cookies=snapshot["cookies"],
                        user_agent=snapshot["user_agent"],
                    )
                except Exception as e:
                    error_text = f"{type(e).__name__}: {e}"
                    if request_retry_index < args.request_retries:
                        current_delay = increase_delay(current_delay, base_delay)
                        log(f"Request exception, preparing retry {request_retry_index + 1}/{args.request_retries}: {meta['brand_title']} / {meta['series_title']} / {meta['model_title']} -> {error_text}")
                        request_retry_index += 1
                        time.sleep(current_delay)
                        continue
                    persist_task_record(build_task_record(key=key, meta=meta, status="exception", error=error_text))
                    summary["last_error"] = {
                        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "key": key,
                        "error": error_text,
                        "phase": "gap_replay_request_fetch",
                    }
                    sync_run_record()
                    current_delay = increase_delay(current_delay, base_delay)
                    mark_gap_unresolved(
                        planned,
                        pass_index=pass_index,
                        reason="request_exception",
                        phase="gap_replay_request_fetch",
                        error=error_text,
                        source_status="exception",
                    )
                    log("Gap replay request exception:", type(e).__name__, e)
                    return False

                current_delay = recover_delay(current_delay, base_delay)
                code = str(quote.get("code", ""))
                msg = str(quote.get("msg", ""))

                if code in SESSION_EXPIRED_API_CODES and login_retry_index < args.max_login_retries:
                    summary["task_counts"]["relogin"] += 1
                    current_delay = increase_delay(current_delay, base_delay)
                    persist_run_event(
                        "relogin",
                        {
                            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "task_key": key,
                            **meta,
                            "code": code,
                            "msg": msg,
                            "retry_index": login_retry_index + 1,
                            "max_login_retries": args.max_login_retries,
                            "current_delay": current_delay,
                            "mode": "gap_replay",
                            "gap_pass_index": int(pass_index),
                            "auth_generation": snapshot["generation"],
                        },
                    )
                    log(
                        f"Login session expired, preparing retry {login_retry_index + 1}/{args.max_login_retries}; current delay backed off to {current_delay:.2f} sec"
                    )
                    try:
                        request_auth_state = refresh_request_auth_state(
                            expected_generation=snapshot["generation"],
                            phase="gap_replay_quote_fetch",
                        )
                    except DGTeamFatalStop as stop_exc:
                        persist_task_record(build_task_record(key=key, meta=meta, status="error", code=code, msg=msg, error=str(stop_exc)))
                        summary["last_error"] = {
                            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "key": key,
                            "code": code,
                            "msg": msg,
                            "error": str(stop_exc),
                            "phase": "gap_replay_relogin_retry",
                        }
                        stop_submitting = True
                        summary["status"] = stop_exc.status
                        summary["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                        if fatal_stop is None:
                            fatal_stop = stop_exc
                        summary_checkpoint.maybe_write(summary, force=True)
                        sync_run_record()
                        mark_gap_unresolved(
                            planned,
                            pass_index=pass_index,
                            reason="relogin_failed",
                            phase="gap_replay_relogin_retry",
                            code=code,
                            msg=msg,
                            error=str(stop_exc),
                            source_status="error",
                        )
                        return False
                    login_retry_index += 1
                    time.sleep(current_delay)
                    continue

                if code in SESSION_EXPIRED_API_CODES:
                    persist_task_record(build_task_record(key=key, meta=meta, status="error", code=code, msg=msg))
                    summary["last_error"] = {
                        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "key": key,
                        "code": code,
                        "msg": msg,
                        "phase": "gap_replay_quote_fetch",
                    }
                    sync_run_record()
                    mark_gap_unresolved(
                        planned,
                        pass_index=pass_index,
                        reason="login_state",
                        phase="gap_replay_quote_fetch",
                        code=code,
                        msg=msg,
                        source_status="error",
                    )
                    stop_submitting = True
                    summary["status"] = "stopped_session_expired"
                    summary["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    if fatal_stop is None:
                        fatal_stop = DGTeamFatalStop(
                            f"Session expired and could not be refreshed during gap replay: code={code} msg={msg}",
                            "stopped_session_expired",
                        )
                    summary_checkpoint.maybe_write(summary, force=True)
                    sync_run_record()
                    return False

                if code == "0" and "Membership expired" in msg:
                    persist_task_record(build_task_record(key=key, meta=meta, status="error", code=code, msg=msg))
                    persist_run_event(
                        "membership_expired",
                        {
                            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "task_key": key,
                            **meta,
                            "code": code,
                            "msg": msg,
                            "mode": "gap_replay",
                            "gap_pass_index": int(pass_index),
                        },
                        event_key=f"membership_expired:{key}:gap:{pass_index}",
                    )
                    summary["last_error"] = {
                        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "key": key,
                        "code": code,
                        "msg": msg,
                        "phase": "gap_replay_quote_fetch",
                    }
                    stop_submitting = True
                    summary["status"] = "stopped_membership_expired"
                    summary["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    if fatal_stop is None:
                        fatal_stop = DGTeamFatalStop(
                            f"Membership expired; quote API unavailable: code={code} msg={msg}",
                            "stopped_membership_expired",
                        )
                    summary_checkpoint.maybe_write(summary, force=True)
                    sync_run_record()
                    mark_gap_unresolved(
                        planned,
                        pass_index=pass_index,
                        reason="membership_expired",
                        phase="gap_replay_quote_fetch",
                        code=code,
                        msg=msg,
                        source_status="error",
                    )
                    return False

                if code not in SUCCESS_API_CODES:
                    persist_task_record(build_task_record(key=key, meta=meta, status="error", code=code, msg=msg))
                    summary["last_error"] = {
                        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "key": key,
                        "code": code,
                        "msg": msg,
                        "phase": "gap_replay_quote_fetch",
                    }
                    sync_run_record()
                    mark_gap_unresolved(
                        planned,
                        pass_index=pass_index,
                        reason=("retryable_api_code" if code in RETRYABLE_API_CODES else "api_code"),
                        phase="gap_replay_quote_fetch",
                        code=code,
                        msg=msg,
                        source_status="error",
                    )
                    if code in RETRYABLE_API_CODES:
                        persist_run_event(
                            "retryable_api_error",
                            {
                                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                                "task_key": key,
                                **meta,
                                "code": code,
                                "msg": msg,
                                "mode": "gap_replay",
                                "gap_pass_index": int(pass_index),
                            },
                        )
                    return False

                try:
                    result = process_quote_result(
                        quote,
                        meta,
                        image_dir,
                        raw_dir,
                        allowed_gprice_labels,
                        per_group_valid_limit,
                        save_price_images=args.save_price_images,
                        save_raw=args.save_raw,
                        ocr_cache=ocr_cache,
                        external_rules=external_rules,
                        external_should_keep=external_should_keep,
                    )
                except Exception as e:
                    error_text = f"{type(e).__name__}: {e}"
                    persist_task_record(build_task_record(key=key, meta=meta, status="exception", error=error_text))
                    summary["last_error"] = {
                        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "key": key,
                        "error": error_text,
                        "phase": "gap_replay_process_quote",
                    }
                    sync_run_record()
                    mark_gap_unresolved(
                        planned,
                        pass_index=pass_index,
                        reason="process_exception",
                        phase="gap_replay_process_quote",
                        error=error_text,
                        source_status="exception",
                    )
                    log("Gap replay processing exception:", type(e).__name__, e)
                    return False

                rows = result["rows"]
                stats = result["stats"]
                try:
                    if not rows:
                        persist_zero_row_result(key, meta, code, msg, stats, count_task=False, count_status=False)
                        mark_gap_unresolved(
                            planned,
                            pass_index=pass_index,
                            reason="zero_rows",
                            phase="gap_replay_process_quote",
                            source_status="zero_row",
                        )
                        log(f"Gap replay kept zero rows [{pass_index}]; task remains unresolved")
                        return False
                    persist_ok_result(key, meta, code, msg, rows, stats, count_task=False, count_status=False)
                except Exception as e:
                    error_text = f"{type(e).__name__}: {e}"
                    persist_task_record(build_task_record(key=key, meta=meta, status="persistence_error", error=error_text))
                    summary["last_error"] = {
                        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "key": key,
                        "error": error_text,
                        "phase": "gap_replay_persist_result",
                    }
                    sync_run_record()
                    mark_gap_unresolved(
                        planned,
                        pass_index=pass_index,
                        reason="persistence_error",
                        phase="gap_replay_persist_result",
                        error=error_text,
                        source_status="persistence_error",
                    )
                    log("Gap replay persistence exception:", type(e).__name__, e)
                    return False
                mark_gap_resolved(planned, pass_index=pass_index, rows_count=len(rows))
                log(f"Gap replay succeeded [{pass_index}], quote rows:", len(rows))
                return True

        def run_gap_replay_passes():
            if args.gap_replay_passes <= 0 or fatal_stop is not None:
                refresh_gap_replay_summary()
                return
            for pass_index in range(1, args.gap_replay_passes + 1):
                pending_gap_tasks = [
                    planned_task_by_key[key]
                    for key, state in gap_state_by_key.items()
                    if not state.get("resolved") and key in planned_task_by_key
                ]
                if not pending_gap_tasks:
                    break
                summary["gap_replay"]["passes_started"] += 1
                summary["gap_replay"]["scheduled"] += len(pending_gap_tasks)
                refresh_gap_replay_summary()
                append_gap_event(
                    "gap_replay_start",
                    {
                        "pass_index": int(pass_index),
                        "task_count": len(pending_gap_tasks),
                    },
                    event_key=f"gap_replay_start:{sqlite_run_key}:{pass_index}",
                )
                summary_checkpoint.maybe_write(summary)
                sync_run_record()
                log(f"Starting gap replay pass {pass_index}/{args.gap_replay_passes}; pending {len(pending_gap_tasks)} items")
                for replay_idx, planned in enumerate(pending_gap_tasks, 1):
                    if fatal_stop is not None:
                        break
                    meta = planned["meta"]
                    log(f"[gap {pass_index}:{replay_idx}/{len(pending_gap_tasks)}] Fetching: {meta['brand_title']} / {meta['series_title']} / {meta['model_title']} / {meta['city_title']}")
                    replay_gap_task(planned, pass_index=pass_index)
                    time.sleep(current_delay)
                if fatal_stop is not None:
                    break
            refresh_gap_replay_summary()
            if gap_state_by_key:
                append_gap_event(
                    "gap_replay_complete",
                    {
                        "passes_started": int(summary["gap_replay"]["passes_started"]),
                        "detected": int(summary["gap_replay"]["detected"]),
                        "resolved": int(summary["gap_replay"]["resolved"]),
                        "remaining": int(summary["gap_replay"]["remaining"]),
                    },
                    event_key=f"gap_replay_complete:{sqlite_run_key}",
                )
                summary_checkpoint.maybe_write(summary, force=True)
                sync_run_record()
                log(
                    "Gap replay completed:",
                    "found",
                    int(summary["gap_replay"]["detected"]),
                    " items pending",
                    "recovered",
                    int(summary["gap_replay"]["resolved"]),
                    " items pending",
                    "remaining",
                    int(summary["gap_replay"]["remaining"]),
                    " items pending",
                )

        try:
            while True:
                drain_processing()
                if len(pending_process) >= args.max_pending_process:
                    drain_processing(wait_for_one=True)

                while (
                    not stop_submitting
                    and next_planned_index < len(scheduled_tasks)
                    and len(pending_requests) < args.request_workers
                    and len(pending_process) < args.max_pending_process
                ):
                    if args.max_tasks and started_tasks >= args.max_tasks:
                        log("Reached max-tasks; waiting for submitted tasks to finish before stopping")
                        summary["status"] = "stopped_max_tasks"
                        summary["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                        stop_submitting = True
                        break
                    planned = scheduled_tasks[next_planned_index]
                    next_planned_index += 1
                    submit_request_future(planned)

                if pending_requests:
                    drain_requests(wait_for_one=True)
                    continue

                if pending_process:
                    drain_processing(wait_for_one=True)
                    continue

                if stop_submitting or next_planned_index >= len(scheduled_tasks):
                    break

            drain_requests(wait_for_all=True)
            drain_processing(wait_for_all=True)
            run_gap_replay_passes()
            if fatal_stop is not None:
                sync_ocr_cache_summary()
                summary_checkpoint.flush(summary)
                sync_run_record()
                raise fatal_stop
            if summary.get("status") == "stopped_max_tasks":
                sync_ocr_cache_summary()
                summary_checkpoint.flush(summary)
                sync_run_record()
                return
            if external_storage and external_publish_live_market and sqlite_run_key:
                try:
                    live_publish_result = external_publish_live_market(external_storage, sqlite_run_key)
                    summary.setdefault("integrations", {})["live_market_publish"] = live_publish_result
                    log(
                        "Live market published:",
                        live_publish_result.get("run_key"),
                        "snapshots:",
                        live_publish_result.get("snapshot_count"),
                    )
                except Exception as e:
                    live_publish_error = f"{type(e).__name__}: {e}"
                    summary.setdefault("integrations", {})["live_market_publish"] = {
                        "ok": False,
                        "run_key": sqlite_run_key,
                        "error": live_publish_error,
                    }
                    summary["status"] = "failed_live_publish"
                    summary["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    sync_ocr_cache_summary()
                    summary_checkpoint.flush(summary)
                    sync_run_record()
                    log("Live market publish failed:", live_publish_error)
                    raise DGTeamFatalStop(
                        f"Live market publish failed after crawl completed: {live_publish_error}",
                        "failed_live_publish",
                    ) from e
            summary["status"] = "completed"
            summary["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            sync_ocr_cache_summary()
            summary_checkpoint.flush(summary)
            sync_run_record()
            log("All tasks completed")
        finally:
            try:
                drain_requests(wait_for_all=True)
            except Exception as e:
                    log("Gap replay request exception:", type(e).__name__, e)
            try:
                drain_processing(wait_for_all=True)
            except Exception as e:
                log("Shutdown processing exception:", type(e).__name__, e)
            request_executor.shutdown(wait=True)
            process_executor.shutdown(wait=True)
            sync_ocr_cache_summary()
            summary_checkpoint.flush(summary)
            sync_run_record()
            rows_appender.close()
            gap_appender.close()
            quotes_appender.close()
            progress_appender.close()
            ocr_cache.close()
            context.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Interrupted by user", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        print(f"Unhandled exception: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)

