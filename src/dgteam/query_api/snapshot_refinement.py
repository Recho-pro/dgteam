from __future__ import annotations

import copy
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Sequence


COMMON_COLOR_HINTS: Sequence[str] = (
    "深空灰色",
    "深空灰",
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
    "红",
    "红色",
    "钛",
    "钛色",
)

MODEL_KEYWORDS: Sequence[str] = (
    "iphone",
    "苹果",
    "apple",
    "redmi",
    "红米",
    "小米",
    "xiaomi",
    "huawei",
    "华为",
    "honor",
    "荣耀",
    "mate",
    "pura",
    "nova",
    "magic",
    "iqoo",
    "vivo",
    "oppo",
    "oneplus",
    "一加",
    "lenovo",
    "联想",
    "小新",
    "thinkbook",
    "thinkpad",
    "k80",
    "x200",
)

COMPONENT_VARIANT_HINTS: Sequence[str] = (
    "充电盒",
    "耳机盒",
    "左耳",
    "右耳",
    "单耳",
    "charging case",
    "earbud case",
)

COLOR_HINT_ALIASES: Mapping[str, str] = {
    "侮腎子弼": "深空灰",
    "侮腎子弼色": "深空灰",
}


@dataclass(frozen=True)
class SnapshotRefinementOutcome:
    applied: bool
    reason: str
    summary: str
    matched_branches: int
    matched_capacity_groups: int
    matched_colors: int
    snapshot: Dict[str, Any]


def normalize_search_surface(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "").strip().lower())
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def compact_search_surface(value: str) -> str:
    return normalize_search_surface(value).replace(" ", "")


def normalize_capacity_hint(token: str) -> str:
    raw = str(token or "").strip().upper().replace(" ", "")
    raw = raw.replace("GB", "G").replace("TB", "T")
    if not raw:
        return ""
    pair_match = re.fullmatch(r"(\d+)\+(\d+)(G|T)?", raw)
    if pair_match:
        return f"{pair_match.group(1)}+{pair_match.group(2)}{pair_match.group(3) or 'G'}".lower()
    single_match = re.fullmatch(r"(\d+)(G|T)?", raw)
    if not single_match:
        return ""
    size = int(single_match.group(1))
    if not single_match.group(2) and size not in {64, 128, 256, 512, 1024, 2048}:
        return ""
    return f"{single_match.group(1)}{single_match.group(2) or ''}".lower()


def extract_capacity_hints(query: str) -> list[str]:
    hints: set[str] = set()
    normalized = normalize_search_surface(query)
    compact = compact_search_surface(query)
    sources = [item for item in (normalized, compact) if item]
    patterns = (
        re.compile(r"\b\d+\+\d+(?:g|t)?\b", flags=re.IGNORECASE),
        re.compile(r"\b\d+(?:g|t)\b", flags=re.IGNORECASE),
        re.compile(r"\b(?:64|128|256|512|1024|2048)\b"),
    )
    for source in sources:
        for pattern in patterns:
            for match in pattern.findall(source):
                hint = normalize_capacity_hint(match)
                if hint:
                    hints.add(hint)
    return sorted(hints)


def extract_color_hints(query: str) -> list[str]:
    compact = compact_search_surface(query)
    matches: list[str] = []
    matched_hints: list[str] = []
    for alias, canonical in COLOR_HINT_ALIASES.items():
        normalized_alias = compact_search_surface(alias)
        if normalized_alias and normalized_alias in compact:
            matched_hints.append(normalized_alias)
            matches.append(canonical)
    for hint in sorted(COMMON_COLOR_HINTS, key=len, reverse=True):
        normalized_hint = compact_search_surface(hint)
        if not normalized_hint or normalized_hint not in compact:
            continue
        if any(normalized_hint in existing for existing in matched_hints):
            continue
        matched_hints.append(normalized_hint)
        matches.append(hint)
    return matches


def tokenize_search_query(query: str) -> list[str]:
    normalized = normalize_search_surface(query)
    if not normalized:
        return []
    return [token for token in normalized.split() if token]


