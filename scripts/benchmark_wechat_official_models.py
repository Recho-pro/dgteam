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
from dgteam.integrations.wechat_official.image_recognizer import WechatOfficialEcommerceImageRecognizer
from dgteam.integrations.wechat_official.response_layer import WechatOfficialMarketResponseLayer
from dgteam.query_api.service import QueryService


DEFAULT_MODELS = [
    "qwen/qwen3-vl-32b-instruct",
    "qwen/qwen3-vl-235b-a22b-instruct",
    "google/gemini-2.5-flash",
    "openai/gpt-4.1-mini",
    "openai/gpt-4.1",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark WeChat official-account image recognition models against real screenshots.",
    )
    parser.add_argument(
        "--image",
        dest="images",
        action="append",
        required=True,
        help="Absolute path to an ecommerce detail screenshot. Repeat for multiple images.",
    )
    parser.add_argument(
        "--model",
        dest="models",
        action="append",
        help="OpenRouter model id. Repeat to compare multiple models.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("runtime/local/wechat_model_benchmark.json"),
        help="Where to write the benchmark JSON report.",
    )
    return parser.parse_args()


def build_recognizer() -> WechatOfficialEcommerceImageRecognizer:
    settings = load_settings()
    conf = settings.wechat_official
    return WechatOfficialEcommerceImageRecognizer(
        client=object(),
        api_key=conf.image_api_key,
        primary_model="",
        fallback_model="",
        cache_dir=settings.local_root / "tmp" / "wechat_model_benchmark_cache",
        timeout_seconds=conf.image_timeout_seconds,
        max_edge_px=conf.image_max_edge_px,
        max_bytes=conf.image_max_bytes,
        jpeg_quality=conf.image_jpeg_quality,
    )


def main() -> None:
    configure_utf8_stdio()
    args = parse_args()
    settings = load_settings()
    project = load_project_config()

    recognizer = build_recognizer()
    query_service = QueryService(db_path=project.paths.db_path)
    response_layer = WechatOfficialMarketResponseLayer(query_service=query_service)
    models = args.models or DEFAULT_MODELS

    results: list[dict[str, Any]] = []
    for image_arg in args.images:
        image_path = Path(image_arg).expanduser().resolve()
        raw_bytes = image_path.read_bytes()
        preprocess_started = time.perf_counter()
        processed_name, processed_bytes, preprocess_meta = recognizer._prepare_for_model(
            image_name=image_path.name,
            image_bytes=raw_bytes,
        )
        preprocess_ms = round((time.perf_counter() - preprocess_started) * 1000, 1)

        for model in models:
            row: dict[str, Any] = {
                "image": str(image_path),
                "image_name": image_path.name,
                "model": model,
                "preprocess_ms": preprocess_ms,
                "processed_size": preprocess_meta.get("processed_size"),
                "processed_bytes": len(processed_bytes),
            }

            started = time.perf_counter()
            try:
                payload = recognizer._recognize_with_model(
                    image_name=processed_name,
                    image_bytes=processed_bytes,
                    model=model,
                )
                row["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
                row["status"] = "ok"
                row["payload"] = payload

                candidates = recognizer._build_candidates(payload)
                row["normalized_candidates"] = candidates

                for query in candidates[:4]:
                    plan = response_layer.resolve_query(
                        query,
                        limit=3,
                        preferred_brand=str(payload.get("brand") or "").strip(),
                        preferred_family=str(payload.get("family") or "").strip(),
                    )
                    if plan.kind == "snapshot":
                        row["plan_kind"] = "snapshot"
                        row["matched_query"] = query
                        row["snapshot_title"] = plan.snapshot.get("header", {}).get("title")
                        row["price_range"] = plan.snapshot.get("market_v1", {}).get("price_range")
                        break
                    if plan.kind == "ambiguous" and "plan_kind" not in row:
                        row["plan_kind"] = "ambiguous"
                        row["matched_query"] = query
                        row["ambiguous_results"] = [
                            str(item.get("label") or item.get("family_title") or "")
                            for item in plan.results[:3]
                        ]
                row.setdefault("plan_kind", "no_result")
            except Exception as exc:
                row["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
                row["status"] = "error"
                row["error"] = str(exc)

            results.append(row)

    report = {
        "generated_at": int(time.time()),
        "db_path": str(project.paths.db_path),
        "models": models,
        "results": results,
    }
    output_path = args.output.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_json_utf8(output_path, report)

    print(f"wrote {output_path}")
    for row in results:
        print(
            json.dumps(
                {
                    "image_name": row["image_name"],
                    "model": row["model"],
                    "status": row["status"],
                    "elapsed_ms": row["elapsed_ms"],
                    "plan_kind": row.get("plan_kind"),
                    "snapshot_title": row.get("snapshot_title"),
                    "matched_query": row.get("matched_query"),
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
