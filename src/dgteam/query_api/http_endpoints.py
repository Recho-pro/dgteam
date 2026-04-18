from __future__ import annotations

from typing import Any, Dict, List, Mapping, Tuple

from dgteam.query_api.contracts import MODEL_SUGGESTION_LIMIT


def query_value(params: Mapping[str, List[str]], *names: str, default: str = "") -> str:
    for name in names:
        values = params.get(name)
        if values:
            return str(values[0] or "").strip()
    return default


def query_int_value(
    params: Mapping[str, List[str]],
    *names: str,
    default: int,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    raw_value = query_value(params, *names, default=str(default))
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        value = int(default)
    if minimum is not None:
        value = max(value, minimum)
    if maximum is not None:
        value = min(value, maximum)
    return value


def health_payload(app: Any) -> Dict[str, Any]:
    status_payload = app.status_payload()
    db_path = getattr(app, "db_path", None)
    if db_path is None:
        storage = getattr(app, "storage", None)
        db_path = getattr(storage, "db_path", "")
    return {
        "ok": True,
        "service": "dgteam-query-api",
        "db_path": str(db_path or ""),
        "status": {
            "run_key": status_payload.get("live", {}).get("run_key", ""),
            "quote_count": status_payload.get("live", {}).get("quote_count", 0),
            "market_snapshot_count": status_payload.get("live", {}).get("market_snapshot_count", 0),
            "published_at": status_payload.get("live", {}).get("published_at", ""),
        },
    }


def search_payload(app: Any, params: Mapping[str, List[str]]) -> Dict[str, Any]:
    query = query_value(params, "q", "query")
    limit = query_int_value(params, "limit", default=MODEL_SUGGESTION_LIMIT, minimum=1, maximum=MODEL_SUGGESTION_LIMIT)
    return app.search_payload(query, limit=limit)


def sku_payload(app: Any, params: Mapping[str, List[str]]) -> Tuple[Dict[str, Any], int]:
    payload = app.snapshot_payload(
        data_source=query_value(params, "source", "data_source", default="quote_rows"),
        external_key=query_value(params, "external_key"),
        detail_key=query_value(params, "detail_key", "detailKey", "key"),
        brand_title=query_value(params, "brand", "brand_title"),
        series_title=query_value(params, "series", "series_title"),
        model_title=query_value(params, "model", "model_title"),
        family_title=query_value(params, "family", "family_title"),
        group_title=query_value(params, "group", "group_title"),
        condition_bucket=query_value(params, "bucket", "condition_bucket"),
        refinement_query=query_value(params, "refinement_query", "refinement"),
    )
    status = 200 if payload.get("ok") else 404
    if payload.get("error_code") in {"invalid_detail_key", "missing_model_identifier"}:
        status = 400
    return payload, status