def extract_compact_aliases(value: str) -> list[str]:
    aliases: set[str] = set()
    normalized = normalize_search_surface(value)
    compact = compact_search_surface(value)
    if normalized:
        aliases.add(normalized)
    if compact:
        aliases.add(compact)
    if "promax" in compact:
        aliases.add(compact.replace("promax", "pm"))
    if compact.startswith("iphone"):
        shorthand = compact.removeprefix("iphone")
        if shorthand:
            aliases.add(shorthand)
        if "promax" in shorthand:
            aliases.add(shorthand.replace("promax", "pm"))
    return [item for item in aliases if item]


def snapshot_vocabulary(snapshot: Mapping[str, Any]) -> list[str]:
    values: set[str] = set()
    header_title = str((snapshot.get("header") or {}).get("title") or "")
    branches = snapshot.get("branches") or []
    for token in extract_compact_aliases(header_title):
        values.add(token)
    for branch in branches:
        for token in extract_compact_aliases(str(branch.get("branch_title") or "")):
            values.add(token)
        for group in branch.get("capacity_groups") or []:
            for token in extract_compact_aliases(str(group.get("capacity_label") or "")):
                values.add(token)
            for color in group.get("colors") or []:
                for field in ("color_label", "group_title"):
                    for token in extract_compact_aliases(str(color.get(field) or "")):
                        values.add(token)
    return sorted(values)


def query_looks_like_global_model_search(query: str) -> bool:
    compact = compact_search_surface(query)
    if not compact:
        return False
    return any(compact_search_surface(keyword) in compact for keyword in MODEL_KEYWORDS)


def can_refine_current_snapshot(query: str, snapshot: Mapping[str, Any]) -> bool:
    if not snapshot:
        return False
    tokens = tokenize_search_query(query)
    if not tokens:
        return False
    if query_looks_like_global_model_search(query):
        return False
    vocabulary = snapshot_vocabulary(snapshot)
    if not vocabulary:
        return False
    capacity_hints = extract_capacity_hints(query)
    color_hints = extract_color_hints(query)
    if capacity_hints or color_hints:
        return True
    for token in tokens:
        compact_token = compact_search_surface(token)
        if not any(value in compact_token or compact_token in value for value in vocabulary):
            return False
    return True


def score_text_match(value: str, query: str) -> int:
    source = compact_search_surface(value)
    compact_query = compact_search_surface(query)
    if not source or not compact_query:
        return 0
    if source == compact_query:
        return 320
    if source.startswith(compact_query):
        return 220
    if compact_query in source:
        return 140
    return 0


def variant_search_surface(color: Mapping[str, Any]) -> str:
    return compact_search_surface(
        " ".join(
            str(part or "")
            for part in (
                color.get("group_title"),
                color.get("color_label"),
            )
            if str(part or "").strip()
        )
    )


def surface_contains_capacity_hint(surface: str, hints: Sequence[str]) -> bool:
    if not hints:
        return True
    compact_surface = compact_search_surface(surface)
    normalized_hints = [compact_search_surface(hint) for hint in hints if hint]
    return bool(normalized_hints) and all(hint in compact_surface for hint in normalized_hints)


def token_match_score(surface: str, tokens: Sequence[str]) -> int:
    compact_surface = compact_search_surface(surface)
    if not compact_surface or not tokens:
        return 0
    score = 0
    for token in tokens:
        compact_token = compact_search_surface(token)
        if not compact_token:
            continue
        if compact_surface == compact_token:
            score += 320
        elif compact_surface.startswith(compact_token):
            score += 220
        elif compact_token in compact_surface or compact_surface in compact_token:
            score += 140
    return score


def color_hint_matches(color_surface: str, color_hints: Sequence[str]) -> bool:
    if not color_hints:
        return True
    compact_surface = compact_search_surface(color_surface)
    for hint in color_hints:
        compact_hint = compact_search_surface(hint)
        aliases = [compact_hint]
        if compact_hint.endswith("色"):
            aliases.append(compact_hint[:-1])
        for alias in aliases:
            if alias and (compact_surface in alias or alias in compact_surface):
                return True
    return False


def query_requests_component(query: str) -> bool:
    compact = compact_search_surface(query)
    if not compact:
        return False
    return any(compact_search_surface(hint) in compact for hint in COMPONENT_VARIANT_HINTS)


def variant_is_component(color: Mapping[str, Any]) -> bool:
    surface = variant_search_surface(color)
    if not surface:
        return False
    return any(compact_search_surface(hint) in surface for hint in COMPONENT_VARIANT_HINTS)


