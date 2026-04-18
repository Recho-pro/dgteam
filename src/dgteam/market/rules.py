from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from dgteam.core.quote_models import QuoteDecision
from dgteam.core.textio import read_json_utf8


DEFAULT_RULES_PATH = Path(__file__).resolve().parents[3] / "rules" / "default_rules.json"
DERIVED_FIELDS = [
    "clean_scope",
    "condition_bucket",
    "is_target_price",
    "needs_review",
    "matched_positive_tags",
    "matched_negative_tags",
    "matched_sale_tags",
    "exclude_reason",
    "rule_note",
]


def load_rules(path: Optional[Path] = None) -> Dict[str, Any]:
    rules_path = (path or DEFAULT_RULES_PATH).resolve()
    payload = read_json_utf8(rules_path)
    validate_rules(payload)
    return payload


def validate_rules(rules: Mapping[str, Any]) -> None:
    for key in ("crawler", "cleaning"):
        if key not in rules:
            raise ValueError(f"rules missing top-level field: {key}")
    if "quality_engine" in rules and not isinstance(rules.get("quality_engine"), Mapping):
        raise ValueError("rules quality_engine field must be an object when provided")


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


def join_text(parts: Iterable[str]) -> str:
    return " | ".join(part.strip() for part in parts if part and part.strip())


def matched_tags(text: str, patterns: Sequence[Mapping[str, str]]) -> List[str]:
    hits: List[str] = []
    for item in patterns:
        keyword = normalize_text(item.get("keyword"))
        tag = normalize_text(item.get("tag"))
        if keyword and keyword in text and tag:
            hits.append(tag)
    return hits


def matched_keywords(text: str, keywords: Sequence[Any]) -> List[str]:
    hits: List[str] = []
    for item in keywords:
        keyword = normalize_text(item)
        if keyword and keyword in text:
            hits.append(keyword)
    return hits


def build_recent_gprice_labels(history_days: int, now: Optional[datetime] = None) -> List[str]:
    current = now or datetime.now()
    day_count = max(1, int(history_days or 1))
    return [(current - timedelta(days=offset)).strftime("%m-%d") for offset in range(day_count)]


def extract_gprice_label(gprice: Any, rules: Mapping[str, Any]) -> str:
    pattern = rules["crawler"].get("gprice_pattern", r"(\d{2}-\d{2})")
    match = re.search(pattern, normalize_text(gprice))
    return match.group(1) if match else ""


def is_apple_brand(meta: Mapping[str, Any], rules: Mapping[str, Any]) -> bool:
    brand_title = normalize_text(meta.get("brand_title")).lower()
    keywords = [normalize_text(x).lower() for x in rules["crawler"]["apple"].get("brand_keywords", [])]
    return any(keyword and keyword in brand_title for keyword in keywords)


def should_keep_crawl_item(
    item: Mapping[str, Any],
    meta: Mapping[str, Any],
    rules: Mapping[str, Any],
    allowed_gprice_labels: Optional[Set[str]] = None,
    now: Optional[datetime] = None,
) -> Tuple[bool, str]:
    crawler_rules = rules["crawler"]
    allowed = allowed_gprice_labels or set(build_recent_gprice_labels(int(crawler_rules.get("history_days", 3)), now=now))
    gprice_text = normalize_text(item.get("GPrice"))
    gprice_label = extract_gprice_label(gprice_text, rules)
    if not gprice_label or gprice_label not in allowed:
        return False, "drop_by_date"
    if any(keyword in gprice_text for keyword in crawler_rules.get("out_of_stock_keywords", [])):
        return False, "drop_out_of_stock"

    dstatus = normalize_text(item.get("dstatus"))
    activation = normalize_text(item.get("Activation") or item.get("activation"))
    if activation:
        return False, "drop_by_activation_field"
    if matched_keywords(dstatus, crawler_rules.get("global_exclude_dstatus_keywords", [])):
        return False, "drop_by_global_dstatus"

    if is_apple_brand(meta, rules):
        apple_rules = crawler_rules["apple"]
        allow_keywords = [normalize_text(x) for x in apple_rules.get("allow_dstatus_keywords", [])]
        if allow_keywords and not any(keyword in dstatus for keyword in allow_keywords):
            return False, "drop_by_apple_dstatus"
        if any(keyword in dstatus for keyword in apple_rules.get("exclude_dstatus_keywords", [])):
            return False, "drop_by_apple_dstatus"
    else:
        non_apple_rules = crawler_rules["non_apple"]
        if any(keyword in dstatus for keyword in non_apple_rules.get("exclude_dstatus_keywords", [])):
            return False, "drop_by_non_apple_dstatus"

    return True, ""


