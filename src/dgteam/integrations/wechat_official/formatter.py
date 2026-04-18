from __future__ import annotations

import re
from typing import Any


WEB_URL = "https://dgtdnb.com"
TEAM_WECHAT = "Recho1688"
GENERIC_GROUP_LABELS = {"默认规格", "默认", "标配", "基础款", "未指定"}
GENERIC_MARKET_TITLES = {
    "当前机型",
    "吸尘器",
    "Apple充电头/数据线",
}
PHONE_STORAGE_VALUES = {64, 128, 256, 512, 1024, 2048}


def _clean(text: Any) -> str:
    return str(text or "").strip()


def _join_lines(*lines: str) -> str:
    return "\n".join(line for line in lines if _clean(line))


def format_subscribe_message() -> str:
    return _join_lines(
        "你好，这里是 DG 团队行情助手。",
        "你可以直接发机型名给我，比如：iPhone17ProMax、苹果17pro、红米K80。",
        "也可以直接发电商详情页截图，我会先识别，再帮你对行情。",
        "想看完整页面也可以直接打开：",
        WEB_URL,
    )


def format_greeting_message() -> str:
    return _join_lines(
        "在的，你直接把机型名或者商品截图发给我就行。",
        "如果我给了你几个候选，直接回数字 1、2、3 继续就可以。",
    )


def format_help_message() -> str:
    return _join_lines(
        "这样用最快：",
        "1. 直接发机型名，中文、拼音、简称、带空格都可以。",
        "2. 如果我给了多个候选，直接回 1、2、3 继续。",
        "3. 如果想继续细化，补一句容量、颜色、版本或关键配置就行，比如 512G、黑色、国行、16G+1T。",
        "4. 发电商详情页截图时，尽量把标题、已选规格和价格区域一起截进去。",
        "完整页面：",
        WEB_URL,
    )


def format_image_help_message() -> str:
    return _join_lines(
        "截图这样发，识别会更稳：",
        "1. 最好带商品标题。",
        "2. 最好带已选容量、颜色、版本或配置。",
        "3. 能看到价格区域更好，但不是必须。",
        "4. 如果第一张没识别准，补一张标题更完整的图就行。",
    )


def format_contact_message() -> str:
    return _join_lines(
        "联系团队可以加这个微信：",
        f"团队撸货板块负责人微信号：{TEAM_WECHAT}",
        "如果只是查行情，直接继续在这里发机型名或截图就可以。",
    )


def format_reset_message() -> str:
    return "好的，上一轮我先清掉了。你重新发一个机型名，或者直接发新截图就行。"


def format_no_result(query: str) -> str:
    clean_query = _clean(query)
    return _join_lines(
        f"我这边暂时没对上“{clean_query}”这条行情。",
        "你可以换一种更接近机型本身的写法再发一次，先别带促销词。",
        "比如：iPhone17ProMax、苹果17pro、红米K80。",
    )


def format_ambiguous_result(query: str, results: list[dict[str, Any]]) -> str:
    lines = [f"“{_clean(query)}”我先帮你缩到这几项："]
    for index, item in enumerate(results[:6], start=1):
        label = _clean(item.get("label") or item.get("family_title") or item.get("model_title")) or "未命名机型"
        lines.append(f"{index}. {label}")
    lines.append("直接回数字继续就行，比如回 1。")
    return "\n".join(lines)


def format_image_query_placeholder(task_id: str = "") -> str:
    return _join_lines(
        "图片收到了，我先帮你抓标题和已选规格。",
        "通常几秒内会先回你一版；如果我觉得还不够稳，会再补第二条更准确的结果。",
    )


