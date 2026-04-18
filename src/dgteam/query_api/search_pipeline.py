from __future__ import annotations

from typing import Any, Callable, Dict, List, Tuple

from dgteam.query_api.contracts import API_CONTRACT_VERSION, DETAIL_CONTRACT_VERSION, MODEL_SUGGESTION_LIMIT


RankedCandidate = Tuple[int, Dict[str, Any]]


def effective_search_limit(limit: int, *, maximum: int = MODEL_SUGGESTION_LIMIT) -> int:
    return max(1, min(int(limit or maximum), maximum))


def empty_search_payload(query: str, *, run_key: str) -> Dict[str, Any]:
    return {
        "ok": True,
        "contract_version": API_CONTRACT_VERSION,
        "query": str(query or ""),
        "run_key": str(run_key or ""),
        "results": [],
    }


def search_cache_key(
    *,
    run_key: str,
    latest_imported_at: str,
    query: str,
    limit: int,
    normalize_search_text: Callable[[Any], str],
) -> Tuple[str, str, str, int]:
    return (
        str(run_key or ""),
        str(latest_imported_at or ""),
        normalize_search_text(query),
        effective_search_limit(limit),
    )


def rank_candidates(
    query: str,
    candidates: List[Dict[str, Any]],
    *,
    query_context: Any,
    score_candidate: Callable[..., int],
    candidate_bucket_priority: Callable[[Dict[str, Any]], int],
    safe_int: Callable[[Any], int],
) -> List[RankedCandidate]:
    if getattr(query_context, "only_refinement", False):
        return []

    ranked: List[RankedCandidate] = []
    for candidate in candidates:
        score = score_candidate(query, candidate, context=query_context)
        if score >= 0:
            ranked.append((score, candidate))

    ranked.sort(
        key=lambda item: (
            item[0],
            candidate_bucket_priority(item[1]),
            safe_int(item[1].get("row_count")),
            safe_int(item[1].get("variant_count")),
            safe_int(item[1].get("source_count")),
            str(item[1].get("latest_imported_at") or ""),
        ),
        reverse=True,
    )

    brand_hints = set(getattr(query_context, "brand_hints", set()) or set())
    if brand_hints:
        preferred = [item for item in ranked if str(item[1].get("brand_title") or "").strip() in brand_hints]
        if preferred:
            ranked = preferred + [item for item in ranked if item not in preferred]

    return ranked


def build_search_results(
    ranked: List[RankedCandidate],
    *,
    run_key: str,
    limit: int,
    query_context: Any,
    detail_ref_for_candidate: Callable[[str, Dict[str, Any]], Tuple[Any, str]],
    detail_ref_query_payload: Callable[..., Dict[str, Any]],
    candidate_label: Callable[[Dict[str, Any]], str],
    candidate_meta: Callable[[Dict[str, Any]], str],
    candidate_explain: Callable[..., Dict[str, Any]],
    safe_int: Callable[[Any], int],
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for score, candidate in ranked[: effective_search_limit(limit)]:
        detail_ref, detail_key = detail_ref_for_candidate(run_key, candidate)
        results.append(
            {
                "data_source": "quote_rows",
                "brand_title": candidate.get("brand_title") or "",
                "series_title": candidate.get("series_title") or "",
                "model_title": candidate.get("model_title") or "",
                "family_title": candidate.get("family_title") or candidate.get("model_title") or "",
                "group_title": "",
                "condition_bucket": candidate.get("condition_bucket") or "",
                "row_count": safe_int(candidate.get("row_count")),
                "source_count": safe_int(candidate.get("source_count")),
                "variant_count": safe_int(candidate.get("variant_count")),
                "branch_count": safe_int(candidate.get("branch_count")),
                "latest_imported_at": candidate.get("latest_imported_at") or "",
                "latest_gprice": candidate.get("latest_gprice") or "",
                "label": candidate_label(candidate),
                "meta": candidate_meta(candidate),
                "score": score,
                "detail_key": detail_key,
                "detail_contract": DETAIL_CONTRACT_VERSION,
                "query_ref": detail_ref_query_payload(detail_ref, detail_key=detail_key),
                "explain": {
                    **candidate_explain(candidate, run_key=run_key),
                    "brand_hints": sorted(getattr(query_context, "brand_hints", set()) or set()),
                    "series_hints": sorted(getattr(query_context, "series_hints", set()) or set()),
                    "category_intents": sorted(getattr(query_context, "category_intents", set()) or set()),
                    "capacity_hints": sorted(getattr(query_context, "capacity_hints", set()) or set()),
                    "color_hints": sorted(getattr(query_context, "color_hints", set()) or set()),
                },
            }
        )
    return results


def build_search_payload(
    query: str,
    *,
    limit: int,
    meta: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    prepare_query_context: Callable[[str], Any],
    score_candidate: Callable[..., int],
    candidate_bucket_priority: Callable[[Dict[str, Any]], int],
    detail_ref_for_candidate: Callable[[str, Dict[str, Any]], Tuple[Any, str]],
    detail_ref_query_payload: Callable[..., Dict[str, Any]],
    candidate_label: Callable[[Dict[str, Any]], str],
    candidate_meta: Callable[[Dict[str, Any]], str],
    candidate_explain: Callable[..., Dict[str, Any]],
    safe_int: Callable[[Any], int],
) -> Dict[str, Any]:
    trimmed = str(query or "").strip()
    run_key = str(meta.get("run_key") or "")
    if not trimmed:
        return empty_search_payload("", run_key=run_key)

    query_context = prepare_query_context(trimmed)
    if getattr(query_context, "only_refinement", False):
        return empty_search_payload(trimmed, run_key=run_key)

    ranked = rank_candidates(
        trimmed,
        candidates,
        query_context=query_context,
        score_candidate=score_candidate,
        candidate_bucket_priority=candidate_bucket_priority,
        safe_int=safe_int,
    )
    return {
        "ok": True,
        "contract_version": API_CONTRACT_VERSION,
        "query": trimmed,
        "run_key": run_key,
        "results": build_search_results(
            ranked,
            run_key=run_key,
            limit=limit,
            query_context=query_context,
            detail_ref_for_candidate=detail_ref_for_candidate,
            detail_ref_query_payload=detail_ref_query_payload,
            candidate_label=candidate_label,
            candidate_meta=candidate_meta,
            candidate_explain=candidate_explain,
            safe_int=safe_int,
        ),
    }
