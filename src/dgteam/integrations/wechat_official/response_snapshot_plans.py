from __future__ import annotations

from typing import Any

from dgteam.integrations.wechat_official.formatter import (
    format_market_capacity_refinement,
    format_market_variant_refinement,
)
from dgteam.integrations.wechat_official.models import WechatOfficialMarketReplyPlan
from dgteam.query_api.service import QueryService


def lookup_snapshot_for_candidate(
    query_service: QueryService,
    candidate: dict[str, object],
    *,
    refinement_query: str = "",
) -> dict[str, object]:
    query_ref = dict(candidate.get("query_ref") or {})
    snapshot_lookup = getattr(query_service, "snapshot", None)
    if snapshot_lookup is None:
        snapshot_lookup = query_service.app.snapshot_payload
    return snapshot_lookup(
        data_source=str(query_ref.get("data_source") or candidate.get("data_source") or "quote_rows"),
        external_key=str(query_ref.get("external_key") or candidate.get("external_key") or ""),
        detail_key=str(query_ref.get("detail_key") or candidate.get("detail_key") or ""),
        brand_title=str(query_ref.get("brand_title") or candidate.get("brand_title") or ""),
        series_title=str(query_ref.get("series_title") or candidate.get("series_title") or ""),
        model_title=str(query_ref.get("model_title") or candidate.get("model_title") or ""),
        family_title=str(query_ref.get("family_title") or candidate.get("family_title") or ""),
        group_title=str(query_ref.get("group_title") or candidate.get("group_title") or ""),
        condition_bucket=str(query_ref.get("condition_bucket") or candidate.get("condition_bucket") or ""),
        refinement_query=str(refinement_query or ""),
    )


def build_backend_refinement_plan(
    *,
    query_service: QueryService,
    candidate: dict[str, object],
    refinement_query: str,
    strip_auto_prefix: bool = False,
) -> WechatOfficialMarketReplyPlan:
    snapshot = lookup_snapshot_for_candidate(
        query_service,
        candidate,
        refinement_query=refinement_query,
    )
    if not snapshot.get("ok"):
        return WechatOfficialMarketReplyPlan(kind="empty")

    refinement = dict(dict(snapshot.get("resolution") or {}).get("refinement") or {})
    if not refinement.get("applied"):
        return WechatOfficialMarketReplyPlan(kind="empty")

    selection = _first_backend_refinement_selection(snapshot)
    if not selection:
        return WechatOfficialMarketReplyPlan(kind="empty")

    if selection["kind"] == "variant":
        reply_text = format_market_variant_refinement(
            candidate=candidate,
            snapshot=snapshot,
            refinement_query=refinement_query,
            capacity_group=selection["capacity_group"],
            variant=selection["variant"],
        )
    else:
        reply_text = format_market_capacity_refinement(
            candidate=candidate,
            snapshot=snapshot,
            refinement_query=refinement_query,
            capacity_group=selection["capacity_group"],
        )
    if strip_auto_prefix:
        reply_text = strip_auto_refinement_prefix(reply_text)

    return WechatOfficialMarketReplyPlan(
        kind="snapshot",
        query=refinement_query,
        candidate=candidate,
        snapshot=snapshot,
        reply_text=reply_text,
    )


def _first_backend_refinement_selection(snapshot: dict[str, object]) -> dict[str, Any]:
    resolution = dict(snapshot.get("resolution") or {})
    refinement = dict(resolution.get("refinement") or {})
    branches = [dict(item or {}) for item in list(snapshot.get("branches") or [])]
    for branch in branches:
        groups = [dict(item or {}) for item in list(branch.get("capacity_groups") or [])]
        if not groups:
            continue
        group = groups[0]
        colors = [dict(item or {}) for item in list(group.get("colors") or [])]
        matched_colors = [item for item in colors if item.get("__matched")]
        visible_colors = matched_colors or colors
        if int(refinement.get("matched_color_count") or 0) == 1 and len(visible_colors) == 1:
            narrowed_group = dict(group)
            narrowed_group["colors"] = visible_colors
            return {
                "kind": "variant",
                "capacity_group": narrowed_group,
                "variant": dict(visible_colors[0] or {}),
            }
        return {
            "kind": "capacity",
            "capacity_group": group,
        }
    return {}


def build_refinement_plan_from_snapshot(
    *,
    candidate: dict[str, object],
    snapshot: dict[str, object],
    refinement_query: str,
    refinement: dict[str, Any],
) -> WechatOfficialMarketReplyPlan:
    if refinement["kind"] == "variant":
        reply_text = format_market_variant_refinement(
            candidate=candidate,
            snapshot=snapshot,
            refinement_query=refinement_query,
            capacity_group=refinement["capacity_group"],
            variant=refinement["variant"],
        )
    else:
        reply_text = format_market_capacity_refinement(
            candidate=candidate,
            snapshot=snapshot,
            refinement_query=refinement_query,
            capacity_group=refinement["capacity_group"],
        )

    return WechatOfficialMarketReplyPlan(
        kind="snapshot",
        query=refinement_query,
        candidate=candidate,
        snapshot=snapshot,
        reply_text=reply_text,
    )


def strip_auto_refinement_prefix(reply_text: str) -> str:
    lines = [line for line in str(reply_text or "").splitlines() if line.strip()]
    filtered = [line for line in lines if not line.startswith("你刚刚补的是：")]
    return "\n".join(filtered)