def format_image_query_deferred(
    *,
    recognized_summary: str = "",
    query_hint: str = "",
    task_id: str = "",
    page_type: str = "",
    definitely_unsupported: bool = False,
) -> str:
    if definitely_unsupported:
        return format_image_unsupported(
            reason=recognized_summary or query_hint,
            page_type=page_type,
        )
    return _join_lines(
        "图片我先接住了，正在继续核对。",
        f"我先大概识别到：{recognized_summary}" if _clean(recognized_summary) else "",
        f"接下来我会优先按“{_clean(query_hint)}”这条线继续找。" if _clean(query_hint) else "",
    )


def format_image_market_snapshot(*, recognized_summary: str, market_reply: str) -> str:
    prefix = f"我先从截图里识别到：\n{_clean(recognized_summary)}\n" if _clean(recognized_summary) else ""
    return f"{prefix}\n{_clean(market_reply)}".strip()


def format_image_candidates(*, recognized_summary: str, candidates: list[str]) -> str:
    lines = []
    if _clean(recognized_summary):
        lines.append(f"我先从图里读到的是：{_clean(recognized_summary)}")
        lines.append("")
    lines.append("更像下面这几个：")
    for index, item in enumerate(candidates[:3], start=1):
        lines.append(f"{index}. {_clean(item)}")
    lines.append("直接回数字继续，或者再发一张标题更完整的图。")
    return "\n".join(lines)


def format_image_no_result(*, recognized_summary: str) -> str:
    return _join_lines(
        "这张图我先识别出一部分信息了，但还没稳到可以直接给你行情。",
        f"当前识别到的是：{_clean(recognized_summary)}" if _clean(recognized_summary) else "",
        "你可以补一张带完整标题和已选规格的截图，或者直接把标题文字发给我。",
    )


def format_image_unsupported(*, reason: str = "", page_type: str = "") -> str:
    clean_reason = _humanize_image_reason(reason)
    normalized_type = _clean(page_type).lower()
    if normalized_type in {"price_list", "quote_sheet"}:
        return _join_lines(
            "这张更像整张报价表，不是单个商品详情页。",
            "如果你要查单个型号，直接发对应商品详情截图，或者直接发机型名给我更快。",
        )
    if normalized_type in {"chat_record", "embedded_chat_screenshot"}:
        return _join_lines(
            "这张更像聊天记录或转发截图，商品信息太碎了。",
            "最好直接发原始商品详情页截图，画面里带上标题和已选规格，我识别会稳很多。",
        )
    if normalized_type in {"listing_page", "store_page"}:
        return _join_lines(
            "这张更像商品列表页，还没落到单个商品详情。",
            "你点进具体商品后再截一张，最好带标题和规格。",
        )
    return _join_lines(
        "这张图我这次没看明白。",
        clean_reason,
        "优先支持商品详情页截图，最好能看到标题、规格和价格区域。",
    )


def format_numeric_selection_out_of_range() -> str:
    return "这个编号不在当前候选里。你可以回 1 到 6 之间的数字，或者直接重发更准确的机型名。"


def format_numeric_selection_expired() -> str:
    return "上一轮候选已经过期了，你重新发一次机型名就行，我会接着帮你查。"


def format_pending_image_status(*, status: str, task_id: str = "", has_candidates: bool = False) -> str:
    normalized = _clean(status).lower()
    if normalized == "queued":
        return "图片已经收到了，还在排队处理中。"
    if normalized == "processing":
        return "我正在看这张图，通常很快会回你。"
    if normalized == "completed":
        if has_candidates:
            return "图片结果已经出来了。如果我刚给了你几个候选，直接回数字继续就行。"
        return "这张图已经处理完了。如果你没看到上一条结果，重发同一张图也可以。"
    if normalized == "failed":
        return "这张图刚才没处理成功。你可以直接重发一张，或者补发标题文字，我继续帮你对。"
    return "我还在帮你看这张图。"


