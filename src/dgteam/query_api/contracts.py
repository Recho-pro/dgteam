from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict


MODEL_SUGGESTION_LIMIT = 6
API_CONTRACT_VERSION = "query-ui.v2"
DETAIL_REF_VERSION = 1
DETAIL_CONTRACT_VERSION = "sku-detail.v2"

ENDPOINT_CONTRACTS: Dict[str, Dict[str, Any]] = {
    "/health": {
        "method": "GET",
        "owned_by": "query_api",
        "purpose": "Runtime liveness and live-data marker.",
        "required_response_fields": ["ok", "service", "db_path", "status"],
        "status_fields": ["run_key", "quote_count", "market_snapshot_count", "published_at"],
        "compatibility": "Additive fields only; keep current status object shape.",
    },
    "/api/status": {
        "method": "GET",
        "owned_by": "query_api",
        "purpose": "Live run metadata, hot queries, reference import context, and endpoint contract discovery.",
        "required_response_fields": [
            "ok",
            "contract_version",
            "live",
            "summary",
            "reference_import",
            "hot_queries",
            "endpoint_contracts",
            "backend_owned_logic",
        ],
        "compatibility": "Hot query item fields remain stable for existing query_ui consumers.",
    },
    "/api/search": {
        "method": "GET",
        "owned_by": "query_api",
        "query_params": {
            "q|query": "User search text.",
            "limit": f"Optional integer clamped to 1..{MODEL_SUGGESTION_LIMIT}.",
        },
        "purpose": "Backend-owned search normalization, candidate aggregation ranking, and query_ref/detail_key emission.",
        "required_response_fields": ["ok", "contract_version", "query", "run_key", "results"],
        "result_required_fields": [
            "data_source",
            "brand_title",
            "series_title",
            "model_title",
            "family_title",
            "condition_bucket",
            "label",
            "meta",
            "score",
            "detail_key",
            "detail_contract",
            "query_ref",
            "explain",
        ],
        "compatibility": "Existing consumers may keep using brand/series/model fields or move to query_ref/detail_key.",
    },
    "/api/sku": {
        "method": "GET",
        "owned_by": "query_api",
        "query_params": {
            "detail_key|detailKey|key": "Preferred stable detail reference emitted by /api/search.",
            "source|data_source": "Compatibility fallback data source.",
            "external_key": "Compatibility fallback for external reference details.",
            "brand|brand_title": "Compatibility fallback brand title.",
            "series|series_title": "Compatibility fallback series title.",
            "model|model_title": "Compatibility fallback model title.",
            "family|family_title": "Compatibility fallback family title.",
            "group|group_title": "Compatibility fallback group title.",
            "bucket|condition_bucket": "Compatibility fallback condition bucket.",
            "refinement_query|refinement": "Optional backend-owned narrowing text scoped to the resolved family snapshot.",
        },
        "purpose": "Backend-owned detail resolution, snapshot assembly, and optional family-scoped refinement.",
        "required_response_fields": ["ok", "contract_version", "query", "header", "hero", "market_v1", "resolution", "branches"],
        "error_codes": ["invalid_detail_key", "missing_model_identifier", "model_not_found", "no_usable_price"],
        "compatibility": "detail_key is preferred, but legacy field-based lookups remain supported and refinement_query is additive.",
    },
    "/api/*": {
        "method": "GET",
        "owned_by": "query_api",
        "purpose": "Unknown API routes return a stable JSON 404 error instead of falling through to static asset handling.",
        "status": 404,
        "required_response_fields": ["ok", "error", "error_code", "request_id", "contract_version", "details"],
        "error_codes": ["unknown_api_endpoint"],
        "compatibility": "Static 404 behavior remains only for non-/api paths; API consumers always receive JSON errors.",
    },
}

BACKEND_OWNED_LOGIC = {
    "search_normalization": [
        "unicode normalization",
        "brand/pinyin alias expansion",
        "capacity/color refinement token detection",
        "category intent detection",
        "model-code bridge expansion",
    ],
    "candidate_aggregation": [
        "quote row family aggregation",
        "hot query candidate selection",
        "candidate scoring and ranking",
        "detail_key/query_ref generation",
    ],
    "refinement": [
        "refinement-only query detection",
        "detail_key based family resolution",
        "snapshot-level capacity/color narrowing",
        "live run fallback for stale detail keys",
        "condition bucket resolution",
    ],
    "snapshot_assembly": [
        "branch assembly",
        "capacity/color group assembly",
        "reference market merge",
        "market_v1 hero/default capacity selection",
    ],
    "static_resource_serving": [
        "index.html no-store headers",
        "versioned JS/CSS immutable cache headers",
        "utf-8 content type normalization",
    ],
}


def endpoint_contracts_payload() -> Dict[str, Dict[str, Any]]:
    return deepcopy(ENDPOINT_CONTRACTS)


def backend_owned_logic_payload() -> Dict[str, Any]:
    return deepcopy(BACKEND_OWNED_LOGIC)