def classify_row(row: Mapping[str, Any], rules: Mapping[str, Any]) -> Dict[str, str]:
    cleaning = rules["cleaning"]
    brand = normalize_text(row.get("brand_title"))
    series = normalize_text(row.get("series_title"))
    model = normalize_text(row.get("model_title"))
    group = normalize_text(row.get("group_title"))
    dstatus = normalize_text(row.get("dstatus"))
    activation = normalize_text(row.get("activation"))
    text = join_text([series, model, group, dstatus])

    positive_tags = sorted(set(matched_tags(text, cleaning.get("positive_keywords", []))))
    negative_tags = sorted(set(matched_tags(text, cleaning.get("negative_keywords", []))))
    sale_tags = sorted(set(matched_tags(text, cleaning.get("sale_keywords", []))))
    if activation:
        negative_tags = sorted(set([*negative_tags, "activation_date_present"]))
    hard_exclude_negative_tags = {
        normalize_text(tag)
        for tag in cleaning.get("hard_exclude_negative_tags", [])
        if normalize_text(tag)
    }
    hard_negative_hits = [tag for tag in negative_tags if tag in hard_exclude_negative_tags]

    is_apple = brand == normalize_text(cleaning.get("apple_brand"))
    target_keywords = [
        normalize_text(keyword)
        for keyword in cleaning.get("apple_target_dstatus_keywords", [])
        if normalize_text(keyword)
    ]
    if not target_keywords:
        target_substring = normalize_text(cleaning.get("apple_target_dstatus_substring"))
        if target_substring:
            target_keywords = [target_substring]
    has_company_pure_sealed = any(keyword in dstatus for keyword in target_keywords)
    has_yuanfeng = "factory_sealed" in positive_tags
    has_chunyuan = "pure_original" in positive_tags
    special_model_keywords = ("官换", "资源机", "演示")
    has_special_model_tag = any(keyword in text for keyword in special_model_keywords)

    if not is_apple:
        non_apple_excluded = any(
            keyword in dstatus
            for keyword in rules["crawler"]["non_apple"].get("exclude_dstatus_keywords", [])
        )
        exclude_reasons = list(hard_negative_hits)
        if non_apple_excluded and "non_apple_dstatus_keyword" not in exclude_reasons:
            exclude_reasons.append("non_apple_dstatus_keyword")
        decision = QuoteDecision(
            keep=not (non_apple_excluded or bool(hard_negative_hits)),
            reason="|".join(exclude_reasons),
            scope="non_apple_guarded",
            note="non-apple rows are kept unless they hit hard exclusion tags or the non-apple dstatus exclusion list",
            matched_positive_tags=positive_tags,
            matched_negative_tags=negative_tags,
            matched_sale_tags=sale_tags,
            condition_bucket=(
                "non_apple_hard_excluded"
                if hard_negative_hits
                else ("non_apple_excluded" if non_apple_excluded else "non_apple_allowed")
            ),
            is_target_price=not (non_apple_excluded or bool(hard_negative_hits)),
            needs_review=False,
        )
        return decision.to_clean_fields()

    decision = QuoteDecision(
        keep=False,
        reason="",
        scope="apple_strict",
        note="apple rows must contain company pure sealed status and must not contain activation or regional restriction qualifiers",
        matched_positive_tags=positive_tags,
        matched_negative_tags=negative_tags,
        matched_sale_tags=sale_tags,
        condition_bucket="apple_unknown_review",
        is_target_price=False,
        needs_review=False,
    )

    if has_special_model_tag:
        decision.condition_bucket = "apple_special_model_non_target"
        decision.reason = "special_model_tag"
    elif has_company_pure_sealed and not negative_tags:
        decision.keep = True
        decision.condition_bucket = "apple_company_pure_sealed_target"
        decision.is_target_price = True
        decision.note = "dstatus contains company pure sealed status and has no activation or regional restriction qualifiers"
    elif has_company_pure_sealed and negative_tags:
        decision.condition_bucket = "apple_company_pure_sealed_but_excluded"
        decision.reason = "|".join(negative_tags)
    elif has_yuanfeng and negative_tags:
        decision.condition_bucket = "apple_factory_sealed_but_excluded"
        decision.reason = "|".join(negative_tags)
    elif has_yuanfeng:
        decision.condition_bucket = "apple_non_company_factory_sealed_non_target"
        decision.reason = "not_company_pure_sealed"
    elif "opened_not_activated" in negative_tags:
        decision.condition_bucket = "apple_opened_not_activated"
        decision.reason = "opened_not_activated"
    elif any(tag in negative_tags for tag in ("opened", "activated", "activation_date_present")):
        decision.condition_bucket = "apple_opened_or_activated"
        decision.reason = "|".join(negative_tags)
    elif has_chunyuan:
        decision.condition_bucket = "apple_pure_original_non_target"
        decision.reason = "pure_original_without_clean_sealed_status"
    elif negative_tags:
        decision.condition_bucket = "apple_flagged_non_target"
        decision.reason = "|".join(negative_tags)
    elif sale_tags:
        decision.condition_bucket = "apple_condition_unspecified_non_target"
        decision.reason = "condition_unspecified"
        decision.note = "apple row has sales tags only and no safe sealed marker"
    else:
        decision.condition_bucket = "apple_unknown_review"
        decision.reason = "unknown_apple_condition"
        decision.needs_review = True
        decision.note = "apple row did not match a safe sealed/unsealed rule"

    return decision.to_clean_fields()