def format_market_snapshot(*, candidate: dict[str, Any], snapshot: dict[str, Any]) -> str:
    title = _clean(
        snapshot.get("header", {}).get("title")
        or candidate.get("label")
        or candidate.get("family_title")
        or candidate.get("model_title")
        or "当前机型"
    )
    hero = dict(snapshot.get("hero") or {})
    market_v1 = dict(snapshot.get("market_v1") or {})
    latest_labels = list(snapshot.get("header", {}).get("selected_gprice_labels") or [])
    price_range = _clean(market_v1.get("price_range") or "--")
    market_price = hero.get("market_price")

    lines = [title]
    if price_range and price_range != "--":
        lines.append(f"现在大概区间：{price_range}")
    if market_price:
        lines.append(f"中间参考：¥{int(market_price):,}")
    if latest_labels:
        lines.append(f"最近报价日：{' / '.join(str(item) for item in latest_labels[:3])}")

    branch_lines = _branch_capacity_lines(snapshot)
    if branch_lines:
        lines.append("常见规格：")
        lines.extend(branch_lines)

    lines.append(_snapshot_follow_up_hint(snapshot))
    lines.append(f"完整页面：{WEB_URL}")
    return "\n".join(lines)


def format_market_capacity_refinement(
    *,
    candidate: dict[str, Any],
    snapshot: dict[str, Any],
    refinement_query: str,
    capacity_group: dict[str, Any],
) -> str:
    base_title = _clean(
        snapshot.get("header", {}).get("title")
        or candidate.get("label")
        or candidate.get("family_title")
        or candidate.get("model_title")
        or "当前机型"
    )
    title = _select_capacity_display_title(
        base_title=base_title,
        refinement_query=refinement_query,
        capacity_group=capacity_group,
    )
    capacity_label = _clean(capacity_group.get("capacity_label") or "这档规格")
    price_range = _clean(capacity_group.get("price_range") or "--")
    market_price = capacity_group.get("market_price")
    latest_labels = list(capacity_group.get("selected_gprice_labels") or snapshot.get("header", {}).get("selected_gprice_labels") or [])

    lines = [title]
    if _clean(refinement_query):
        lines.append(f"你刚刚补的是：{_clean(refinement_query)}")
    if price_range and price_range != "--":
        lines.append(f"这一档当前参考区间：{price_range}")
    if market_price:
        lines.append(f"中间参考：¥{int(market_price):,}")
    if latest_labels:
        lines.append(f"最近报价：{' / '.join(str(item) for item in latest_labels[:3])}")

    variant_lines = _capacity_variant_lines(capacity_group)
    if variant_lines:
        section_label = _capacity_section_label(capacity_group)
        lines.append(section_label)
        lines.extend(variant_lines)

    lines.append(_capacity_follow_up_hint(capacity_group))
    lines.append(f"完整页面：{WEB_URL}")
    return "\n".join(lines)


def format_market_variant_refinement(
    *,
    candidate: dict[str, Any],
    snapshot: dict[str, Any],
    refinement_query: str,
    capacity_group: dict[str, Any],
    variant: dict[str, Any],
) -> str:
    base_title = _clean(
        snapshot.get("header", {}).get("title")
        or candidate.get("label")
        or candidate.get("family_title")
        or candidate.get("model_title")
        or "当前机型"
    )
    price_range = _clean(variant.get("price_range") or capacity_group.get("price_range") or "--")
    market_price = variant.get("market_price") or capacity_group.get("market_price")
    latest_labels = list(variant.get("selected_gprice_labels") or capacity_group.get("selected_gprice_labels") or snapshot.get("header", {}).get("selected_gprice_labels") or [])
    capacity_label = _clean(capacity_group.get("capacity_label"))
    color_label = _clean(variant.get("group_title") or variant.get("color_label"))
    normalized_capacity = _normalize_capacity_label(capacity_label)
    normalized_variant = _normalize_variant_label(color_label)
    if (
        _should_prefer_variant_summary(capacity_label, color_label)
        or (normalized_capacity and normalized_capacity in normalized_variant)
    ):
        detail_label = color_label
    else:
        detail_label = " ".join(part for part in (capacity_label, color_label) if part).strip() or _clean(refinement_query)
    title = _select_variant_display_title(
        base_title=base_title,
        detail_label=detail_label,
    )

    lines = [title]
    if detail_label and _clean(detail_label).casefold() not in _clean(title).casefold():
        lines.append(f"当前这款：{detail_label}")
    if price_range and price_range != "--":
        lines.append(f"当前参考区间：{price_range}")
    if market_price:
        lines.append(f"中间参考：¥{int(market_price):,}")
    if latest_labels:
        lines.append(f"最近报价：{' / '.join(str(item) for item in latest_labels[:3])}")
    lines.append(_variant_follow_up_hint(capacity_group, variant))
    lines.append(f"完整页面：{WEB_URL}")
    return "\n".join(lines)


