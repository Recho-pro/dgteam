from __future__ import annotations

import argparse
import time

from dgteam.core.config import load_settings
from dgteam.core.console_encoding import configure_utf8_stdio
from dgteam.core.logging import setup_logging
from dgteam.core.project_config import load_project_config
from dgteam.integrations.wechat_official.client import WechatOfficialClient
from dgteam.integrations.wechat_official.image_recognizer import WechatOfficialEcommerceImageRecognizer
from dgteam.integrations.wechat_official.image_runtime import WechatOfficialImageRuntimeProfile
from dgteam.integrations.wechat_official.recognition_queue import WechatOfficialRecognitionQueue
from dgteam.integrations.wechat_official.result_dispatcher import WechatOfficialImageResultDispatcher
from dgteam.integrations.wechat_official.recognition_worker import (
    NullWechatOfficialImageRecognizer,
    WechatOfficialRecognitionWorker,
)
from dgteam.integrations.wechat_official.response_layer import WechatOfficialMarketResponseLayer
from dgteam.integrations.wechat_official.session_store import WechatOfficialSessionStore
from dgteam.integrations.wechat_official.trace import WechatOfficialTraceLogger
from dgteam.query_api.service import QueryService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DGTEAM WeChat official image recognition worker")
    parser.add_argument("--run-once", action="store_true", help="Process at most one queued image task.")
    return parser.parse_args()


def build_worker() -> tuple[WechatOfficialRecognitionWorker, float]:
    settings = load_settings()
    project_config = load_project_config()
    queue = WechatOfficialRecognitionQueue(settings.wechat_official.state_dir / "recognition")
    client = WechatOfficialClient(config=settings.wechat_official)
    query_service = QueryService(db_path=project_config.paths.db_path)
    response_layer = WechatOfficialMarketResponseLayer(query_service=query_service)
    session_store = WechatOfficialSessionStore(settings.wechat_official.state_dir / "sessions")
    trace_logger = WechatOfficialTraceLogger(settings.wechat_official.state_dir / "trace")
    image_runtime = WechatOfficialImageRuntimeProfile.from_config(settings.wechat_official)

    if image_runtime.worker_enabled:
        recognizer = WechatOfficialEcommerceImageRecognizer(
            client=client,
            api_key=settings.wechat_official.image_api_key,
            primary_model=settings.wechat_official.image_primary_model,
            fallback_model=settings.wechat_official.image_fallback_model,
            cache_dir=settings.wechat_official.state_dir / "recognition" / "cache",
            cache_namespace="slow_lane",
            recognition_profile="full",
            timeout_seconds=settings.wechat_official.image_timeout_seconds,
            max_edge_px=settings.wechat_official.image_max_edge_px,
            max_bytes=settings.wechat_official.image_max_bytes,
            jpeg_quality=settings.wechat_official.image_jpeg_quality,
        )
    else:
        recognizer = NullWechatOfficialImageRecognizer(
            primary_model=settings.wechat_official.image_primary_model,
            fallback_model=settings.wechat_official.image_fallback_model,
        )
    dispatcher = WechatOfficialImageResultDispatcher(
        client=client,
        response_layer=response_layer,
        session_store=session_store,
        trace_logger=trace_logger,
    )
    worker = WechatOfficialRecognitionWorker(
        queue=queue,
        recognizer=recognizer,
        result_handler=dispatcher,
    )
    return worker, max(0.2, float(settings.wechat_official.image_poll_interval_seconds))


def main() -> None:
    configure_utf8_stdio()
    settings = load_settings()
    setup_logging(settings.log_level)
    args = parse_args()
    worker, poll_interval = build_worker()
    if args.run_once:
        worker.run_once()
        return
    while True:
        task = worker.run_once()
        if task is None:
            time.sleep(poll_interval)


if __name__ == "__main__":
    main()
