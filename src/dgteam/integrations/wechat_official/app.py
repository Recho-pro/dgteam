from __future__ import annotations

import argparse
import logging

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
import uvicorn

from dgteam.core.config import load_settings
from dgteam.core.console_encoding import configure_utf8_stdio
from dgteam.core.logging import setup_logging
from dgteam.core.paths import ensure_runtime_dirs
from dgteam.core.project_config import load_project_config
from dgteam.integrations.wechat_official.crypto import WechatOfficialCryptoError
from dgteam.integrations.wechat_official.service import WechatOfficialService


LOGGER = logging.getLogger("dgteam.wechat_official.app")


def build_app() -> FastAPI:
    settings = load_settings()
    project_config = load_project_config()
    service = WechatOfficialService(
        config=settings.wechat_official,
        db_path=project_config.paths.db_path,
    )
    app = FastAPI(title="DGTEAM WeChat Official Account Bridge", version="0.1.0")
    callback_path = settings.wechat_official.callback_path or "/wechat/official/callback"

    @app.get("/health")
    def health() -> dict[str, object]:
        return service.health_payload()

    @app.get(callback_path)
    def verify_callback(
        signature: str = Query(default=""),
        msg_signature: str = Query(default=""),
        timestamp: str = Query(default=""),
        nonce: str = Query(default=""),
        echostr: str = Query(default=""),
    ) -> PlainTextResponse:
        resolved_signature = msg_signature or signature
        LOGGER.info(
            "wechat official verify request path=%s timestamp=%s nonce=%s has_signature=%s has_msg_signature=%s has_echostr=%s",
            callback_path,
            timestamp,
            nonce,
            bool(signature),
            bool(msg_signature),
            bool(echostr),
        )
        try:
            plain = service.verify_callback_url(
                msg_signature=resolved_signature,
                timestamp=timestamp,
                nonce=nonce,
                echostr=echostr,
            )
        except WechatOfficialCryptoError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return PlainTextResponse(plain, media_type="text/plain; charset=utf-8")

    @app.post(callback_path)
    async def callback(
        request: Request,
        signature: str = Query(default=""),
        msg_signature: str = Query(default=""),
        timestamp: str = Query(default=""),
        nonce: str = Query(default=""),
    ) -> Response:
        resolved_signature = msg_signature or signature
        body_bytes = await request.body()
        body = body_bytes.decode("utf-8", errors="replace")
        LOGGER.info(
            "wechat official callback post path=%s timestamp=%s nonce=%s has_signature=%s has_msg_signature=%s body_length=%s",
            callback_path,
            timestamp,
            nonce,
            bool(signature),
            bool(msg_signature),
            len(body),
        )
        try:
            result = service.handle_callback(
                raw_body=body,
                msg_signature=resolved_signature,
                timestamp=timestamp,
                nonce=nonce,
            )
        except WechatOfficialCryptoError as exc:
            LOGGER.exception("wechat official callback failed: %s", exc)
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        response_xml = str(result.get("response_xml") or "success")
        if response_xml == "success":
            return PlainTextResponse("success", media_type="text/plain; charset=utf-8")
        return Response(content=response_xml, media_type="application/xml; charset=utf-8")

    @app.exception_handler(HTTPException)
    async def _http_error_handler(_: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"ok": False, "error": exc.detail})

    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DGTEAM WeChat Official Account bridge")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    return parser.parse_args()


def main() -> None:
    configure_utf8_stdio()
    args = parse_args()
    settings = load_settings()
    setup_logging(settings.log_level)
    ensure_runtime_dirs(settings)
    host = args.host or settings.wechat_official.host
    port = args.port or settings.wechat_official.port
    uvicorn.run(build_app(), host=host, port=port)


if __name__ == "__main__":
    main()