def _humanize_image_reason(reason: str) -> str:
    clean_reason = _clean(reason)
    if not clean_reason:
        return ""
    lowered = clean_reason.casefold()
    if "price list" in lowered or "quote sheet" in lowered or "table" in lowered:
        return "这张图里是整张表格或多条报价，不是单个商品详情页。"
    if "chat" in lowered or "conversation" in lowered:
        return "这张图更像聊天记录，商品关键信息不够集中。"
    if "embedded" in lowered or "thumbnail" in lowered:
        return "这张图里的商品截图太小了，标题和规格不够完整。"
    if "not enough" in lowered or "too blurry" in lowered or "unclear" in lowered:
        return "这张图里的标题或规格不够清楚，我没法稳妥地给你对行情。"
    return clean_reason


def _snapshot_follow_up_hint(snapshot: dict[str, Any]) -> str:
    examples = _snapshot_example_tokens(snapshot)
    if examples:
        return f"如果还想继续细化，直接补一句容量、配置、颜色或版本就行，比如 {examples}。"
    return "如果还想继续细化，直接补一句容量、配置、颜色或版本就行。"


def _capacity_follow_up_hint(capacity_group: dict[str, Any]) -> str:
    examples = _capacity_example_tokens(capacity_group)
    if examples:
        return f"如果还想继续细化，直接再补一个配置、颜色或版本就行，比如 {examples}。"
    return "如果还想继续细化，直接再补一个配置、颜色或版本就行。"


def _variant_follow_up_hint(capacity_group: dict[str, Any], variant: dict[str, Any]) -> str:
    examples = _variant_example_tokens(capacity_group, variant)
    if examples:
        return f"如果还想继续看别的颜色或版本，直接补一句就行，比如 {examples}。"
    return "如果还想继续看别的颜色或版本，直接补一句就行。"


def _snapshot_example_tokens(snapshot: dict[str, Any]) -> str:
    branches = list(snapshot.get("branches") or [])
    if not branches:
        return ""
    first_branch = dict(branches[0] or {})
    capacity_groups = list(first_branch.get("capacity_groups") or [])
    if not capacity_groups:
        return ""
    return _capacity_example_tokens(dict(capacity_groups[0] or {}))


def _capacity_example_tokens(capacity_group: dict[str, Any]) -> str:
    examples: list[str] = []
    capacity_label = _clean(capacity_group.get("capacity_label"))
    representative = _representative_variant(capacity_group)
    representative_label = _clean(representative.get("group_title") or representative.get("color_label"))
    if representative_label and _should_prefer_variant_summary(capacity_label, representative_label):
        examples.append(representative_label)
    elif capacity_label:
        examples.append(capacity_label)
    for item in list(capacity_group.get("colors") or [])[:3]:
        label = _clean(item.get("group_title") or item.get("color_label"))
        if label:
            examples.append(label)
    return _dedupe_examples(examples)


