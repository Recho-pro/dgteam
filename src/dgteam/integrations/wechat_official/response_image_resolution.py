from __future__ import annotations

from typing import Any

from dgteam.integrations.wechat_official.formatter import (
    format_image_candidates,
    format_image_market_snapshot,
    format_image_no_result,
)
from dgteam.integrations.wechat_official.models import WechatOfficialImageCandidateResolution


def resolve_image_candidate_queries(
    layer: Any,
    *,
    recognized_summary: str,
    candidate_queries: list[str],
    preferred_brand: str = "",
    preferred_family: str = "",
    query_limit: int = 3,
    max_queries: int = 4,
) -> WechatOfficialImageCandidateResolution:
    ordered_queries = dedupe_strings(candidate_queries)
    if not ordered_queries:
        return WechatOfficialImageCandidateResolution(
            kind="no_result",
            reply_text=format_image_no_result(recognized_summary=recognized_summary),
        )

    best_ambiguous_results: list[dict[str, object]] = []
    best_ambiguous_query = ""
    primary_query = ordered_queries[0]
    for query in ordered_queries[:max_queries]:
        plan = layer.resolve_query(
            query,
            limit=query_limit,
            preferred_brand=preferred_brand,
            preferred_family=preferred_family,
        )
        if plan.kind == "snapshot":
            market_reply = plan.reply_text
            refinement_query = layer._select_image_refinement_query(
                primary_query=primary_query,
                matched_query=query,
                candidate=dict(plan.candidate or {}),
                preferred_brand=preferred_brand,
                preferred_family=preferred_family,
            )
            refinement_queries = dedupe_strings(
                [
                    layer._build_contextual_refinement_query(
                        query=refinement_query,
                        candidate=dict(plan.candidate or {}),
                        preferred_brand=preferred_brand,
                        preferred_family=preferred_family,
                    )
                    if refinement_query
                    else "",
                    layer._build_contextual_refinement_query(
                        query=primary_query,
                        candidate=dict(plan.candidate or {}),
                        preferred_brand=preferred_brand,
                        preferred_family=preferred_family,
                    ),
                    layer._build_contextual_refinement_query(
                        query=query,
                        candidate=dict(plan.candidate or {}),
                        preferred_brand=preferred_brand,
                        preferred_family=preferred_family,
                    ),
                    refinement_query,
                    primary_query if primary_query.casefold() != query.casefold() else "",
                    query,
                ]
            )
            for refinement_query in refinement_queries:
                refined_plan = layer.resolve_refinement_query(
                    base_candidate=dict(plan.candidate or {}),
                    refinement_query=refinement_query,
                )
                if refined_plan.kind == "snapshot" and refined_plan.reply_text:
                    market_reply = layer._strip_auto_refinement_prefix(refined_plan.reply_text)
                    break
            resolved_title = str(
                plan.snapshot.get("header", {}).get("title")
                or plan.candidate.get("label")
                or plan.candidate.get("family_title")
                or plan.candidate.get("model_title")
                or ""
            ).strip()
            return WechatOfficialImageCandidateResolution(
                kind="snapshot",
                reply_text=format_image_market_snapshot(
                    recognized_summary=recognized_summary,
                    market_reply=market_reply,
                ),
                resolved_query=query,
                resolved_title=resolved_title,
                matched_query=query,
                resolved_candidate=dict(plan.candidate or {}),
            )
        if plan.kind == "ambiguous":
            current_results = [dict(item or {}) for item in list(plan.results or [])[:query_limit]]
            if not best_ambiguous_results:
                best_ambiguous_results = current_results
                best_ambiguous_query = query

    if best_ambiguous_results:
        labels = dedupe_strings(
            [
                str(item.get("label") or item.get("family_title") or item.get("model_title") or "").strip()
                for item in best_ambiguous_results
            ]
        )
        return WechatOfficialImageCandidateResolution(
            kind="ambiguous",
            reply_text=format_image_candidates(
                recognized_summary=recognized_summary,
                candidates=labels[:query_limit],
            ),
            pending_candidates=best_ambiguous_results,
            resolved_query=best_ambiguous_query or ordered_queries[0],
        )

    return WechatOfficialImageCandidateResolution(
        kind="no_result",
        reply_text=format_image_no_result(recognized_summary=recognized_summary),
    )


def dedupe_strings(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        clean = str(item or "").strip()
        if not clean:
            continue
        key = clean.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(clean)
    return ordered
