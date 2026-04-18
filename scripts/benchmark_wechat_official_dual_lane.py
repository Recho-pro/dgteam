from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from dgteam.core.config import load_settings
from dgteam.core.console_encoding import configure_utf8_stdio
from dgteam.core.project_config import load_project_config
from dgteam.core.textio import write_json_utf8
from dgteam.integrations.wechat_official.client import WechatOfficialClient
from dgteam.integrations.wechat_official.fast_lane import WechatOfficialImageFastLane
from dgteam.integrations.wechat_official.image_recognizer import WechatOfficialEcommerceImageRecognizer
from dgteam.integrations.wechat_official.models import WechatOfficialInboundMessage
from dgteam.integrations.wechat_official.recognition_queue import WechatOfficialRecognitionQueue
from dgteam.integrations.wechat_official.response_layer import WechatOfficialMarketResponseLayer
from dgteam.query_api.service import QueryService


class LocalImageClient:
    def __init__(self, image_bytes: bytes, image_name: str, content_type: str = "image/png"):
        self.image_bytes = image_bytes
        self.image_name = image_name
        self.content_type = content_type

    def download_media(self, media_id: str) -> tuple[bytes, str, str]:
        return self.image_bytes, self.image_name, self.content_type

    def download_image_url(self, url: str) -> tuple[bytes, str, str]:
        return self.image_bytes, self.image_name, self.content_type


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark the dual-lane WeChat official-account image query pipeline.",
    )
    parser.add_argument(
        "--image",
        dest="images",
        action="append",
        required=True,
        help="Absolute path to a screenshot. Repeat for multiple images.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("runtime/local/wechat_official_dual_lane_benchmark.json"),
        help="Where to write the benchmark report.",
    )
    parser.add_argument(
        "--api-key",
        default="",
        help="Override OpenRouter API key for this run. If omitted, read from project settings/environment.",
    )
    parser.add_argument(
        "--fast-model",
        default="",
        help="Override fast-lane model for this benchmark run.",
    )
    parser.add_argument(
        "--slow-model",
        default="",
        help="Override slow-lane primary model for this benchmark run.",
    )
    parser.add_argument(
        "--slow-fallback-model",
        default="",
        help="Override slow-lane fallback model for this benchmark run.",
    )
    return parser.parse_args()


def build_message() -> WechatOfficialInboundMessage:
    return WechatOfficialInboundMessage(
        msg_type="image",
        event="",
        event_key="",
        from_user="benchmark-openid",
        to_user="gh_benchmark",
        content="",
        media_id="benchmark-media",
        pic_url="",
        msg_id="benchmark-msg",
        raw_payload={},
    )


def _slow_resolution_payload(response_layer: WechatOfficialMarketResponseLayer, result) -> tuple[dict[str, Any], float]:
    started_at = time.perf_counter()
    resolution = response_layer.resolve_image_candidates(
        recognized_summary=str(result.summary or "").strip(),
        candidate_queries=[
            str(result.recognized_query or "").strip(),
            *[str(item or "").strip() for item in list(result.candidates or [])],
        ],
        preferred_brand=str(result.raw_payload.get("brand") or "").strip(),
        preferred_family=str(result.raw_payload.get("family") or "").strip(),
    )
    elapsed_ms = round((time.perf_counter() - started_at) * 1000, 1)
    return (
        {
            "kind": resolution.kind,
            "reply_preview": resolution.reply_text[:160],
            "resolved_query": resolution.resolved_query,
            "resolved_title": resolution.resolved_title,
            "matched_query": resolution.matched_query,
            "pending_candidate_count": len(list(resolution.pending_candidates or [])),
        },
        elapsed_ms,
    )


