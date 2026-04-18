from __future__ import annotations

from typing import Any, Dict

from dgteam.query_api.contracts import API_CONTRACT_VERSION


UNKNOWN_API_ERROR_CODE = "unknown_api_endpoint"
UNSUPPORTED_METHOD_ERROR_CODE = "unsupported_method"


def api_error_payload(
    message: str,
    *,
    code: str,
    request_id: str,
    details: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    return {
        "ok": False,
        "error": str(message or "Request failed"),
        "error_code": str(code or "api_error"),
        "request_id": request_id,
        "contract_version": API_CONTRACT_VERSION,
        "details": dict(details or {}),
    }


def unknown_api_payload(path: str, *, request_id: str) -> Dict[str, Any]:
    normalized_path = str(path or "").strip() or "/api"
    return api_error_payload(
        "Unknown API endpoint.",
        code=UNKNOWN_API_ERROR_CODE,
        request_id=request_id,
        details={
            "path": normalized_path,
            "method": "GET",
            "contract": "Unknown /api/* paths must return JSON 404 responses, not static asset fallbacks.",
        },
    )


def unsupported_method_payload(method: str, path: str, *, request_id: str) -> Dict[str, Any]:
    normalized_method = str(method or "").strip().upper() or "UNKNOWN"
    normalized_path = str(path or "").strip() or "/"
    return api_error_payload(
        "Unsupported endpoint or method.",
        code=UNSUPPORTED_METHOD_ERROR_CODE,
        request_id=request_id,
        details={
            "path": normalized_path,
            "method": normalized_method,
        },
    )
