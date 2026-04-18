from __future__ import annotations

import argparse
import logging

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
import uvicorn

from dgteam.core.config import load_settings
from dgteam.core.console_encoding import configure_utf8_stdio
from dgteam.core.logging import setup_logging
from dgteam.core.paths import ensure_runtime_dirs
from dgteam.core.project_config import load_project_config
from dgteam.integrations.wechat_clawbot.service import WechatClawbotBridgeService
from dgteam.integrations.wechat_clawbot.wecom_crypto import WecomCryptoError
from dgteam.integrations.wechat_clawbot.wecom_client import WecomApiError

LOGGER = logging.getLogger("dgteam.wecom_bridge.app")


def build_app() -> FastAPI:
    settings = load_settings()
    project_config = load_project_config()
    service = WechatClawbotBridgeService(
        config=settings.wechat_clawbot,
        db_path=project_config.paths.db_path,
    )
    app = FastAPI(title="DGTEAM WeCom Customer Service Bridge", version="0.2.0")
    callback_path = settings.wechat_clawbot.callback_path or "/wechat/kf/callback"

    @app.get("/health")
    def health() -> dict[str, object]:
        return service.health_payload()

    @app.get(callback_path)
    def verify_callback(
        msg_signature: str = Query(default=""),
        timestamp: str = Query(default=""),
        nonce: str = Query(default=""),
        echostr: str = Query(default=""),
    ) -> PlainTextResponse:
        LOGGER.info(
            "wecom callback verify request path=%s timestamp=%s nonce=%s has_signature=%s has_echostr=%s",
            callback_path,
            timestamp,
            nonce,
            bool(msg_signature),
            bool(echostr),
        )
        try:
            plain = service.verify_callback_url(
                msg_signature=msg_signature,
                timestamp=timestamp,
                nonce=nonce,
                echostr=echostr,
            )
        except WecomCryptoError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return PlainTextResponse(plain, media_type="text/plain; charset=utf-8")

    @app.post(callback_path)
    async def callback(
        request: Request,
        msg_signature: str = Query(default=""),
        timestamp: str = Query(default=""),
        nonce: str = Query(default=""),
    ) -> PlainTextResponse:
        body_bytes = await request.body()
        body = body_bytes.decode("utf-8", errors="replace")
        LOGGER.info(
            "wecom callback post path=%s timestamp=%s nonce=%s has_signature=%s body_length=%s",
            callback_path,
            timestamp,
            nonce,
            bool(msg_signature),
            len(body),
        )
        try:
            result = service.handle_wecom_callback(
                raw_body=body,
                msg_signature=msg_signature,
                timestamp=timestamp,
                nonce=nonce,
            )
            LOGGER.info(
                "wecom callback processed ok callback_type=%s processed_count=%s",
                result.get("callback_type"),
                result.get("processed_count"),
            )
        except (WecomCryptoError, WecomApiError, ValueError) as exc:
            LOGGER.exception("wecom callback failed: %s", exc)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return PlainTextResponse("success", media_type="text/plain; charset=utf-8")

    @app.post("/webhook/event")
    def webhook_event(payload: dict, x_dgteam_secret: str | None = Header(default=None)) -> dict[str, object]:
        expected = settings.wechat_clawbot.shared_secret
        if expected and x_dgteam_secret != expected:
            raise HTTPException(status_code=401, detail="invalid clawbot secret")
        return service.ingest_payload(payload)

    @app.post("/webhook/command")
    def webhook_command(payload: dict, x_dgteam_secret: str | None = Header(default=None)) -> dict[str, object]:
        expected = settings.wechat_clawbot.shared_secret
        if expected and x_dgteam_secret != expected:
            raise HTTPException(status_code=401, detail="invalid clawbot secret")
        return service.route_command(payload)

    @app.exception_handler(HTTPException)
    async def _http_error_handler(_: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "ok": False,
                "error": exc.detail,
            },
        )

    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DGTEAM WeCom customer-service bridge")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    return parser.parse_args()


def main() -> None:
    configure_utf8_stdio()
    args = parse_args()
    settings = load_settings()
    setup_logging(settings.log_level)
    ensure_runtime_dirs(settings)
    host = args.host or settings.wechat_clawbot.host
    port = args.port or settings.wechat_clawbot.port
    uvicorn.run(build_app(), host=host, port=port)


if __name__ == "__main__":
    main()