def benchmark_one_image(
    *,
    image_path: Path,
    api_key: str,
    fast_model: str,
    slow_model: str,
    slow_fallback_model: str,
    state_root: Path,
    query_service: QueryService,
) -> dict[str, Any]:
    raw_bytes = image_path.read_bytes()
    response_layer = WechatOfficialMarketResponseLayer(query_service=query_service)
    queue = WechatOfficialRecognitionQueue(state_root / image_path.stem / "recognition")

    client = LocalImageClient(
        image_bytes=raw_bytes,
        image_name=image_path.name,
        content_type="image/png" if image_path.suffix.lower() == ".png" else "image/jpeg",
    )
    fast_recognizer = WechatOfficialEcommerceImageRecognizer(
        client=client,  # type: ignore[arg-type]
        api_key=api_key,
        primary_model=fast_model,
        fallback_model="",
        cache_dir=state_root / image_path.stem / "cache",
        cache_namespace=f"benchmark_fast_{image_path.stem}",
        recognition_profile="fast",
        timeout_seconds=4,
        max_edge_px=768,
        max_bytes=140000,
        jpeg_quality=68,
    )
    slow_recognizer = WechatOfficialEcommerceImageRecognizer(
        client=client,  # type: ignore[arg-type]
        api_key=api_key,
        primary_model=slow_model,
        fallback_model=slow_fallback_model,
        cache_dir=state_root / image_path.stem / "cache",
        cache_namespace=f"benchmark_slow_{image_path.stem}",
        recognition_profile="full",
        timeout_seconds=45,
        max_edge_px=832,
        max_bytes=200000,
        jpeg_quality=66,
    )
    fast_lane = WechatOfficialImageFastLane(
        client=client,  # type: ignore[arg-type]
        queue=queue,
        recognizer=fast_recognizer,
        response_layer=response_layer,
    )

    message = build_message()
    fast_outcome = fast_lane.probe(message)

    slow_result_payload: dict[str, Any] | None = None
    slow_resolution_payload: dict[str, Any] | None = None
    slow_resolution_ms = 0.0
    if fast_outcome.status == "deferred":
        slow_started_at = time.perf_counter()
        slow_result = slow_recognizer.recognize(fast_outcome.task)
        slow_total_ms = round((time.perf_counter() - slow_started_at) * 1000, 1)
        slow_result_payload = {
            "status": slow_result.status,
            "confidence": slow_result.confidence,
            "recognized_query": slow_result.recognized_query,
            "candidates": list(slow_result.candidates or []),
            "summary": slow_result.summary,
            "model": slow_result.model,
            "timings_ms": {
                **dict(slow_result.raw_payload.get("timings_ms") or {}),
                "outer_total_ms": slow_total_ms,
            },
        }
        slow_resolution_payload, slow_resolution_ms = _slow_resolution_payload(response_layer, slow_result)

    return {
        "image": str(image_path),
        "fast": {
            "status": fast_outcome.status,
            "reply_preview": fast_outcome.reply_text[:160],
            "timings_ms": dict(fast_outcome.timings_ms or {}),
            "recognition": (
                {
                    "status": fast_outcome.recognition_result.status,
                    "confidence": fast_outcome.recognition_result.confidence,
                    "recognized_query": fast_outcome.recognition_result.recognized_query,
                    "candidates": list(fast_outcome.recognition_result.candidates or []),
                    "summary": fast_outcome.recognition_result.summary,
                    "model": fast_outcome.recognition_result.model,
                    "timings_ms": dict(fast_outcome.recognition_result.raw_payload.get("timings_ms") or {}),
                }
                if fast_outcome.recognition_result is not None
                else None
            ),
            "resolution": (
                {
                    "kind": fast_outcome.resolution.kind,
                    "resolved_query": fast_outcome.resolution.resolved_query,
                    "resolved_title": fast_outcome.resolution.resolved_title,
                    "matched_query": fast_outcome.resolution.matched_query,
                }
                if fast_outcome.resolution is not None
                else None
            ),
        },
        "slow": {
            "recognition": slow_result_payload,
            "resolution": slow_resolution_payload,
            "resolution_ms": slow_resolution_ms,
        },
    }


