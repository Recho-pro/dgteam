from __future__ import annotations

from typing import Any, Dict

from dgteam.query_api.contracts import API_CONTRACT_VERSION, DETAIL_CONTRACT_VERSION


def legacy_snapshot_query(
    *,
    data_source: str,
    brand_title: str,
    series_title: str,
    model_title: str,
    family_title: str = "",
    group_title: str = "",
    condition_bucket: str = "",
    external_key: str = "",
    detail_key: str = "",
    refinement_query: str = "",
) -> Dict[str, Any]:
    payload = {
        "data_source": str(data_source or "quote_rows").strip() or "quote_rows",
        "brand_title": str(brand_title or "").strip(),
        "series_title": str(series_title or "").strip(),
        "model_title": str(model_title or "").strip(),
        "family_title": str(family_title or model_title or "").strip(),
        "group_title": str(group_title or "").strip(),
        "condition_bucket": str(condition_bucket or "").strip(),
        "external_key": str(external_key or "").strip(),
        "refinement_query": str(refinement_query or "").strip(),
    }
    if str(detail_key or "").strip():
        payload["detail_key"] = str(detail_key or "").strip()
    return payload


def snapshot_error_payload(
    *,
    code: str,
    message: str,
    query: Dict[str, Any],
    resolution: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "ok": False,
        "contract_version": API_CONTRACT_VERSION,
        "error": str(message or "Snapshot lookup failed."),
        "error_code": str(code or "snapshot_error"),
        "query": dict(query or {}),
    }
    if resolution is not None:
        payload["resolution"] = dict(resolution)
    return payload


def quote_resolution_payload(
    *,
    used_detail_key: bool,
    requested_run_key: str,
    effective_run_key: str,
    resolved_family_title: str,
    resolved_condition_bucket: str,
    branch_resolution_source: str,
    fallback_to_live_run: bool,
    resolved_branch_models: list[str],
) -> Dict[str, Any]:
    return {
        "contract_version": DETAIL_CONTRACT_VERSION,
        "used_detail_key": bool(used_detail_key),
        "requested_run_key": str(requested_run_key or ""),
        "effective_run_key": str(effective_run_key or ""),
        "resolved_family_title": str(resolved_family_title or ""),
        "resolved_condition_bucket": str(resolved_condition_bucket or ""),
        "branch_resolution_source": str(branch_resolution_source or ""),
        "fallback_to_live_run": bool(fallback_to_live_run),
        "resolved_branch_models": list(resolved_branch_models or []),
    }


def refinement_resolution_payload(refinement: Any, refinement_query: str) -> Dict[str, Any]:
    return {
        "requested_query": str(refinement_query or ""),
        "applied": bool(getattr(refinement, "applied", False)),
        "reason": str(getattr(refinement, "reason", "")),
        "summary": str(getattr(refinement, "summary", "")),
        "matched_branch_count": int(getattr(refinement, "matched_branches", 0) or 0),
        "matched_capacity_group_count": int(getattr(refinement, "matched_capacity_groups", 0) or 0),
        "matched_color_count": int(getattr(refinement, "matched_colors", 0) or 0),
    }
