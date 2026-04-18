from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Tuple

from dgteam.market.market_engine import build_market_v1
from dgteam.market.price_cleaning import (
    dedupe_latest_rows,
    numeric_rows,
    percentile,
    select_recent_sample_window,
)


@dataclass
class BranchPayloadAssembler:
    storage: Any
    safe_int: Callable[[Any], int]
    range_text: Callable[[Iterable[int]], str]
    capacity_sort_key: Callable[[str], Tuple[int, int]]
    split_group_spec: Callable[[str], Tuple[str, str]]
    normalize_variant_title: Callable[[str], str]
    friendly_status_text: Callable[[Any], str]

    def build_from_snapshot_candidates(
        self,
        *,
        snapshot_candidates: List[Dict[str, Any]],
        reference_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        capacity_map: Dict[str, Dict[str, Any]] = {}
        selected_labels: List[str] = []
        branch_title = str(snapshot_candidates[0].get("model_title") or "").strip() if snapshot_candidates else ""

        for candidate in snapshot_candidates:
            snapshot_row = self.storage.get_market_snapshot_row(
                run_key=str(candidate.get("run_key") or "").strip(),
                brand_title=str(candidate.get("brand_title") or "").strip(),
                series_title=str(candidate.get("series_title") or "").strip(),
                model_title=str(candidate.get("model_title") or "").strip(),
                group_title=str(candidate.get("group_title") or "").strip(),
                condition_bucket=str(candidate.get("condition_bucket") or "").strip(),
            )
            if not snapshot_row:
                continue
            variant = self._variant_from_snapshot_row(snapshot_row, reference_context=reference_context)
            if not variant:
                continue
            self._upsert_capacity_variant(capacity_map, variant)
            selected_labels.extend(variant.get("selected_gprice_labels") or [])

        capacity_groups = self._finalize_capacity_groups(capacity_map)
        if not capacity_groups:
            return {}
        return {
            "branch_title": branch_title,
            "latest_imported_at": max((str(item.get("latest_imported_at") or "") for item in capacity_groups), default=""),
            "sample_count": sum(self.safe_int(item.get("sample_count")) for item in capacity_groups),
            "seller_count": max(self.safe_int(item.get("seller_count")) for item in capacity_groups),
            "price_range": self.range_text(
                self.safe_int(item.get("market_price")) for item in capacity_groups if self.safe_int(item.get("market_price")) > 0
            ),
            "branch_rank": len(capacity_groups),
            "selected_gprice_labels": sorted({label for label in selected_labels if label}, reverse=True)[:3],
            "resolution_source": "published_snapshot",
            "capacity_groups": capacity_groups,
        }

    def build_from_rows(
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
        grouped_rows = self._group_rows_by_variant(rows)
        capacity_map: Dict[str, Dict[str, Any]] = {}
        selected_labels: List[str] = []

        for variant_group, variant_rows in grouped_rows.items():
            published_snapshot = self.storage.get_market_snapshot_row(
                run_key=run_key,
                brand_title=brand_title,
                series_title=series_title,
                model_title=model_title,
                group_title=variant_group,
                condition_bucket=condition_bucket,
            )
            if published_snapshot:
                variant = self._variant_from_snapshot_row(published_snapshot, reference_context=reference_context)
            else:
                variant = self._variant_from_rows(
                    rows=variant_rows,
                    brand_title=brand_title,
                    series_title=series_title,
                    model_title=model_title,
                    group_title=variant_group,
                    reference_map=reference_map,
                    reference_context=reference_context,
                )
            if not variant:
                continue

            self._upsert_capacity_variant(capacity_map, variant)
            selected_labels.extend(variant.get("selected_gprice_labels") or [])

        capacity_groups = self._finalize_capacity_groups(capacity_map)
        if not capacity_groups:
            return {}

        return {
            "branch_title": model_title,
            "latest_imported_at": max((str(item.get("latest_imported_at") or "") for item in capacity_groups), default=""),
            "sample_count": sum(self.safe_int(item.get("sample_count")) for item in capacity_groups),
            "seller_count": max(self.safe_int(item.get("seller_count")) for item in capacity_groups),
            "price_range": self.range_text(
                self.safe_int(item.get("market_price")) for item in capacity_groups if self.safe_int(item.get("market_price")) > 0
            ),
            "branch_rank": len(capacity_groups),
            "selected_gprice_labels": sorted({label for label in selected_labels if label}, reverse=True)[:3],
            "resolution_source": "mixed_snapshot_rows",
            "capacity_groups": capacity_groups,
        }

    def _group_rows_by_variant(self, rows: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            groups.setdefault(str(row.get("group_title") or "").strip(), []).append(row)
        return groups

    def _upsert_capacity_variant(self, capacity_map: Dict[str, Dict[str, Any]], variant: Dict[str, Any]) -> None:
        capacity_label, color_label = self.split_group_spec(str(variant.get("group_title") or ""))
        bucket = capacity_map.get(capacity_label)
        if bucket is None:
            bucket = {
                "capacity_label": capacity_label,
                "market_price": 0,
                "price_range": "--",
                "sample_count": 0,
                "seller_count": 0,
                "latest_imported_at": "",
                "trusted_status": "",
                "raw_status": "",
                "reference_price": 0,
                "reference_source_name": "",
                "reference_fetched_at": "",
                "selection_source": "",
                "selected_gprice_labels": [],
                "colors": [],
            }
            capacity_map[capacity_label] = bucket

        color_payload = {
            "color_label": color_label,
            "group_title": str(variant.get("group_title") or ""),
            "market_price": self.safe_int(variant.get("market_price")),
            "price_range": str(variant.get("price_range") or "--"),
            "sample_count": self.safe_int(variant.get("sample_count")),
            "seller_count": self.safe_int(variant.get("seller_count")),
            "latest_imported_at": str(variant.get("latest_imported_at") or ""),
            "trusted_status": str(variant.get("trusted_status") or ""),
            "raw_status": str(variant.get("raw_status") or ""),
            "selection_source": str(variant.get("selection_source") or ""),
            "selected_gprice_labels": list(variant.get("selected_gprice_labels") or []),
        }
        bucket["colors"].append(color_payload)

        candidate_rank = (
            self.safe_int(color_payload["sample_count"]),
            self.safe_int(color_payload["seller_count"]),
            color_payload["latest_imported_at"],
        )
        current_rank = (
            self.safe_int(bucket.get("sample_count")),
            self.safe_int(bucket.get("seller_count")),
            str(bucket.get("latest_imported_at") or ""),
        )
        if candidate_rank >= current_rank:
            bucket["market_price"] = self.safe_int(variant.get("market_price"))
            bucket["price_range"] = str(variant.get("price_range") or "--")
            bucket["sample_count"] = self.safe_int(variant.get("sample_count"))
            bucket["seller_count"] = self.safe_int(variant.get("seller_count"))
            bucket["latest_imported_at"] = str(variant.get("latest_imported_at") or "")
            bucket["trusted_status"] = str(variant.get("trusted_status") or "")
            bucket["raw_status"] = str(variant.get("raw_status") or "")
            bucket["reference_price"] = self.safe_int(variant.get("reference_price"))
            bucket["reference_source_name"] = str(variant.get("reference_source_name") or "")
            bucket["reference_fetched_at"] = str(variant.get("reference_fetched_at") or "")
            bucket["selection_source"] = str(variant.get("selection_source") or "")
            bucket["selected_gprice_labels"] = list(variant.get("selected_gprice_labels") or [])

    def _finalize_capacity_groups(self, capacity_map: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
        capacity_groups = list(capacity_map.values())
        for bucket in capacity_groups:
            bucket["colors"].sort(
                key=lambda item: (
                    self.safe_int(item.get("sample_count")),
                    self.safe_int(item.get("seller_count")),
                    str(item.get("latest_imported_at") or ""),
                ),
                reverse=True,
            )
        capacity_groups.sort(key=lambda item: self.capacity_sort_key(str(item.get("capacity_label") or "")))
        return capacity_groups

    def _variant_from_snapshot_row(
        self,
        snapshot_row: Dict[str, Any],
        *,
        reference_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "group_title": self.normalize_variant_title(snapshot_row.get("group_title") or ""),
            "market_price": self.safe_int(snapshot_row.get("market_price")),
            "price_range": str(snapshot_row.get("price_range") or "--"),
            "sample_count": self.safe_int(snapshot_row.get("trusted_sample_count")),
            "seller_count": self.safe_int(snapshot_row.get("trusted_seller_count")),
            "latest_imported_at": str(snapshot_row.get("latest_imported_at") or ""),
            "selected_gprice_labels": [
                str(label or "").strip()
                for label in str(snapshot_row.get("selected_gprice_labels") or "").split("|")
                if str(label or "").strip()
            ],
            "trusted_status": self.friendly_status_text(snapshot_row.get("trusted_status") or ""),
            "raw_status": str(snapshot_row.get("trusted_status") or ""),
            "reference_price": self.safe_int(snapshot_row.get("reference_price")),
            "reference_source_name": str(snapshot_row.get("reference_source_name") or "").strip()
            or str(reference_context.get("source_name") or ""),
            "reference_fetched_at": str(snapshot_row.get("reference_fetched_at") or "").strip()
            or str(reference_context.get("fetched_at") or ""),
            "selection_source": "published_snapshot",
        }

    def _variant_from_rows(
        self,
        *,
        rows: List[Dict[str, Any]],
        brand_title: str,
        series_title: str,
        model_title: str,
        group_title: str,
        reference_map: Dict[str, Any],
        reference_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        market_v1 = build_market_v1(
            rows=rows,
            brand_title=brand_title,
            series_title=series_title,
            model_title=model_title,
            group_title=group_title,
            reference_map=reference_map,
            reference_context=None,
        )
        if not market_v1.get("ok"):
            return {}

        deduped_rows = dedupe_latest_rows(rows)
        selected_rows, selected_labels = select_recent_sample_window(numeric_rows(deduped_rows), min_samples=10, max_labels=3)
        selected_prices = [self.safe_int(row.get("price_value")) for row in selected_rows if self.safe_int(row.get("price_value")) > 0]
        best = dict(market_v1.get("best_cluster") or {})
        reference = dict(market_v1.get("reference") or {})

        return {
            "group_title": self.normalize_variant_title(group_title),
            "market_price": self.safe_int(best.get("median_price")) or percentile(selected_prices, 0.5),
            "price_range": str(best.get("price_range") or self.range_text(selected_prices)),
            "sample_count": self.safe_int(best.get("sample_count")) or len(selected_rows),
            "seller_count": self.safe_int(best.get("seller_count")) or len(selected_rows),
            "latest_imported_at": max((str(row.get("imported_at") or "") for row in rows), default=""),
            "selected_gprice_labels": list(selected_labels),
            "trusted_status": self.friendly_status_text(best.get("dstatus") or ""),
            "raw_status": str(best.get("dstatus") or ""),
            "reference_price": self.safe_int(reference.get("price")),
            "reference_source_name": str(reference.get("source_title") or reference_context.get("source_name") or ""),
            "reference_fetched_at": str(reference.get("fetched_at") or reference_context.get("fetched_at") or ""),
            "selection_source": "computed_from_rows",
        }