def summarize_bottleneck(rows: list[dict[str, Any]]) -> dict[str, Any]:
    buckets: dict[str, list[float]] = {
        "fast_download_ms": [],
        "fast_recognize_ms": [],
        "fast_resolve_ms": [],
        "fast_total_ms": [],
        "slow_preprocess_ms": [],
        "slow_model_ms": [],
        "slow_total_ms": [],
        "slow_resolve_ms": [],
    }

    for row in rows:
        fast_timings = dict(((row.get("fast") or {}).get("timings_ms") or {}))
        if fast_timings.get("download_ms") is not None:
            buckets["fast_download_ms"].append(float(fast_timings["download_ms"]))
        if fast_timings.get("fast_recognize_ms") is not None:
            buckets["fast_recognize_ms"].append(float(fast_timings["fast_recognize_ms"]))
        if fast_timings.get("fast_resolve_ms") is not None:
            buckets["fast_resolve_ms"].append(float(fast_timings["fast_resolve_ms"]))
        if fast_timings.get("total_ms") is not None:
            buckets["fast_total_ms"].append(float(fast_timings["total_ms"]))

        slow_recognition = dict((((row.get("slow") or {}).get("recognition") or {}).get("timings_ms") or {}))
        if slow_recognition.get("preprocess_ms") is not None:
            buckets["slow_preprocess_ms"].append(float(slow_recognition["preprocess_ms"]))
        if slow_recognition.get("model_ms") is not None:
            buckets["slow_model_ms"].append(float(slow_recognition["model_ms"]))
        if slow_recognition.get("outer_total_ms") is not None:
            buckets["slow_total_ms"].append(float(slow_recognition["outer_total_ms"]))
        slow_resolve_ms = (row.get("slow") or {}).get("resolution_ms")
        if slow_resolve_ms is not None:
            buckets["slow_resolve_ms"].append(float(slow_resolve_ms))

    averages = {
        key: round(sum(values) / len(values), 1) if values else None
        for key, values in buckets.items()
    }
    ranked = sorted(
        ((key, value) for key, value in averages.items() if value is not None),
        key=lambda item: item[1],
        reverse=True,
    )
    return {
        "averages_ms": averages,
        "ranked": [{"stage": key, "avg_ms": value} for key, value in ranked],
        "primary_bottleneck": ranked[0][0] if ranked else "",
    }


def main() -> None:
    configure_utf8_stdio()
    args = parse_args()
    settings = load_settings(Path(__file__).resolve().parents[1])
    project = load_project_config(Path(__file__).resolve().parents[1])
    query_service = QueryService(db_path=project.paths.db_path)

    api_key = str(args.api_key or settings.wechat_official.image_api_key or "").strip()
    if not api_key:
        raise SystemExit("No OpenRouter API key found. Pass --api-key or configure DGTEAM_WECHAT_OFFICIAL_IMAGE_API_KEY.")

    fast_model = str(args.fast_model or settings.wechat_official.image_fast_model).strip()
    slow_model = str(args.slow_model or settings.wechat_official.image_primary_model).strip()
    slow_fallback_model = str(args.slow_fallback_model or settings.wechat_official.image_fallback_model).strip()

    state_root = settings.local_root / "benchmarks" / "wechat_official_dual_lane"
    state_root.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    for image_arg in args.images:
        image_path = Path(image_arg).expanduser().resolve()
        rows.append(
            benchmark_one_image(
                image_path=image_path,
                api_key=api_key,
                fast_model=fast_model,
                slow_model=slow_model,
                slow_fallback_model=slow_fallback_model,
                state_root=state_root,
                query_service=query_service,
            )
        )

    report = {
        "generated_at": int(time.time()),
        "db_path": str(project.paths.db_path),
        "fast_model": fast_model,
        "slow_model": slow_model,
        "slow_fallback_model": slow_fallback_model,
        "rows": rows,
        "summary": summarize_bottleneck(rows),
    }

    output_path = args.output.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_json_utf8(output_path, report)
    print(f"wrote {output_path}")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