def price_range_bounds(value: Any) -> tuple[int, int] | None:
    matches = re.findall(r"\d+", str(value or ""))
    if not matches:
        return None
    if len(matches) == 1:
        price = int(matches[0])
        return price, price
    return int(matches[0]), int(matches[1])


def coerce_int(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def refresh_group_summary_from_colors(group: Dict[str, Any]) -> Dict[str, Any]:
    colors = [dict(item or {}) for item in group.get("colors") or []]
    if not colors:
        return group

    bounds = [price_range_bounds(color.get("price_range")) for color in colors]
    bounds = [item for item in bounds if item is not None]
    if bounds:
        low = min(item[0] for item in bounds)
        high = max(item[1] for item in bounds)
        group["price_range"] = f"{low}-{high}"

    representative_price = next((coerce_int(color.get("market_price")) for color in colors if coerce_int(color.get("market_price")) is not None), None)
    if representative_price is not None:
        group["market_price"] = representative_price

    latest_labels: list[str] = []
    for color in colors:
        for label in color.get("selected_gprice_labels") or []:
            clean = str(label or "").strip()
            if clean and clean not in latest_labels:
                latest_labels.append(clean)
    if latest_labels:
        group["selected_gprice_labels"] = latest_labels
    return group


def capacity_sort_key(label: str) -> tuple[int, int]:
    text = str(label or "").strip().upper().replace(" ", "")
    text = text.replace("GB", "G").replace("TB", "T")
    match = re.fullmatch(r"(?:(\d+)\+)?(\d+)(G|T)", text)
    if not match:
        return (10**9, 10**9)
    memory = int(match.group(1) or 0)
    storage = int(match.group(2)) * (1024 if match.group(3) == "T" else 1)
    return (memory, storage)


def sort_capacity_groups(groups: Sequence[Mapping[str, Any]]) -> list[Dict[str, Any]]:
    return sorted(
        [dict(group) for group in groups],
        key=lambda item: (
            -int(item.get("__matchScore") or 0),
            capacity_sort_key(str(item.get("capacity_label") or ""))[0],
            capacity_sort_key(str(item.get("capacity_label") or ""))[1],
            str(item.get("capacity_label") or ""),
        ),
    )


def _cleanup_match_fields(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    for branch in snapshot.get("branches") or []:
        branch.pop("__matchScore", None)
        for group in branch.get("capacity_groups") or []:
            group.pop("__matchScore", None)
            for color in group.get("colors") or []:
                color.pop("__matchScore", None)
    return snapshot


def refine_snapshot(snapshot: Mapping[str, Any], query: str) -> SnapshotRefinementOutcome:
    base_snapshot = copy.deepcopy(dict(snapshot or {}))
    trimmed_query = str(query or "").strip()
    if not trimmed_query:
        base_snapshot.pop("refinementSummary", None)
        return SnapshotRefinementOutcome(
            applied=False,
            reason="empty_query",
            summary="",
            matched_branches=0,
            matched_capacity_groups=0,
            matched_colors=0,
            snapshot=_cleanup_match_fields(base_snapshot),
        )

    if not can_refine_current_snapshot(trimmed_query, base_snapshot):
        base_snapshot.pop("refinementSummary", None)
        return SnapshotRefinementOutcome(
            applied=False,
            reason="not_applicable",
            summary="",
            matched_branches=0,
            matched_capacity_groups=0,
            matched_colors=0,
            snapshot=_cleanup_match_fields(base_snapshot),
        )

    compact_query = compact_search_surface(trimmed_query)
    capacity_hints = extract_capacity_hints(trimmed_query)
    color_hints = [compact_search_surface(hint) for hint in extract_color_hints(trimmed_query)]
    query_tokens = [compact_search_surface(token) for token in tokenize_search_query(trimmed_query)]
    explicit_component_request = query_requests_component(trimmed_query)

    focused_branches: list[Dict[str, Any]] = []
    matched_capacity_groups = 0
    matched_colors = 0

    for branch in base_snapshot.get("branches") or []:
        branch_score = score_text_match(str(branch.get("branch_title") or ""), compact_query)
        focused_groups: list[Dict[str, Any]] = []
        for group in sort_capacity_groups(branch.get("capacity_groups") or []):
            group_capacity = compact_search_surface(str(group.get("capacity_label") or ""))
            group_capacity_matched = not capacity_hints or surface_contains_capacity_hint(group_capacity, capacity_hints)
            should_filter_by_text = bool(query_tokens and not color_hints and (not capacity_hints or not group_capacity_matched))

            filtered_colors: list[Dict[str, Any]] = []
            for color in group.get("colors") or []:
                color_surface = variant_search_surface(color)
                color_capacity_matched = group_capacity_matched or surface_contains_capacity_hint(color_surface, capacity_hints)
                color_matched = color_hint_matches(color_surface, color_hints)
                text_score = token_match_score(color_surface, query_tokens)
                text_matched = not should_filter_by_text or text_score > 0
                matched = color_capacity_matched and color_matched and text_matched
                color_payload = dict(color)
                color_payload["__matched"] = matched
                color_payload["__matchScore"] = (
                    (160 if matched else 0)
                    + score_text_match(color_surface, compact_query)
                    + text_score
                    + (80 if color_matched else 0)
                    + (80 if color_capacity_matched else 0)
                )
                if not color_hints and not should_filter_by_text:
                    filtered_colors.append(color_payload)
                elif matched:
                    filtered_colors.append(color_payload)

            filtered_colors.sort(key=lambda item: int(item.get("__matchScore") or 0), reverse=True)
            if filtered_colors and not explicit_component_request:
                non_component_colors = [item for item in filtered_colors if not variant_is_component(item)]
                if non_component_colors:
                    filtered_colors = non_component_colors

            group_matched_by_text = score_text_match(str(group.get("capacity_label") or ""), compact_query) > 0 or any(
                token in group_capacity for token in query_tokens
            )
            capacity_matched = group_capacity_matched or bool(filtered_colors)
            group_matched = capacity_matched and (not color_hints or bool(filtered_colors))
            visible_colors = filtered_colors if filtered_colors else [dict(color) for color in group.get("colors") or []]
            lead_color_score = int((visible_colors[0] or {}).get("__matchScore") or 0) if visible_colors else 0
            match_score = (
                (220 if group_matched else 0)
                + (120 if capacity_matched else 0)
                + (80 if group_matched_by_text else 0)
                + lead_color_score
            )

            group_payload = dict(group)
            group_payload["colors"] = visible_colors
            if filtered_colors:
                group_payload = refresh_group_summary_from_colors(group_payload)
            group_payload["__matched"] = group_matched
            group_payload["__matchScore"] = match_score

            if capacity_hints or color_hints:
                if group_matched:
                    focused_groups.append(group_payload)
            elif match_score > 0:
                focused_groups.append(group_payload)

        focused_groups = sort_capacity_groups(focused_groups)
        if not focused_groups and branch_score <= 0:
            continue

        branch_score += sum(int(group.get("__matchScore") or 0) for group in focused_groups)
        branch_payload = dict(branch)
        branch_payload["capacity_groups"] = focused_groups
        branch_payload["__matchScore"] = branch_score
        focused_branches.append(branch_payload)

    focused_branches.sort(key=lambda item: int(item.get("__matchScore") or 0), reverse=True)

    if not focused_branches:
        base_snapshot.pop("refinementSummary", None)
        return SnapshotRefinementOutcome(
            applied=False,
            reason="no_match",
            summary="",
            matched_branches=0,
            matched_capacity_groups=0,
            matched_colors=0,
            snapshot=_cleanup_match_fields(base_snapshot),
        )

    matched_capacity_groups = sum(len(branch.get("capacity_groups") or []) for branch in focused_branches)
    matched_colors = sum(
        len(group.get("colors") or [])
        for branch in focused_branches
        for group in branch.get("capacity_groups") or []
    )
    summary = " · ".join(
        part
        for part in (
            " / ".join(hint.upper() for hint in capacity_hints) if capacity_hints else "",
            " / ".join(extract_color_hints(trimmed_query)) if color_hints else "",
        )
        if part
    )

    refined_snapshot = copy.deepcopy(base_snapshot)
    refined_snapshot["branches"] = focused_branches
    refined_snapshot["refinementSummary"] = summary
    return SnapshotRefinementOutcome(
        applied=True,
        reason="applied",
        summary=summary,
        matched_branches=len(focused_branches),
        matched_capacity_groups=matched_capacity_groups,
        matched_colors=matched_colors,
        snapshot=_cleanup_match_fields(refined_snapshot),
    )
