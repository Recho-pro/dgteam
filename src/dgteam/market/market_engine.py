from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from dgteam.core.textio import read_json_utf8

from .quality_engine import build_data_quality_market
from .price_cleaning import (
    dedupe_latest_rows,
    extract_gprice_label,
    numeric_rows,
    parse_price_int,
    percentile,
    row_recency_sort_key,
    select_recent_day_window,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
REFERENCE_OUTPUT_DIR = PROJECT_ROOT / "runtime" / "local" / "reference_imports"
MIN_HEALTHY_REFERENCE_ROWS = 1000
CAPACITY_TOKEN_RE = re.compile(r"(?i)(\d+\+\d+(?:GB|G|TB|T)|\d+(?:GB|G|TB|T))")


@dataclass
class ReferenceContext:
    csv_path: str
    summary_path: str
    source_name: str
    fetched_at: str


def sku_title_from_parts(model_title: str, group_title: str) -> str:
    model = str(model_title or "").strip()
    group = str(group_title or "").strip()
    return f"{model}-{group}" if group else model


def normalize_capacity_label(token: str) -> str:
    text = str(token or "").strip().upper().replace(" ", "")
    text = text.replace("GB", "G").replace("TB", "T")
    return text


def split_group_spec(group_title: str) -> Tuple[str, str]:
    title = str(group_title or "").strip()
    if not title:
        return ("DEFAULT", "UNSPECIFIED")

    match = CAPACITY_TOKEN_RE.search(title)
    if not match:
        return ("DEFAULT", title)

    capacity = normalize_capacity_label(match.group(1))
    before = title[: match.start()].strip(" /-_+")
    after = title[match.end() :].strip(" /-_+")
    variant = f"{before}{after}".strip()
    variant = re.sub(r"^[\\/|,.;:+-]+|[\\/|,.;:+-]+$", "", variant).strip()
    variant = re.sub(r"\s{2,}", " ", variant)
    return (capacity or "DEFAULT", variant or "STANDARD")


def capacity_sort_key(capacity_label: str) -> Tuple[int, int]:
    label = normalize_capacity_label(capacity_label)
    if not label:
        return (10**9, 10**9)

    match = re.match(r"^(?:(\d+)\+)?(\d+)(G|T)$", label)
    if not match:
        return (10**9 - 1, 10**9 - 1)

    memory_value = int(match.group(1) or 0)
    storage_value = int(match.group(2) or 0)
    if match.group(3) == "T":
        storage_value *= 1024
    return (memory_value, storage_value)


def _label_sample_summary(rows: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        label = extract_gprice_label(row)
        if not label:
            continue
        grouped.setdefault(label, []).append(dict(row))

    summary: List[Dict[str, Any]] = []
    for label, label_rows in grouped.items():
        summary.append(
            {
                "label": label,
                "sample_count": len(label_rows),
                "latest_imported_at": max((str(row.get("imported_at") or "") for row in label_rows), default=""),
            }
        )

    summary.sort(
        key=lambda item: (
            max((row_recency_sort_key(row) for row in grouped.get(str(item["label"]), [])), default=((0, 0, 0, 0, 0, 0), (0, 0), 0)),
            str(item["label"]),
        ),
        reverse=True,
    )
    return summary


def _price_gap_threshold(values: Sequence[int]) -> int:
    if not values:
        return 80
    center = percentile(values, 0.5)
    q1 = percentile(values, 0.25)
    q3 = percentile(values, 0.75)
    iqr = max(0, q3 - q1)
    return max(80, round(center * 0.012), round(iqr * 1.5))


def split_price_clusters(rows: Sequence[Mapping[str, Any]]) -> List[List[Dict[str, Any]]]:
    ordered = sorted((dict(row) for row in rows), key=lambda row: int(row["price_value"]))
    if len(ordered) <= 2:
        return [ordered] if ordered else []

    values = [int(row["price_value"]) for row in ordered]
    gap_threshold = _price_gap_threshold(values)
    clusters: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = [ordered[0]]
    for index in range(1, len(ordered)):
        prev_value = int(ordered[index - 1]["price_value"])
        curr_value = int(ordered[index]["price_value"])
        if curr_value - prev_value >= gap_threshold:
            clusters.append(current)
            current = [ordered[index]]
        else:
            current.append(ordered[index])
    if current:
        clusters.append(current)
    return clusters


def status_priority(brand_title: str, dstatus: str) -> int:
    brand = str(brand_title or "").strip()
    status = str(dstatus or "").strip()
    if brand == "\u82f9\u679c":
        if status == "\u516c\u53f8\u7eaf\u539f\u5c01":
            return 36
        if status == "\u516c\u53f8\u7eaf\u539f\u5c01/\u7279\u5b9a\u533a\u57df\u9500\u552e":
            return 32
        if status == "\u516c\u53f8\u7eaf\u539f\u5c01/\u53ef\u51fa\u5168\u56fd":
            return 28
        if "\u516c\u53f8\u7eaf\u539f\u5c01" in status:
            return 22
        return 0
    return 10 if status else 6


def score_cluster(
    *,
    brand_title: str,
    dstatus: str,
    prices: Sequence[int],
    reference_price: Optional[int],
) -> float:
    if not prices:
        return -10**9
    count = len(prices)
    seller_count = count
    median_price = percentile(prices, 0.5)
    spread = max(prices) - min(prices)
    status_bonus = status_priority(brand_title, dstatus)
    count_score = seller_count * 14.0
    spread_penalty = min(spread, 1600) / 18.0
    singleton_penalty = 14.0 if count == 1 else 0.0
    reference_penalty = 0.0
    if reference_price is not None:
        reference_penalty = min(abs(median_price - reference_price), 2000) / 45.0
    return count_score + status_bonus - spread_penalty - singleton_penalty - reference_penalty


def confidence_label(
    *,
    seller_count: int,
    prices: Sequence[int],
    reference_price: Optional[int],
) -> Tuple[int, str]:
    if not prices:
        return (0, "pending data")
    median_price = percentile(prices, 0.5)
    q1 = percentile(prices, 0.25)
    q3 = percentile(prices, 0.75)
    spread_ratio = (q3 - q1) / median_price if median_price else 1
    score = min(seller_count, 20) * 3
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


def _reference_candidates(root: Path) -> List[Tuple[Path, Dict[str, Any]]]:
    candidates: List[Tuple[Path, Dict[str, Any]]] = []
    if not root.exists():
        return candidates
    for child in root.iterdir():
        if not child.is_dir():
            continue
        summary_path = child / "summary.json"
        csv_path = child / "reference_prices_supplier.csv"
        if not summary_path.exists() or not csv_path.exists():
            continue
        try:
            summary = read_json_utf8(summary_path)
        except Exception:
            continue
        counts = summary.get("counts") or {}
        supplier_rows = int(counts.get("supplier_rows") or 0)
        finished_at = str(summary.get("finished_at") or "")
        candidates.append(
            (
                child,
                {
                    "summary": summary,
                    "supplier_rows": supplier_rows,
                    "finished_at": finished_at,
                },
            )
        )
    candidates.sort(
        key=lambda item: (
            str(item[1].get("finished_at") or ""),
            item[0].name,
        ),
        reverse=True,
    )
    return candidates


def load_latest_healthy_reference_map(
    root: Path = REFERENCE_OUTPUT_DIR,
    *,
    min_rows: int = MIN_HEALTHY_REFERENCE_ROWS,
) -> Tuple[Dict[Tuple[str, str, str], Dict[str, Any]], Optional[ReferenceContext]]:
    candidates = _reference_candidates(root)
    selected_dir: Optional[Path] = None
    selected_summary: Optional[Dict[str, Any]] = None
    for directory, payload in candidates:
        if int(payload["supplier_rows"]) >= min_rows:
            selected_dir = directory
            selected_summary = payload["summary"]
            break
    if not selected_dir:
        return {}, None

    csv_path = selected_dir / "reference_prices_supplier.csv"
    mapping: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            price = parse_price_int(row.get("reference_price"))
            sku_title = str(row.get("sku_title") or "").strip()
            if price is None or not sku_title:
                continue
            key = (
                str(row.get("brand_title") or "").strip(),
                str(row.get("series_title") or "").strip(),
                sku_title,
            )
            mapping[key] = {
                "reference_price": price,
                "sheet_name": str(row.get("sheet_name") or "").strip(),
                "fetched_at": str(row.get("fetched_at") or "").strip(),
            }

    context = ReferenceContext(
        csv_path=str(csv_path),
        summary_path=str(selected_dir / "summary.json"),
        source_name=selected_dir.name,
        fetched_at=str((selected_summary or {}).get("finished_at") or ""),
    )
    return mapping, context


def reference_for_sku(
    reference_map: Mapping[Tuple[str, str, str], Dict[str, Any]],
    *,
    brand_title: str,
    series_title: str,
    model_title: str,
    group_title: str,
) -> Optional[Dict[str, Any]]:
    exact_key = (
        str(brand_title or "").strip(),
        str(series_title or "").strip(),
        sku_title_from_parts(model_title, group_title),
    )
    model_only_key = (
        str(brand_title or "").strip(),
        str(series_title or "").strip(),
        str(model_title or "").strip(),
    )
    return reference_map.get(exact_key) or reference_map.get(model_only_key)


def _cluster_band(prices: Sequence[int]) -> Tuple[int, int]:
    if not prices:
        return (0, 0)
    if len(prices) < 4:
        return (min(prices), max(prices))
    return (percentile(prices, 0.25), percentile(prices, 0.75))


def build_market_v1(
    *,
    rows: Sequence[Mapping[str, Any]],
    brand_title: str,
    series_title: str,
    model_title: str,
    group_title: str,
    reference_map: Optional[Mapping[Tuple[str, str, str], Dict[str, Any]]] = None,
    reference_context: Optional[ReferenceContext] = None,
    rules: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    capacity_label, variant_label = split_group_spec(group_title)
    scope_level = "model"
    if str(group_title or "").strip():
        scope_level = "capacity" if capacity_label != "DEFAULT" and variant_label == "STANDARD" else "variant"

    market_scope = {
        "scope_level": scope_level,
        "sku_title": sku_title_from_parts(model_title, group_title),
        "group_title": str(group_title or "").strip(),
        "capacity_label": capacity_label,
        "variant_label": variant_label,
        "is_capacity_aggregate": scope_level == "capacity",
        "is_variant_specific": scope_level == "variant",
    }

    reference_info = None
    reference_price = None
    if reference_map:
        reference_info = reference_for_sku(
            reference_map,
            brand_title=brand_title,
            series_title=series_title,
            model_title=model_title,
            group_title=group_title,
        )
        if reference_info:
            reference_price = int(reference_info["reference_price"])
    quality_result = build_data_quality_market(
        rows=rows,
        brand_title=brand_title,
        reference_price=reference_price,
        rules=rules,
    )
    explanation = dict(quality_result.get("explanation") or {})
    explanation["scope_summary"] = (
        f"{scope_level} scope for {market_scope['sku_title']}"
        if market_scope["sku_title"]
        else f"{scope_level} scope"
    )
    return {
        **quality_result,
        "market_scope": market_scope,
        "explanation": explanation,
        "reference": {
            "price": reference_price,
            "sheet_name": str((reference_info or {}).get("sheet_name") or ""),
            "source_title": str((reference_info or {}).get("source_title") or ""),
            "fetched_at": str((reference_info or {}).get("fetched_at") or ""),
            "source_name": str((reference_info or {}).get("source_name") or (reference_context.source_name if reference_context else "")),
            "csv_path": reference_context.csv_path if reference_context else "",
        },
    }


def build_capacity_market_breakdown(
    *,
    rows: Sequence[Mapping[str, Any]],
    brand_title: str,
    series_title: str,
    model_title: str,
    reference_map: Optional[Mapping[Tuple[str, str, str], Dict[str, Any]]] = None,
    reference_context: Optional[ReferenceContext] = None,
    rules: Optional[Mapping[str, Any]] = None,
) -> List[Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        capacity_label, variant_label = split_group_spec(str(row.get("group_title") or ""))
        bucket = grouped.setdefault(
            capacity_label,
            {
                "capacity_label": capacity_label,
                "rows": [],
                "variant_labels": set(),
            },
        )
        bucket["rows"].append(dict(row))
        if variant_label and variant_label not in {"STANDARD", "UNSPECIFIED"}:
            bucket["variant_labels"].add(variant_label)

    results: List[Dict[str, Any]] = []
    for capacity_label, bucket in grouped.items():
        market = build_market_v1(
            rows=list(bucket["rows"]),
            brand_title=brand_title,
            series_title=series_title,
            model_title=model_title,
            group_title=capacity_label if capacity_label != "DEFAULT" else "",
            reference_map=reference_map,
            reference_context=reference_context,
            rules=rules,
        )
        results.append(
            {
                "capacity_label": capacity_label,
                "variant_count": len(bucket["variant_labels"]),
                "variant_labels": sorted(bucket["variant_labels"]),
                "row_count": len(bucket["rows"]),
                "market_v1": market,
            }
        )

    results.sort(key=lambda item: capacity_sort_key(str(item.get("capacity_label") or "")))
    return results
