from __future__ import annotations

from collections import Counter
import re
from math import ceil
from statistics import mean
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


def parse_price_int(value: Any) -> Optional[int]:
    text = str(value or "").strip()
    if not text or not text.isdigit():
        return None
    try:
        return int(text)
    except Exception:
        return None


def percentile(values: Sequence[int], ratio: float) -> int:
    if not values:
        return 0
    ordered = sorted(int(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    if ratio <= 0:
        return ordered[0]
    if ratio >= 1:
        return ordered[-1]
    position = (len(ordered) - 1) * ratio
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight)


def parse_mmdd_label(label: Any) -> Tuple[int, int]:
    text = str(label or "").strip()
    match = re.search(r"(\d{2})-(\d{2})", text)
    if not match:
        return (0, 0)
    try:
        return (int(match.group(1)), int(match.group(2)))
    except Exception:
        return (0, 0)


def parse_timestamp_sort_key(value: Any) -> Tuple[int, int, int, int, int, int]:
    text = str(value or "").strip()
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})", text)
    if not match:
        return (0, 0, 0, 0, 0, 0)
    try:
        return tuple(int(match.group(index)) for index in range(1, 7))
    except Exception:
        return (0, 0, 0, 0, 0, 0)


def extract_gprice_label(row: Mapping[str, Any]) -> str:
    for field in ("gprice", "GPrice", "gprice_two", "GPriceTwo"):
        text = str(row.get(field) or "").strip()
        match = re.search(r"(\d{2}-\d{2})", text)
        if match:
            return match.group(1)
    return ""


def parse_imported_day_label(value: Any) -> str:
    text = str(value or "").strip()
    match = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    if match:
        return match.group(1)
    sort_key = parse_timestamp_sort_key(text)
    if sort_key[0]:
        return f"{sort_key[0]:04d}-{sort_key[1]:02d}-{sort_key[2]:02d}"
    return ""


def sample_density_label(sample_count: int) -> str:
    if sample_count < 8:
        return "sparse"
    if sample_count < 15:
        return "balanced"
    return "dense"


def _retained_floor(sample_count: int, density_label: str) -> int:
    if sample_count <= 4:
        return sample_count
    if density_label == "sparse":
        return max(4, sample_count - 1)
    if density_label == "balanced":
        return max(4, ceil(sample_count * 0.65))
    return max(5, ceil(sample_count * 0.45))


def _retained_floor_from_config(
    sample_count: int,
    density_label: str,
    config: Optional[Mapping[str, Any]],
) -> int:
    if not config:
        return _retained_floor(sample_count, density_label)

    retention = dict(config.get("retention") or {})
    if sample_count <= 4:
        return sample_count
    if density_label == "sparse":
        min_keep = int(retention.get("sparse_min_keep", 4) or 4)
        max_trim = int(retention.get("sparse_max_trim", 1) or 1)
        return max(min_keep, sample_count - max_trim)
    if density_label == "balanced":
        min_keep = int(retention.get("balanced_min_keep", 4) or 4)
        keep_ratio = float(retention.get("balanced_keep_ratio", 0.65) or 0.65)
        return max(min_keep, ceil(sample_count * keep_ratio))
    min_keep = int(retention.get("dense_min_keep", 5) or 5)
    keep_ratio = float(retention.get("dense_keep_ratio", 0.45) or 0.45)
    return max(min_keep, ceil(sample_count * keep_ratio))