def _variant_example_tokens(capacity_group: dict[str, Any], variant: dict[str, Any]) -> str:
    examples: list[str] = []
    for item in list(capacity_group.get("colors") or [])[:4]:
        label = _clean(item.get("color_label") or item.get("group_title"))
        if label and label != _clean(variant.get("color_label") or variant.get("group_title")):
            examples.append(label)
    capacity_label = _clean(capacity_group.get("capacity_label"))
    normalized_capacity = _normalize_capacity_label(capacity_label)
    if capacity_label and normalized_capacity not in GENERIC_GROUP_LABELS:
        examples.append(capacity_label)
    return _dedupe_examples(examples)


def _dedupe_examples(examples: list[str], *, max_items: int = 3) -> str:
    ordered: list[str] = []
    seen: set[str] = set()
    for item in examples:
        clean = _clean(item)
        if not clean:
            continue
        key = clean.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(clean)
    return "、".join(ordered[:max_items])


def _branch_capacity_lines(snapshot: dict[str, Any], *, max_capacities: int = 4) -> list[str]:
    branches = list(snapshot.get("branches") or [])
    if not branches:
        return []
    first_branch = dict(branches[0] or {})
    capacity_groups = list(first_branch.get("capacity_groups") or [])
    lines: list[str] = []
    for group in capacity_groups[:max_capacities]:
        label = _group_summary_label(group)
        price_range = _clean(group.get("price_range") or "--")
        if label:
            lines.append(f"{label}：{price_range}")
    return lines


def _capacity_variant_lines(capacity_group: dict[str, Any], *, max_variants: int = 4) -> list[str]:
    variants = list(capacity_group.get("colors") or [])
    lines: list[str] = []
    for item in variants[:max_variants]:
        label = _clean(item.get("color_label") or item.get("group_title"))
        price_range = _clean(item.get("price_range") or "--")
        if label:
            lines.append(f"{label}：{price_range}")
    return lines


def _capacity_section_label(capacity_group: dict[str, Any]) -> str:
    capacity_label = _clean(capacity_group.get("capacity_label"))
    normalized = _normalize_capacity_label(capacity_label)
    if not capacity_label or normalized in GENERIC_GROUP_LABELS or _is_ram_only_capacity(normalized):
        return "这档常见配置："
    return f"{capacity_label} 常见颜色/版本："


def _select_capacity_display_title(
    *,
    base_title: str,
    refinement_query: str,
    capacity_group: dict[str, Any],
) -> str:
    clean_title = _clean(base_title)
    if not _title_needs_promotion(clean_title):
        return clean_title

    variants = [dict(item or {}) for item in list(capacity_group.get("colors") or []) if dict(item or {})]
    if len(variants) == 1:
        variant_label = _clean(variants[0].get("group_title") or variants[0].get("color_label"))
        if variant_label:
            return variant_label

    clean_query = _clean(refinement_query)
    if clean_query:
        return _humanize_promoted_title(clean_query)

    representative = _representative_variant(capacity_group)
    representative_label = _clean(representative.get("group_title") or representative.get("color_label"))
    return representative_label or clean_title


def _select_variant_display_title(*, base_title: str, detail_label: str) -> str:
    clean_title = _clean(base_title)
    if not _title_needs_promotion(clean_title):
        return clean_title
    return _clean(detail_label) or clean_title


def _title_needs_promotion(title: str) -> bool:
    clean_title = _clean(title)
    if not clean_title:
        return True
    if clean_title in GENERIC_MARKET_TITLES:
        return True
    compact = _normalize_variant_label(clean_title)
    if compact in {_normalize_variant_label(item) for item in GENERIC_MARKET_TITLES}:
        return True
    if "/" in clean_title and any(token in clean_title for token in ("充电头", "数据线", "配件")):
        return True
    return False


