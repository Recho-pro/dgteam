from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .price_cleaning import (
    build_price_stats,
    clean_price_rows,
    dedupe_latest_rows,
    numeric_rows,
    percentile,
    select_recent_day_window,
)
from .rules import classify_row, is_apple_brand, load_rules, normalize_text


def _string_list(values: Sequence[Any]) -> List[str]:
    return [str(value or "").strip() for value in values if str(value or "").strip()]


def _quality_config(rules: Mapping[str, Any]) -> Dict[str, Any]:
    payload = dict(rules.get("quality_engine") or {})
    payload.setdefault("day_selection", {})
    payload.setdefault("status_filter", {})
    payload.setdefault("interval_generation", {})
    payload.setdefault("scoring", {})
    return payload


def _is_target_price(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes"}


def _enrich_row(row: Mapping[str, Any], rules: Mapping[str, Any]) -> Dict[str, Any]:
    copied = dict(row)
    copied.update(classify_row(copied, rules))
    return copied


def _status_noise_keywords(
    *,
    brand_title: str,
    rules: Mapping[str, Any],
    quality_config: Mapping[str, Any],
) -> List[str]:
    crawler = dict(rules.get("crawler") or {})
    keywords: List[str] = []
    keywords.extend(_string_list(crawler.get("global_exclude_dstatus_keywords") or []))
    keywords.extend(_string_list(dict(quality_config.get("status_filter") or {}).get("exclude_noise_keywords") or []))
    if is_apple_brand({"brand_title": brand_title}, rules):
        keywords.extend(_string_list(dict(crawler.get("apple") or {}).get("exclude_dstatus_keywords") or []))
    else:
        keywords.extend(_string_list(dict(crawler.get("non_apple") or {}).get("exclude_dstatus_keywords") or []))

    deduped: List[str] = []
    seen: set[str] = set()
    for keyword in keywords:
        if keyword and keyword not in seen:
            seen.add(keyword)
            deduped.append(keyword)
    return deduped


def _filter_target_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    brand_title: str,
    rules: Mapping[str, Any],
    quality_config: Mapping[str, Any],
) -> Dict[str, Any]:
    eligible_rows: List[Dict[str, Any]] = []
    rejected_rows: List[Dict[str, Any]] = []
    rejected_reason_counts: Counter[str] = Counter()
    rejected_keyword_counts: Counter[str] = Counter()
    noise_keywords = _status_noise_keywords(brand_title=brand_title, rules=rules, quality_config=quality_config)

    for row in rows:
        enriched = _enrich_row(row, rules)
        dstatus = normalize_text(enriched.get("dstatus"))
        activation = normalize_text(enriched.get("activation"))
        matched_noise = [keyword for keyword in noise_keywords if keyword and keyword in dstatus]
        if activation:
            enriched["quality_reject_reason"] = "activation_field_present"
            rejected_rows.append(enriched)
            rejected_reason_counts["activation_field_present"] += 1
            continue
        if matched_noise:
            enriched["quality_reject_reason"] = "status_noise_keyword"
            enriched["quality_reject_keywords"] = "|".join(matched_noise)
            rejected_rows.append(enriched)
            rejected_reason_counts["status_noise_keyword"] += 1
            rejected_keyword_counts.update(matched_noise)
            continue
        if not _is_target_price(enriched.get("is_target_price")):
            condition_bucket = normalize_text(enriched.get("condition_bucket")) or "unknown"
            enriched["quality_reject_reason"] = "condition_not_target"
            enriched["quality_reject_condition_bucket"] = condition_bucket
            rejected_rows.append(enriched)
            rejected_reason_counts["condition_not_target"] += 1
            rejected_keyword_counts[condition_bucket] += 1
            continue
        eligible_rows.append(enriched)

    eligible_numeric_rows = numeric_rows(eligible_rows)
    non_numeric_count = max(0, len(eligible_rows) - len(eligible_numeric_rows))
    if non_numeric_count:
        rejected_reason_counts["missing_numeric_price"] += non_numeric_count

    return {
        "eligible_rows": eligible_rows,
        "eligible_numeric_rows": eligible_numeric_rows,
        "rejected_rows": rejected_rows,
        "rejected_reason_counts": dict(rejected_reason_counts),
        "rejected_keyword_counts": dict(rejected_keyword_counts),
        "eligible_row_count": len(eligible_rows),
        "eligible_numeric_row_count": len(eligible_numeric_rows),
        "rejected_row_count": len(rejected_rows) + non_numeric_count,
        "noise_keywords": noise_keywords,
    }