def _method_plan_from_config(
    density_label: str,
    config: Optional[Mapping[str, Any]],
) -> List[Tuple[Any, Dict[str, Any]]]:
    if not config:
        if density_label == "balanced":
            return [
                (trim_modified_zscore_rows, {"threshold": 4.0, "min_gap_floor": 140, "min_gap_ratio": 0.02}),
                (trim_iqr_rows, {"fence_multiplier": 2.4, "min_gap_floor": 140, "min_gap_ratio": 0.02}),
            ]
        if density_label == "dense":
            return [
                (trim_modified_zscore_rows, {"threshold": 3.5, "min_gap_floor": 120, "min_gap_ratio": 0.018}),
                (trim_iqr_rows, {"fence_multiplier": 2.0, "min_gap_floor": 120, "min_gap_ratio": 0.018}),
                (trim_cluster_tail_rows, {}),
            ]
        return []

    outlier_profiles = dict(config.get("outlier_profiles") or {})
    profile = dict(outlier_profiles.get(density_label) or {})
    if not profile:
        return _method_plan_from_config(density_label, None)

    plan: List[Tuple[Any, Dict[str, Any]]] = []
    modified_zscore = dict(profile.get("modified_zscore") or {})
    if bool(modified_zscore.get("enabled", density_label in {"balanced", "dense"})):
        plan.append(
            (
                trim_modified_zscore_rows,
                {
                    "threshold": float(modified_zscore.get("threshold", 4.0 if density_label == "balanced" else 3.5)),
                    "min_gap_floor": int(modified_zscore.get("min_gap_floor", 140 if density_label == "balanced" else 120)),
                    "min_gap_ratio": float(modified_zscore.get("min_gap_ratio", 0.02 if density_label == "balanced" else 0.018)),
                },
            )
        )

    iqr_profile = dict(profile.get("iqr") or {})
    if bool(iqr_profile.get("enabled", density_label in {"balanced", "dense"})):
        plan.append(
            (
                trim_iqr_rows,
                {
                    "fence_multiplier": float(iqr_profile.get("fence_multiplier", 2.4 if density_label == "balanced" else 2.0)),
                    "min_gap_floor": int(iqr_profile.get("min_gap_floor", 140 if density_label == "balanced" else 120)),
                    "min_gap_ratio": float(iqr_profile.get("min_gap_ratio", 0.02 if density_label == "balanced" else 0.018)),
                },
            )
        )

    cluster_tail = dict(profile.get("cluster_tail") or {})
    if bool(cluster_tail.get("enabled", density_label == "dense")):
        plan.append((trim_cluster_tail_rows, {}))
    return plan


def _day_bucket_key(row: Mapping[str, Any]) -> Tuple[str, str, str]:
    gprice_label = extract_gprice_label(row)
    if gprice_label:
        return (f"label:{gprice_label}", gprice_label, "label")
    imported_day = parse_imported_day_label(row.get("imported_at"))
    if imported_day:
        return (f"day:{imported_day}", imported_day, "day")
    return ("unlabelled", "", "unlabelled")


def format_recent_window_summary(window: Mapping[str, Any]) -> str:
    selected_row_count = int(window.get("selected_row_count") or 0)
    selected_day_labels = [str(label or "").strip() for label in list(window.get("selected_day_labels") or []) if str(label or "").strip()]
    selected_gprice_labels = [str(label or "").strip() for label in list(window.get("selected_gprice_labels") or []) if str(label or "").strip()]
    if not selected_day_labels:
        return f"selected {selected_row_count} rows from unlabeled recent history"

    primary_label = selected_gprice_labels[0] if selected_gprice_labels else selected_day_labels[0]
    if len(selected_day_labels) == 1:
        return f"selected {selected_row_count} rows from latest day {primary_label}"

    fallback_labels = selected_gprice_labels[1:] if len(selected_gprice_labels) > 1 else selected_day_labels[1:]
    fallback_text = ", ".join(fallback_labels)
    if fallback_text:
        return f"selected {selected_row_count} rows from latest day {primary_label} with fallback days {fallback_text}"
    return f"selected {selected_row_count} rows from latest day {primary_label}"