def _humanize_promoted_title(text: str) -> str:
    clean_text = _clean(text)
    if not clean_text:
        return ""
    parts: list[str] = []
    for part in clean_text.split():
        if not part.isascii():
            parts.append(part)
            continue
        if any(ch.isalpha() for ch in part):
            normalized = re.sub(
                r"[A-Za-z]+",
                lambda match: match.group(0).upper() if len(match.group(0)) <= 3 else match.group(0).title(),
                part,
            )
            parts.append(normalized)
        else:
            parts.append(part)
    return " ".join(parts)


def _group_summary_label(capacity_group: dict[str, Any]) -> str:
    capacity_label = _clean(capacity_group.get("capacity_label"))
    representative = _representative_variant(capacity_group)
    representative_label = _clean(
        (representative or {}).get("group_title")
        or (representative or {}).get("color_label")
    )
    if representative_label and _should_prefer_variant_summary(capacity_label, representative_label):
        return representative_label
    return capacity_label or representative_label


def _representative_variant(capacity_group: dict[str, Any]) -> dict[str, Any]:
    variants = [dict(item or {}) for item in list(capacity_group.get("colors") or []) if dict(item or {})]
    if not variants:
        return {}

    target_price = _coerce_price_number(capacity_group.get("market_price"))
    if target_price is not None:
        ranked = sorted(
            variants,
            key=lambda item: (
                abs((_coerce_price_number(item.get("market_price")) or target_price) - target_price),
                -_variant_information_score(item),
            ),
        )
        return ranked[0]

    ranked = sorted(
        variants,
        key=lambda item: (-_variant_information_score(item), _clean(item.get("group_title") or item.get("color_label"))),
    )
    return ranked[0]


def _coerce_price_number(value: Any) -> int | None:
    cleaned = _clean(value)
    if not cleaned:
        return None
    try:
        return int(float(cleaned))
    except ValueError:
        return None


def _variant_information_score(variant: dict[str, Any]) -> int:
    label = _clean(variant.get("group_title") or variant.get("color_label"))
    if not label:
        return 0
    score = len(label)
    lowered = label.casefold()
    if any(token in lowered for token in ("i3", "i5", "i7", "i9", "r5", "r7", "r9", "ultra", "core", "hx", "hs")):
        score += 120
    if any(token in lowered for token in ("mm", "esim", "蜂窝", "触屏", "纳米", "玻璃", "蜂巢", "hunter", "linux")):
        score += 90
    if re.search(r"\d+\s*(?:g|gb|t|tb)", lowered):
        score += 30
    return score


def _should_prefer_variant_summary(capacity_label: str, representative_label: str) -> bool:
    normalized_capacity = _normalize_capacity_label(capacity_label)
    normalized_variant = _normalize_variant_label(representative_label)
    if not normalized_variant:
        return False
    if not normalized_capacity:
        return True
    if normalized_capacity in GENERIC_GROUP_LABELS:
        return True
    if _is_ram_only_capacity(normalized_capacity):
        return True
    if normalized_variant == normalized_capacity:
        return False

    reduced_variant = normalized_variant.replace(normalized_capacity, "").strip()
    if not reduced_variant:
        return False
    lowered = reduced_variant.casefold()
    if any(token in lowered for token in ("i3", "i5", "i7", "i9", "r5", "r7", "r9", "ultra", "core", "mm", "esim", "蜂窝", "触屏", "纳米", "玻璃", "hunter", "linux")):
        return True
    return bool(re.search(r"\d+\s*(?:g|gb|t|tb)", lowered))


def _normalize_capacity_label(label: str) -> str:
    normalized = _clean(label).replace(" ", "").upper().replace("GB", "G").replace("TB", "T")
    return normalized


def _normalize_variant_label(label: str) -> str:
    return _clean(label).replace("GB", "G").replace("TB", "T")


def _is_ram_only_capacity(label: str) -> bool:
    match = re.fullmatch(r"(\d+)G", label)
    if not match:
        return False
    return int(match.group(1)) < 64 and int(match.group(1)) not in PHONE_STORAGE_VALUES