def _all_day_summaries(rows: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    if not rows:
        return []
    window = select_recent_day_window(
        rows,
        min_samples=max(1, len(rows)),
        max_labels=max(1, len(rows)),
    )
    return list(window.get("selected_day_summaries") or [])


def _price_gap_threshold(values: Sequence[int]) -> int:
    if not values:
        return 80
    quality_gap = max(0, percentile(values, 0.75) - percentile(values, 0.25))
    center = percentile(values, 0.5)
    return max(80, round(center * 0.012), round(quality_gap * 1.5))


def split_price_clusters(rows: Sequence[Mapping[str, Any]]) -> List[List[Dict[str, Any]]]:
    ordered = sorted((dict(row) for row in rows), key=lambda row: int(row["price_value"]))
    if len(ordered) <= 2:
        return [ordered] if ordered else []

    values = [int(row["price_value"]) for row in ordered]
    gap_threshold = _price_gap_threshold(values)
    clusters: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = [ordered[0]]
    for index in range(1, len(ordered)):
        previous_value = int(ordered[index - 1]["price_value"])
        current_value = int(ordered[index]["price_value"])
        if current_value - previous_value >= gap_threshold:
            clusters.append(current)
            current = [ordered[index]]
        else:
            current.append(ordered[index])
    if current:
        clusters.append(current)
    return clusters


def _status_priority(
    *,
    brand_title: str,
    dstatus: str,
    quality_config: Mapping[str, Any],
) -> int:
    scoring = dict(quality_config.get("scoring") or {})
    status_weights = dict(scoring.get("status_priority") or {})
    if is_apple_brand({"brand_title": brand_title}, {"crawler": {"apple": {"brand_keywords": ["苹果", "apple"]}}}):
        if dstatus == "公司纯原封":
            return int(status_weights.get("apple_exact", 36))
        if dstatus == "公司纯原封/可出全国":
            return int(status_weights.get("apple_nationwide", 30))
        if "公司纯原封" in dstatus:
            return int(status_weights.get("apple_contains_company_pure_sealed", 22))
        if "全国纯原" in dstatus:
            return int(status_weights.get("apple_nationwide", 30))
        return int(status_weights.get("apple_other", 0))
    if dstatus:
        return int(status_weights.get("default_with_status", 10))
    return int(status_weights.get("default_without_status", 6))


def _candidate_score(
    *,
    brand_title: str,
    dstatus: str,
    prices: Sequence[int],
    reference_price: Optional[int],
    interval_low: int,
    interval_high: int,
    quality_config: Mapping[str, Any],
) -> float:
    if not prices:
        return -10**9

    scoring = dict(quality_config.get("scoring") or {})
    count_weight = float(scoring.get("count_weight", 14.0))
    spread_penalty_divisor = float(scoring.get("spread_penalty_divisor", 18.0))
    reference_penalty_divisor = float(scoring.get("reference_penalty_divisor", 45.0))
    reference_penalty_cap = int(scoring.get("reference_penalty_cap", 2000))
    singleton_penalty = float(scoring.get("single_sample_penalty", 14.0))
    pair_penalty = float(scoring.get("two_sample_penalty", 6.0))

    seller_count = len(prices)
    spread = max(0, interval_high - interval_low)
    median_price = percentile(prices, 0.5)
    status_bonus = _status_priority(brand_title=brand_title, dstatus=dstatus, quality_config=quality_config)
    score = seller_count * count_weight + status_bonus - (min(spread, 1600) / max(spread_penalty_divisor, 1.0))
    if seller_count == 1:
        score -= singleton_penalty
    elif seller_count == 2:
        score -= pair_penalty
    if reference_price is not None:
        score -= min(abs(median_price - reference_price), reference_penalty_cap) / max(reference_penalty_divisor, 1.0)
    return round(score, 3)


def _confidence_label(
    *,
    prices: Sequence[int],
    reference_price: Optional[int],
) -> Tuple[int, str]:
    if not prices:
        return (0, "pending data")
    median_price = percentile(prices, 0.5)
    q1 = percentile(prices, 0.25)
    q3 = percentile(prices, 0.75)
    spread_ratio = (q3 - q1) / median_price if median_price else 1
    score = min(len(prices), 20) * 3
    if spread_ratio <= 0.01:
        score += 30
    elif spread_ratio <= 0.02:
        score += 22
    elif spread_ratio <= 0.04:
        score += 12
    else:
        score += 4
    if reference_price is not None:
        diff_ratio = abs(median_price - reference_price) / max(reference_price, 1)
        if diff_ratio <= 0.015:
            score += 18
        elif diff_ratio <= 0.03:
            score += 10
        elif diff_ratio <= 0.06:
            score += 4
    score = max(0, min(98, int(round(score))))
    if score >= 80:
        return (score, "high")
    if score >= 58:
        return (score, "medium")
    return (score, "cautious")


def _interval_from_prices(
    prices: Sequence[int],
    *,
    quality_config: Mapping[str, Any],
) -> Tuple[int, int, str]:
    if not prices:
        return (0, 0, "empty")
    ordered = sorted(int(price) for price in prices)
    interval_config = dict(quality_config.get("interval_generation") or {})
    single_mode = str(interval_config.get("single_quote_mode", "exact")).strip() or "exact"
    sparse_minmax_max_count = int(interval_config.get("sparse_minmax_max_count", 3) or 3)
    iqr_min_sample_count = int(interval_config.get("iqr_min_sample_count", 4) or 4)
    single_padding_floor = int(interval_config.get("single_quote_padding_floor", 0) or 0)
    single_padding_ratio = float(interval_config.get("single_quote_padding_ratio", 0.0) or 0.0)

    if len(ordered) == 1:
        value = ordered[0]
        padding = max(single_padding_floor, int(round(value * single_padding_ratio)))
        if single_mode == "padded" and padding > 0:
            return (value - padding, value + padding, "single_quote_padded")
        return (value, value, "single_quote_exact")
    if len(ordered) <= sparse_minmax_max_count:
        return (ordered[0], ordered[-1], "observed_minmax")
    if len(ordered) < iqr_min_sample_count:
        return (ordered[0], ordered[-1], "sparse_observed_minmax")
    return (percentile(ordered, 0.25), percentile(ordered, 0.75), "iqr_band")


def _latest_day_summary(
    *,
    raw_numeric_day_summaries: Sequence[Mapping[str, Any]],
    eligible_numeric_day_summaries: Sequence[Mapping[str, Any]],
    selected_day_label: str,
    selected_window: Mapping[str, Any],
) -> str:
    selected_count = int(selected_window.get("selected_row_count") or 0)
    latest_raw_day = str((raw_numeric_day_summaries[0] if raw_numeric_day_summaries else {}).get("day_label") or "").strip()
    latest_eligible_day = str((eligible_numeric_day_summaries[0] if eligible_numeric_day_summaries else {}).get("day_label") or "").strip()
    selected_gprice_labels = _string_list(selected_window.get("selected_gprice_labels") or [])
    selected_gprice = selected_gprice_labels[0] if selected_gprice_labels else ""
    day_label = selected_gprice or selected_day_label or latest_eligible_day or latest_raw_day or "unlabeled history"
    if latest_raw_day and selected_day_label and latest_raw_day != selected_day_label:
        return (
            f"selected {selected_count} rows from latest valid day {day_label} "
            f"after raw latest day {latest_raw_day} had no valid rows"
        )
    return f"selected {selected_count} rows from latest valid day {day_label}"


def build_data_quality_market(
    *,
    rows: Sequence[Mapping[str, Any]],
    brand_title: str,
    reference_price: Optional[int] = None,
    rules: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    effective_rules = dict(rules or load_rules())
    quality_config = _quality_config(effective_rules)
    day_selection_config = dict(quality_config.get("day_selection") or {})
    day_min_samples = int(day_selection_config.get("min_samples", 1) or 1)
    day_max_labels = int(day_selection_config.get("max_labels", 1) or 1)

    deduped_rows = dedupe_latest_rows(rows)
    numeric_deduped_rows = numeric_rows(deduped_rows)
    raw_numeric_day_summaries = _all_day_summaries(numeric_deduped_rows)
    filtered = _filter_target_rows(
        deduped_rows,
        brand_title=brand_title,
        rules=effective_rules,
        quality_config=quality_config,
    )
    eligible_numeric_rows = list(filtered.get("eligible_numeric_rows") or [])
    eligible_numeric_day_summaries = _all_day_summaries(eligible_numeric_rows)

    if not numeric_deduped_rows:
        return {
            "ok": False,
            "reason": "no_numeric_rows",
            "selected_gprice_labels": [],
            "selection": {
                "mode": "latest_valid_day_only",
                "fallback_applied": False,
                "single_day_first": True,
                "selected_labels": [],
                "selected_label_count": 0,
                "selected_day_labels": [],
                "selected_day_count": 0,
                "selected_row_count": 0,
                "deduped_row_count": len(deduped_rows),
                "numeric_row_count": 0,
                "target_min_samples": day_min_samples,
                "max_labels": day_max_labels,
                "available_labels": [],
                "selected_label_samples": [],
                "available_day_labels": [],
                "available_day_count": 0,
                "selected_day_samples": [],
                "available_day_samples": [],
            },
            "quality_engine": {
                "deduped_row_count": len(deduped_rows),
                "numeric_row_count": 0,
                "eligible_row_count": int(filtered.get("eligible_row_count") or 0),
                "eligible_numeric_row_count": 0,
                "filter_reason_counts": dict(filtered.get("rejected_reason_counts") or {}),
                "filter_keyword_counts": dict(filtered.get("rejected_keyword_counts") or {}),
                "raw_numeric_day_summaries": raw_numeric_day_summaries,
                "eligible_numeric_day_summaries": eligible_numeric_day_summaries,
                "selected_day_label": "",
                "selected_day_gprice_label": "",
                "selected_day_row_count": 0,
                "candidate_count": 0,
                "low_quality_fallback": False,
            },
            "explanation": {
                "selection_mode": "latest_valid_day_only",
                "selection_summary": "selected 0 rows from latest valid day",
                "quality_summary": "no numeric rows available after dedupe",
            },
        }

    if not eligible_numeric_rows:
        latest_raw_day = str((raw_numeric_day_summaries[0] if raw_numeric_day_summaries else {}).get("day_label") or "").strip()
        return {
            "ok": False,
            "reason": "no_valid_rows_after_status_filter",
            "selected_gprice_labels": [],
            "selection": {
                "mode": "latest_valid_day_only",
                "fallback_applied": False,
                "single_day_first": True,
                "selected_labels": [],
                "selected_label_count": 0,
                "selected_day_labels": [],
                "selected_day_count": 0,
                "selected_row_count": 0,
                "deduped_row_count": len(deduped_rows),
                "numeric_row_count": len(numeric_deduped_rows),
                "target_min_samples": day_min_samples,
                "max_labels": day_max_labels,
                "available_labels": [],
                "selected_label_samples": [],
                "available_day_labels": [latest_raw_day] if latest_raw_day else [],
                "available_day_count": len(raw_numeric_day_summaries),
                "selected_day_samples": [],
                "available_day_samples": raw_numeric_day_summaries,
            },
            "quality_engine": {
                "deduped_row_count": len(deduped_rows),
                "numeric_row_count": len(numeric_deduped_rows),
                "eligible_row_count": int(filtered.get("eligible_row_count") or 0),
                "eligible_numeric_row_count": 0,
                "filter_reason_counts": dict(filtered.get("rejected_reason_counts") or {}),
                "filter_keyword_counts": dict(filtered.get("rejected_keyword_counts") or {}),
                "raw_numeric_day_summaries": raw_numeric_day_summaries,
                "eligible_numeric_day_summaries": eligible_numeric_day_summaries,
                "selected_day_label": "",
                "selected_day_gprice_label": "",
                "selected_day_row_count": 0,
                "candidate_count": 0,
                "low_quality_fallback": False,
            },
            "explanation": {
                "selection_mode": "latest_valid_day_only",
                "selection_summary": "selected 0 rows from latest valid day",
                "quality_summary": "all numeric rows were filtered out by status rules",
            },
        }

    selected_window = select_recent_day_window(
        eligible_numeric_rows,
        min_samples=day_min_samples,
        max_labels=day_max_labels,
    )
    selected_rows = list(selected_window.get("selected_rows") or [])
    selected_gprice_labels = _string_list(selected_window.get("selected_gprice_labels") or [])
    selected_day_labels = _string_list(selected_window.get("selected_day_labels") or [])
    selected_day_label = selected_day_labels[0] if selected_day_labels else ""
    selected_day_gprice_label = selected_gprice_labels[0] if selected_gprice_labels else ""
    raw_latest_day = str((raw_numeric_day_summaries[0] if raw_numeric_day_summaries else {}).get("day_label") or "").strip()
    fallback_applied = bool(raw_latest_day and selected_day_label and raw_latest_day != selected_day_label)

    selection = {
        "mode": "latest_valid_day_only",
        "fallback_applied": fallback_applied,
        "single_day_first": True,
        "selected_labels": list(selected_gprice_labels),
        "selected_label_count": len(selected_gprice_labels),
        "selected_day_labels": list(selected_day_labels),
        "selected_day_count": len(selected_day_labels),
        "selected_row_count": len(selected_rows),
        "deduped_row_count": len(deduped_rows),
        "numeric_row_count": len(numeric_deduped_rows),
        "target_min_samples": day_min_samples,
        "max_labels": day_max_labels,
        "available_labels": [
            {
                "label": str(item.get("gprice_label") or item.get("day_label") or "").strip(),
                "sample_count": int(item.get("sample_count") or 0),
                "latest_imported_at": str(item.get("latest_imported_at") or ""),
            }
            for item in eligible_numeric_day_summaries
        ],
        "selected_label_samples": [
            {
                "label": selected_day_gprice_label or selected_day_label,
                "sample_count": len(selected_rows),
                "latest_imported_at": max((str(row.get("imported_at") or "") for row in selected_rows), default=""),
            }
        ]
        if selected_rows
        else [],
        "available_day_labels": [str(item.get("day_label") or "").strip() for item in eligible_numeric_day_summaries if str(item.get("day_label") or "").strip()],
        "available_day_count": len(eligible_numeric_day_summaries),
        "selected_day_samples": list(selected_window.get("selected_day_summaries") or []),
        "available_day_samples": eligible_numeric_day_summaries,
    }

    status_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in selected_rows:
        status_groups[normalize_text(row.get("dstatus")) or "unlabeled"].append(dict(row))

    candidates: List[Dict[str, Any]] = []
    candidate_index = 0
    for dstatus, status_rows in status_groups.items():
        clean_result = clean_price_rows(status_rows, config=quality_config)
        cleaned_rows = list(clean_result.get("kept_rows") or [])
        if not cleaned_rows:
            continue
        for cluster_rows in split_price_clusters(cleaned_rows):
            prices = [int(row["price_value"]) for row in cluster_rows]
            if not prices:
                continue
            stats = build_price_stats(prices)
            interval_low, interval_high, interval_method = _interval_from_prices(prices, quality_config=quality_config)
            score = _candidate_score(
                brand_title=brand_title,
                dstatus=dstatus,
                prices=prices,
                reference_price=reference_price,
                interval_low=interval_low,
                interval_high=interval_high,
                quality_config=quality_config,
            )
            confidence_score, confidence_text = _confidence_label(prices=prices, reference_price=reference_price)
            candidates.append(
                {
                    "cluster_id": candidate_index,
                    "dstatus": dstatus,
                    "sample_count": len(cluster_rows),
                    "seller_count": len(cluster_rows),
                    "median_price": int(stats.get("market_price") or 0),
                    "price_min": int(stats.get("price_min") or 0),
                    "price_max": int(stats.get("price_max") or 0),
                    "band_low": interval_low,
                    "band_high": interval_high,
                    "price_range": f"{interval_low}-{interval_high}",
                    "interval_method": interval_method,
                    "score": score,
                    "confidence_score": confidence_score,
                    "confidence_label": confidence_text,
                    "rows": [dict(row) for row in cluster_rows],
                    "cleaning_profile": str(clean_result.get("cleaning_profile") or ""),
                    "cleaning_retention_floor": int(clean_result.get("cleaning_retention_floor") or 0),
                    "removed_count": int(clean_result.get("removed_count") or 0),
                    "removed_reason_counts": dict(clean_result.get("removed_reason_counts") or {}),
                    "methods_triggered": list(clean_result.get("methods_triggered") or []),
                    "low_quality_fallback": len(cluster_rows) <= 2 or interval_method.startswith("single_quote"),
                }
            )
            candidate_index += 1

    if not candidates:
        return {
            "ok": False,
            "reason": "no_candidate_after_cleaning",
            "selected_gprice_labels": list(selected_gprice_labels),
            "selection": selection,
            "quality_engine": {
                "deduped_row_count": len(deduped_rows),
                "numeric_row_count": len(numeric_deduped_rows),
                "eligible_row_count": int(filtered.get("eligible_row_count") or 0),
                "eligible_numeric_row_count": len(eligible_numeric_rows),
                "filter_reason_counts": dict(filtered.get("rejected_reason_counts") or {}),
                "filter_keyword_counts": dict(filtered.get("rejected_keyword_counts") or {}),
                "raw_numeric_day_summaries": raw_numeric_day_summaries,
                "eligible_numeric_day_summaries": eligible_numeric_day_summaries,
                "selected_day_label": selected_day_label,
                "selected_day_gprice_label": selected_day_gprice_label,
                "selected_day_row_count": len(selected_rows),
                "candidate_count": 0,
                "low_quality_fallback": False,
            },
            "explanation": {
                "selection_mode": "latest_valid_day_only",
                "selection_summary": _latest_day_summary(
                    raw_numeric_day_summaries=raw_numeric_day_summaries,
                    eligible_numeric_day_summaries=eligible_numeric_day_summaries,
                    selected_day_label=selected_day_label,
                    selected_window=selected_window,
                ),
                "quality_summary": "status filtering kept rows, but no candidate survived price cleaning",
            },
        }

    candidates.sort(
        key=lambda item: (
            float(item.get("score") or 0.0),
            int(item.get("seller_count") or 0),
            -int(item.get("band_high") or 0) + int(item.get("band_low") or 0),
        ),
        reverse=True,
    )
    best = candidates[0]
    best_median = int(best.get("median_price") or 0)
    distance_threshold = max(80, round(best_median * 0.015))
    suspicious_low_count = 0
    suspicious_high_count = 0
    suspicious_low_rows = 0
    suspicious_high_rows = 0
    for candidate in candidates[1:]:
        candidate_median = int(candidate.get("median_price") or 0)
        if best_median - candidate_median >= distance_threshold:
            suspicious_low_count += 1
            suspicious_low_rows += int(candidate.get("sample_count") or 0)
        elif candidate_median - best_median >= distance_threshold:
            suspicious_high_count += 1
            suspicious_high_rows += int(candidate.get("sample_count") or 0)

    selected_day_summary = _latest_day_summary(
        raw_numeric_day_summaries=raw_numeric_day_summaries,
        eligible_numeric_day_summaries=eligible_numeric_day_summaries,
        selected_day_label=selected_day_label,
        selected_window=selected_window,
    )
    quality_summary = (
        f"deduped {len(deduped_rows)} rows, kept {len(eligible_numeric_rows)} valid rows after status filter, "
        f"used day {selected_day_gprice_label or selected_day_label or '--'}, "
        f"selected {int(best.get('sample_count') or 0)} rows after outlier cleaning "
        f"with interval method {best.get('interval_method') or 'unknown'}"
    )

    return {
        "ok": True,
        "selected_gprice_labels": list(selected_gprice_labels),
        "selection": selection,
        "cluster_count": len(candidates),
        "best_cluster": {
            "cluster_id": int(best.get("cluster_id") or 0),
            "dstatus": str(best.get("dstatus") or ""),
            "sample_count": int(best.get("sample_count") or 0),
            "seller_count": int(best.get("seller_count") or 0),
            "median_price": int(best.get("median_price") or 0),
            "band_low": int(best.get("band_low") or 0),
            "band_high": int(best.get("band_high") or 0),
            "price_range": str(best.get("price_range") or ""),
            "confidence_score": int(best.get("confidence_score") or 0),
            "confidence_label": str(best.get("confidence_label") or ""),
        },
        "flags": {
            "suspicious_low_cluster_count": suspicious_low_count,
            "suspicious_low_row_count": suspicious_low_rows,
            "suspicious_high_cluster_count": suspicious_high_count,
            "suspicious_high_row_count": suspicious_high_rows,
        },
        "clusters": [
            {
                "cluster_id": int(candidate.get("cluster_id") or 0),
                "dstatus": str(candidate.get("dstatus") or ""),
                "sample_count": int(candidate.get("sample_count") or 0),
                "seller_count": int(candidate.get("seller_count") or 0),
                "median_price": int(candidate.get("median_price") or 0),
                "price_range": str(candidate.get("price_range") or ""),
                "confidence_score": int(candidate.get("confidence_score") or 0),
                "confidence_label": str(candidate.get("confidence_label") or ""),
                "score": float(candidate.get("score") or 0.0),
            }
            for candidate in candidates
        ],
        "quality_engine": {
            "deduped_row_count": len(deduped_rows),
            "numeric_row_count": len(numeric_deduped_rows),
            "eligible_row_count": int(filtered.get("eligible_row_count") or 0),
            "eligible_numeric_row_count": len(eligible_numeric_rows),
            "filter_reason_counts": dict(filtered.get("rejected_reason_counts") or {}),
            "filter_keyword_counts": dict(filtered.get("rejected_keyword_counts") or {}),
            "raw_numeric_day_summaries": raw_numeric_day_summaries,
            "eligible_numeric_day_summaries": eligible_numeric_day_summaries,
            "selected_day_label": selected_day_label,
            "selected_day_gprice_label": selected_day_gprice_label,
            "selected_day_row_count": len(selected_rows),
            "candidate_count": len(candidates),
            "low_quality_fallback": bool(best.get("low_quality_fallback")),
            "candidate_summaries": [
                {
                    "cluster_id": int(candidate.get("cluster_id") or 0),
                    "dstatus": str(candidate.get("dstatus") or ""),
                    "sample_count": int(candidate.get("sample_count") or 0),
                    "seller_count": int(candidate.get("seller_count") or 0),
                    "median_price": int(candidate.get("median_price") or 0),
                    "interval_method": str(candidate.get("interval_method") or ""),
                    "price_range": str(candidate.get("price_range") or ""),
                    "removed_count": int(candidate.get("removed_count") or 0),
                    "removed_reason_counts": dict(candidate.get("removed_reason_counts") or {}),
                    "methods_triggered": list(candidate.get("methods_triggered") or []),
                    "low_quality_fallback": bool(candidate.get("low_quality_fallback")),
                    "score": float(candidate.get("score") or 0.0),
                }
                for candidate in candidates
            ],
        },
        "explanation": {
            "selection_mode": "latest_valid_day_only",
            "selection_summary": selected_day_summary,
            "quality_summary": quality_summary,
            "selection_detail": {
                "single_day_first": True,
                "selected_day_labels": list(selected_day_labels),
                "available_day_labels": list(selection.get("available_day_labels") or []),
                "selected_day_count": int(selection.get("selected_day_count") or 0),
                "available_day_count": int(selection.get("available_day_count") or 0),
                "selected_labels": list(selected_gprice_labels),
                "raw_latest_day_label": raw_latest_day,
                "selected_day_label": selected_day_label,
                "fallback_to_previous_day": fallback_applied,
            },
        },
    }