def trimmed_mean(values: Sequence[int], trim_ratio: float = 0.1) -> int:
    if not values:
        return 0
    ordered = sorted(int(value) for value in values)
    if len(ordered) < 4:
        return round(mean(ordered))
    trim_count = min(int(len(ordered) * trim_ratio), max(0, (len(ordered) - 2) // 2))
    trimmed = ordered[trim_count : len(ordered) - trim_count] if trim_count else ordered
    return round(mean(trimmed or ordered))


def winsorized_mean(values: Sequence[int], trim_ratio: float = 0.1) -> int:
    if not values:
        return 0
    ordered = sorted(int(value) for value in values)
    if len(ordered) < 4:
        return round(mean(ordered))
    trim_count = min(int(len(ordered) * trim_ratio), max(0, (len(ordered) - 2) // 2))
    if not trim_count:
        return round(mean(ordered))
    low_value = ordered[trim_count]
    high_value = ordered[-trim_count - 1]
    clipped = (
        [low_value] * trim_count
        + ordered[trim_count : len(ordered) - trim_count]
        + [high_value] * trim_count
    )
    return round(mean(clipped or ordered))


def median_abs_deviation(values: Sequence[int]) -> int:
    if not values:
        return 0
    center = percentile(values, 0.5)
    deviations = [abs(int(value) - center) for value in values]
    return percentile(deviations, 0.5)


def merchant_key(row: Mapping[str, Any]) -> str:
    seller_name = str(row.get("sname") or row.get("SName") or "").strip().lower()
    stall_name = str(row.get("sno") or row.get("SNo") or "").strip().lower()
    if seller_name:
        return f"name:{seller_name}"
    if stall_name:
        return f"stall:{stall_name}"
    gid = str(row.get("gid") or row.get("GID") or row.get("id") or "").strip()
    if gid:
        return f"id:{gid}"
    return "unknown"


def row_recency_sort_key(row: Mapping[str, Any]) -> Tuple[
    Tuple[int, int, int, int, int, int],
    Tuple[int, int],
    int,
]:
    imported_at_key = parse_timestamp_sort_key(row.get("imported_at"))
    gprice_key = parse_mmdd_label(extract_gprice_label(row))
    try:
        row_id = int(str(row.get("id") or row.get("gid") or "0").strip() or "0")
    except Exception:
        row_id = 0
    return (imported_at_key, gprice_key, row_id)


def dedupe_latest_rows(rows: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    ordered_rows = sorted((dict(row) for row in rows), key=row_recency_sort_key, reverse=True)
    seen: set[str] = set()
    deduped: List[Dict[str, Any]] = []
    for row in ordered_rows:
        key = merchant_key(row)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(dict(row))
    return deduped


def numeric_rows(rows: Iterable[Mapping[str, Any]], *, price_field: str = "price_text") -> List[Dict[str, Any]]:
    kept: List[Dict[str, Any]] = []
    for row in rows:
        if "price_value" in row and parse_price_int(row.get("price_value")) is not None:
            price_value = int(parse_price_int(row.get("price_value")) or 0)
        else:
            price_value = parse_price_int(row.get(price_field))
            if price_value is None:
                continue
        copied = dict(row)
        copied["price_value"] = price_value
        kept.append(copied)
    return kept


def select_recent_day_window(
    rows: Sequence[Mapping[str, Any]],
    *,
    min_samples: int = 10,
    max_labels: int = 3,
) -> Dict[str, Any]:
    if not rows:
        return {
            "selected_rows": [],
            "selected_labels": [],
            "selected_gprice_labels": [],
            "selected_day_labels": [],
            "available_day_labels": [],
            "selected_day_count": 0,
            "available_day_count": 0,
            "selected_label_count": 0,
            "available_label_count": 0,
            "selected_row_count": 0,
            "available_row_count": 0,
            "mode": "all_rows_fallback",
            "fallback_applied": False,
            "single_day_first": True,
            "selected_day_summaries": [],
            "available_day_summaries": [],
            "summary": "selected 0 rows from unlabeled recent history",
        }

    grouped: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        copied = dict(row)
        bucket_key, bucket_label, bucket_type = _day_bucket_key(copied)
        copied["selected_gprice_label"] = extract_gprice_label(copied)
        copied["selected_quote_day"] = bucket_label if bucket_label != "unlabelled" else ""
        bucket = grouped.setdefault(
            bucket_key,
            {
                "bucket_key": bucket_key,
                "day_label": bucket_label,
                "gprice_label": copied["selected_gprice_label"],
                "bucket_type": bucket_type,
                "rows": [],
                "sample_count": 0,
                "latest_sort_key": ((0, 0, 0, 0, 0, 0), (0, 0), 0),
                "latest_imported_at": "",
            },
        )
        bucket["rows"].append(copied)
        bucket["sample_count"] += 1
        if copied["selected_gprice_label"] and not bucket["gprice_label"]:
            bucket["gprice_label"] = copied["selected_gprice_label"]
        bucket["latest_imported_at"] = max(str(bucket["latest_imported_at"] or ""), str(copied.get("imported_at") or ""))
        row_key = row_recency_sort_key(copied)
        if row_key > bucket["latest_sort_key"]:
            bucket["latest_sort_key"] = row_key

    ordered_buckets = sorted(
        grouped.values(),
        key=lambda bucket: (
            bucket["latest_sort_key"],
            bucket["sample_count"],
            bucket["day_label"],
        ),
        reverse=True,
    )

    target_samples = max(1, int(min_samples))
    target_days = max(1, int(max_labels))
    selected_buckets: List[Dict[str, Any]] = []
    selected_rows: List[Dict[str, Any]] = []
    for bucket in ordered_buckets:
        selected_buckets.append(bucket)
        selected_rows.extend(dict(row) for row in bucket["rows"])
        if len(selected_rows) >= target_samples or len(selected_buckets) >= target_days:
            break

    selected_rows.sort(key=row_recency_sort_key, reverse=True)

    selected_day_labels = [str(bucket["day_label"] or "").strip() for bucket in selected_buckets if str(bucket["day_label"] or "").strip()]
    selected_gprice_labels: List[str] = []
    for bucket in selected_buckets:
        label = str(bucket.get("gprice_label") or "").strip()
        if label and label not in selected_gprice_labels:
            selected_gprice_labels.append(label)

    available_day_labels = [str(bucket["day_label"] or "").strip() for bucket in ordered_buckets if str(bucket["day_label"] or "").strip()]
    available_gprice_labels: List[str] = []
    for bucket in ordered_buckets:
        label = str(bucket.get("gprice_label") or "").strip()
        if label and label not in available_gprice_labels:
            available_gprice_labels.append(label)

    selected_day_summaries = [
        {
            "day_label": str(bucket["day_label"] or "").strip(),
            "gprice_label": str(bucket.get("gprice_label") or "").strip(),
            "sample_count": int(bucket["sample_count"] or 0),
            "latest_imported_at": str(bucket.get("latest_imported_at") or ""),
            "bucket_type": str(bucket.get("bucket_type") or ""),
        }
        for bucket in selected_buckets
    ]
    available_day_summaries = [
        {
            "day_label": str(bucket["day_label"] or "").strip(),
            "gprice_label": str(bucket.get("gprice_label") or "").strip(),
            "sample_count": int(bucket["sample_count"] or 0),
            "latest_imported_at": str(bucket.get("latest_imported_at") or ""),
            "bucket_type": str(bucket.get("bucket_type") or ""),
        }
        for bucket in ordered_buckets
    ]

    if selected_day_labels:
        if len(selected_day_labels) == 1:
            mode = "latest_day_only" if selected_gprice_labels else "latest_imported_day_only"
        else:
            mode = "latest_day_backfilled" if selected_gprice_labels else "latest_imported_day_backfilled"
    else:
        mode = "all_rows_fallback"

    window = {
        "selected_rows": selected_rows,
        "selected_labels": list(selected_gprice_labels),
        "selected_gprice_labels": list(selected_gprice_labels),
        "selected_day_labels": selected_day_labels,
        "available_day_labels": available_day_labels,
        "available_gprice_labels": available_gprice_labels,
        "selected_day_count": len(selected_day_labels),
        "available_day_count": len(ordered_buckets),
        "selected_label_count": len(selected_gprice_labels),
        "available_label_count": len(available_gprice_labels),
        "selected_row_count": len(selected_rows),
        "available_row_count": len(rows),
        "mode": mode,
        "fallback_applied": len(selected_day_labels) > 1,
        "single_day_first": True,
        "selected_day_summaries": selected_day_summaries,
        "available_day_summaries": available_day_summaries,
    }
    window["summary"] = format_recent_window_summary(window)
    return window


def select_recent_sample_window(
    rows: Sequence[Mapping[str, Any]],
    *,
    min_samples: int = 10,
    max_labels: int = 3,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    window = select_recent_day_window(rows, min_samples=min_samples, max_labels=max_labels)
    return list(window["selected_rows"]), list(window["selected_labels"])


def _attach_reason(row: Mapping[str, Any], reason: str) -> Dict[str, Any]:
    copied = dict(row)
    existing = str(copied.get("price_outlier_reason") or "").strip()
    copied["price_outlier_reason"] = reason if not existing else f"{existing}|{reason}"
    copied["is_price_stat_outlier"] = "1"
    return copied


def trim_modified_zscore_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    threshold: float = 3.5,
    min_gap_floor: int = 120,
    min_gap_ratio: float = 0.018,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    if len(rows) < 5:
        return [dict(row) for row in rows], []
    values = [int(row["price_value"]) for row in rows]
    center = percentile(values, 0.5)
    mad = median_abs_deviation(values)
    if mad < 1:
        return [dict(row) for row in rows], []
    min_gap = max(min_gap_floor, int(round(center * min_gap_ratio)))

    kept: List[Dict[str, Any]] = []
    removed: List[Dict[str, Any]] = []
    for row in rows:
        price_value = int(row["price_value"])
        score = 0.6745 * (price_value - center) / mad
        copied = dict(row)
        copied["robust_zscore"] = round(score, 4)
        if abs(score) > threshold and abs(price_value - center) >= min_gap:
            removed.append(_attach_reason(copied, "modified_zscore"))
        else:
            kept.append(copied)
    return kept, removed


def trim_iqr_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    fence_multiplier: float = 2.0,
    min_gap_floor: int = 120,
    min_gap_ratio: float = 0.018,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    if len(rows) < 5:
        return [dict(row) for row in rows], []
    values = [int(row["price_value"]) for row in rows]
    center = percentile(values, 0.5)
    q1 = percentile(values, 0.25)
    q3 = percentile(values, 0.75)
    iqr = max(1, q3 - q1)
    low_bound = q1 - fence_multiplier * iqr
    high_bound = q3 + fence_multiplier * iqr
    min_gap = max(min_gap_floor, int(round(center * min_gap_ratio)))

    kept: List[Dict[str, Any]] = []
    removed: List[Dict[str, Any]] = []
    for row in rows:
        price_value = int(row["price_value"])
        if low_bound <= price_value <= high_bound or abs(price_value - center) < min_gap:
            kept.append(dict(row))
        else:
            removed.append(_attach_reason(row, "iqr_fence"))
    return kept, removed


def trim_cluster_tail_rows(rows: Sequence[Mapping[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    if len(rows) < 5:
        return [dict(row) for row in rows], []

    ordered_rows = sorted((dict(row) for row in rows), key=lambda row: int(row["price_value"]))
    values = [int(row["price_value"]) for row in ordered_rows]
    gaps = [values[index + 1] - values[index] for index in range(len(values) - 1)]
    if not gaps:
        return ordered_rows, []

    gap_index = max(range(len(gaps)), key=lambda index: gaps[index])
    gap_value = gaps[gap_index]
    left_rows = ordered_rows[: gap_index + 1]
    right_rows = ordered_rows[gap_index + 1 :]
    left_values = values[: gap_index + 1]
    right_values = values[gap_index + 1 :]

    if len(left_rows) <= len(right_rows):
        minority_side = "left"
        minority_rows = left_rows
        minority_values = left_values
        majority_rows = right_rows
        majority_values = right_values
    else:
        minority_side = "right"
        minority_rows = right_rows
        minority_values = right_values
        majority_rows = left_rows
        majority_values = left_values

    minority_count = len(minority_rows)
    majority_count = len(majority_rows)
    if majority_count < 3 or minority_count > max(2, len(values) // 3):
        return ordered_rows, []

    majority_center = percentile(majority_values, 0.5)
    majority_spread = majority_values[-1] - majority_values[0]
    minority_spread = minority_values[-1] - minority_values[0] if minority_values else 0
    compact_threshold = max(120, int(round(majority_center * 0.015)))
    if majority_spread > compact_threshold:
        return ordered_rows, []

    min_gap = max(120, majority_spread * 2, max(20, minority_spread) * 2)
    if gap_value < min_gap:
        return ordered_rows, []

    if minority_side == "left":
        reason = "cluster_gap_low_tail"
    else:
        reason = "cluster_gap_high_tail"
    kept_rows = [dict(row) for row in majority_rows]
    removed_rows = [_attach_reason(row, reason) for row in minority_rows]
    return kept_rows, removed_rows


def build_price_stats(values: Sequence[int]) -> Dict[str, int]:
    if not values:
        return {
            "market_price": 0,
            "low_reference": 0,
            "high_reference": 0,
            "price_min": 0,
            "price_max": 0,
            "price_iqr": 0,
            "price_mad": 0,
            "trimmed_mean": 0,
            "winsorized_mean": 0,
        }
    ordered = sorted(int(value) for value in values)
    q1 = percentile(ordered, 0.25)
    q3 = percentile(ordered, 0.75)
    return {
        "market_price": percentile(ordered, 0.5),
        "low_reference": q1,
        "high_reference": q3,
        "price_min": ordered[0],
        "price_max": ordered[-1],
        "price_iqr": max(0, q3 - q1),
        "price_mad": median_abs_deviation(ordered),
        "trimmed_mean": trimmed_mean(ordered),
        "winsorized_mean": winsorized_mean(ordered),
    }


def clean_price_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    config: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    base_rows = [dict(row) for row in rows]
    numeric = numeric_rows(base_rows)
    if not numeric:
        return {
            "raw_rows": base_rows,
            "numeric_rows": [],
            "kept_rows": [],
            "removed_rows": [],
            "removed_count": 0,
            "methods_triggered": [],
            "removed_reason_counts": {},
            "cleaning_profile": "sparse",
            "cleaning_retention_floor": 0,
            "stats": build_price_stats([]),
        }

    working = [dict(row) for row in numeric]
    removed_total: List[Dict[str, Any]] = []
    density = sample_density_label(len(numeric))
    retained_floor = _retained_floor_from_config(len(numeric), density, config)
    method_plan = _method_plan_from_config(density, config)

    for method, kwargs in method_plan:
        if len(working) <= retained_floor:
            break
        next_rows, removed_rows = method(working, **kwargs)
        if len(next_rows) < retained_floor:
            continue
        working = next_rows
        removed_total.extend(removed_rows)

    if not working:
        working = [dict(row) for row in numeric]
        removed_total = []

    clean_values = [int(row["price_value"]) for row in working]
    reason_counts = Counter(
        reason
        for row in removed_total
        for reason in str(row.get("price_outlier_reason") or "").split("|")
        if reason
    )
    methods_triggered = sorted(
        set(reason_counts.keys())
    )
    return {
        "raw_rows": base_rows,
        "numeric_rows": numeric,
        "kept_rows": working,
        "removed_rows": removed_total,
        "removed_count": len(removed_total),
        "methods_triggered": methods_triggered,
        "removed_reason_counts": dict(reason_counts),
        "cleaning_profile": density,
        "cleaning_retention_floor": retained_floor,
        "stats": build_price_stats(clean_values),
    }
