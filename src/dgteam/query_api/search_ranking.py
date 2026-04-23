from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Sequence, Set, Tuple

PRIMARY_DEVICE_SERIES_HINTS: Set[str] = {"iphone", "mate", "pura", "nova", "magic", "note"}
ACCESSORY_LIKE_CATEGORIES: Set[str] = {"charger", "case", "accessory"}


def contains_any_keyword(text: str, keywords: Iterable[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def candidate_keyword_text(candidate: Dict[str, Any]) -> str:
    return " ".join(
        str(candidate.get(field) or "").strip().lower()
        for field in ("brand_title", "series_title", "family_title", "model_title")
    )


def candidate_matches_category_intents(
    candidate: Dict[str, Any],
    intents: Set[str],
    *,
    charger_keywords: Sequence[str],
    case_keywords: Sequence[str],
    accessory_keywords: Sequence[str],
) -> Tuple[bool, bool]:
    if not intents:
        return True, False

    category_kind = str(candidate.get("category_kind") or "phone")
    if category_kind in intents:
        return True, True

    text = candidate_keyword_text(candidate)
    soft_match = (
        ("charger" in intents and contains_any_keyword(text, charger_keywords))
        or ("case" in intents and contains_any_keyword(text, case_keywords))
        or ("accessory" in intents and contains_any_keyword(text, accessory_keywords))
    )
    return soft_match, False


def query_prefers_primary_device_results(context: Any) -> bool:
    category_intents = set(getattr(context, "category_intents", set()) or set())
    if category_intents:
        return False
    if getattr(context, "bare_model_query", False):
        return True
    series_hints = set(getattr(context, "series_hints", set()) or set())
    return bool(series_hints & PRIMARY_DEVICE_SERIES_HINTS)


def candidate_bucket_priority(candidate: Dict[str, Any]) -> int:
    if str(candidate.get("data_source") or "quote_rows").strip() != "quote_rows":
        return 0
    bucket = str(candidate.get("condition_bucket") or "").strip()
    if bucket == "apple_company_pure_sealed_target":
        return 120
    if bucket == "non_apple_allowed":
        return 110
    if bucket.endswith("_target"):
        return 100
    if "allowed" in bucket:
        return 90
    if "review" in bucket:
        return 30
    if "excluded" in bucket:
        return 10
    if "non_target" in bucket:
        return 5
    return 40 if bucket else 0


def candidate_preference_tuple(
    candidate: Dict[str, Any],
    *,
    safe_int: Callable[[Any], int],
) -> Tuple[int, int, int, int, str]:
    return (
        candidate_bucket_priority(candidate),
        safe_int(candidate.get("row_count")),
        safe_int(candidate.get("variant_count")),
        safe_int(candidate.get("source_count")),
        str(candidate.get("latest_imported_at") or ""),
    )


def model_identity_key(candidate: Dict[str, Any]) -> Tuple[str, str, str, str]:
    return (
        str(candidate.get("data_source") or "quote_rows").strip(),
        str(candidate.get("brand_title") or "").strip(),
        str(candidate.get("series_title") or "").strip(),
        str(candidate.get("family_title") or "").strip(),
    )


def hot_candidate_priority(
    candidate: Dict[str, Any],
    *,
    safe_int: Callable[[Any], int],
) -> Tuple[int, int, int, int, int, str]:
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


def build_hot_query_candidates(
    candidates: List[Dict[str, Any]],
    *,
    limit: int = 8,
    safe_int: Callable[[Any], int],
) -> List[Dict[str, Any]]:
    ordered = sorted(candidates, key=lambda item: hot_candidate_priority(item, safe_int=safe_int), reverse=True)
    phones = [candidate for candidate in ordered if str(candidate.get("category_kind") or "phone") == "phone"]
    if len(phones) >= limit:
        return phones[:limit]

    selected: List[Dict[str, Any]] = list(phones)
    selected_keys = {model_identity_key(candidate) for candidate in phones}
    for candidate in ordered:
        identity = model_identity_key(candidate)
        if identity in selected_keys:
            continue
        selected.append(candidate)
        selected_keys.add(identity)
        if len(selected) >= limit:
            break
    return selected[:limit]


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


def score_candidate(
    candidate: Dict[str, Any],
    *,
    context: Any,
    safe_int: Callable[[Any], int],
    charger_keywords: Sequence[str],
    case_keywords: Sequence[str],
    accessory_keywords: Sequence[str],
) -> int:
    if not getattr(context, "variants", None) or getattr(context, "only_refinement", False):
        return -1

    primary_texts = [
        str(candidate.get("model_group_normalized") or ""),
        *[str(value or "") for value in candidate.get("branch_titles_normalized") or []],
    ]
    secondary_texts = [
        str(candidate.get("search_text_normalized") or ""),
        *[str(value or "") for value in candidate.get("variant_texts_normalized") or []],
    ]
    label_text = str(candidate.get("family_only_normalized") or "")
    series_text = str(candidate.get("series_only_normalized") or "")
    alias_texts = [str(value or "") for value in candidate.get("family_aliases_normalized") or []]
    category_kind = str(candidate.get("category_kind") or "phone")

    best_score = -1
    for query_norm, query_tokens, _surface in getattr(context, "variants", []):
        variant_score = _score_query_variant(
            query_norm,
            query_tokens,
            label_text=label_text,
            series_text=series_text,
            alias_texts=alias_texts,
            primary_texts=primary_texts,
            secondary_texts=secondary_texts,
        )
        best_score = max(best_score, variant_score)

    best_core_score = -1
    for query_norm, query_tokens, _surface in getattr(context, "core_variants", []):
        variant_score = _score_query_variant(
            query_norm,
            query_tokens,
            label_text=label_text,
            series_text=series_text,
            alias_texts=alias_texts,
            primary_texts=primary_texts,
            secondary_texts=secondary_texts,
        )
        best_core_score = max(best_core_score, variant_score)

    if best_core_score >= 0:
        best_score = max(best_score, best_core_score + 720)
    if best_score < 0:
        return -1

    brand_hints = set(getattr(context, "brand_hints", set()) or set())
    candidate_brand = str(candidate.get("brand_title") or "").strip()
    if brand_hints:
        if candidate_brand in brand_hints:
            best_score += 1850
        else:
            best_score -= 900

    series_hints = set(getattr(context, "series_hints", set()) or set())
    if series_hints:
        combined_text = " ".join([label_text, series_text, *alias_texts, *primary_texts, *secondary_texts])
        matched_series = sum(1 for hint in series_hints if hint in combined_text)
        if matched_series:
            best_score += matched_series * 950
        else:
            best_score -= 850

    category_intents = set(getattr(context, "category_intents", set()) or set())
    if category_intents:
        category_match, exact_category_match = candidate_matches_category_intents(
            candidate,
            category_intents,
            charger_keywords=charger_keywords,
            case_keywords=case_keywords,
            accessory_keywords=accessory_keywords,
        )
        if category_match and exact_category_match:
            best_score += 1450
        elif category_match:
            best_score += 820
        else:
            return -1
    elif getattr(context, "bare_model_query", False):
        if category_kind == "phone":
            best_score += 520
        else:
            best_score -= 900
    else:
        if category_kind == "phone":
            best_score += 120
        elif category_kind in {"charger", "case"}:
            best_score -= 420
        elif category_kind in {"earbuds", "tablet", "wearable", "laptop"}:
            best_score -= 180

    if query_prefers_primary_device_results(context):
        if category_kind == "phone":
            best_score += 260
        elif category_kind in ACCESSORY_LIKE_CATEGORIES:
            return -1
        elif category_kind in {"earbuds", "tablet", "wearable", "laptop"}:
            best_score -= 540

    variant_texts = [str(value or "") for value in candidate.get("variant_texts_normalized") or []]
    capacity_hints = set(getattr(context, "capacity_hints", set()) or set())
    if capacity_hints:
        matched_capacity = sum(1 for hint in capacity_hints if any(hint in text for text in variant_texts))
        if matched_capacity:
            best_score += matched_capacity * 220
        elif getattr(context, "core_variants", None):
            best_score -= min(len(capacity_hints) * 120, 280)
    color_hints = set(getattr(context, "color_hints", set()) or set())
    if color_hints:
        matched_colors = sum(1 for hint in color_hints if any(hint in text for text in variant_texts))
        if matched_colors:
            best_score += matched_colors * 180

    best_score += min(safe_int(candidate.get("row_count")), 500)
    best_score += min(safe_int(candidate.get("variant_count")) * 25, 180)
    best_score += min(safe_int(candidate.get("source_count")) * 4, 120)
    return best_score
