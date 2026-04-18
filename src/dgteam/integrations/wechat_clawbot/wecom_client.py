from __future__ import annotations

import logging
import time
import uuid
from typing import Any

import requests

from dgteam.integrations.wechat_clawbot.storage import ClawbotStateStore

LOGGER = logging.getLogger("dgteam.wecom_bridge.client")


class WecomApiError(Exception):
    pass


class WecomCustomerServiceClient:
    def __init__(
        self,
        *,
        corp_id: str,
        corp_secret: str,
        api_base_url: str,
        state_store: ClawbotStateStore,
        session: requests.Session | None = None,
    ):
        self.corp_id = str(corp_id or "").strip()
        self.corp_secret = str(corp_secret or "").strip()
        self.api_base_url = str(api_base_url or "https://qyapi.weixin.qq.com").rstrip("/")
        self.state_store = state_store
        self.session = session or requests.Session()

    @property
    def configured(self) -> bool:
        return bool(self.corp_id and self.corp_secret)

    def _token_cache(self) -> dict[str, Any]:
        payload = self.state_store.load_access_token_cache()
        return payload if isinstance(payload, dict) else {}

    def get_access_token(self, *, force_refresh: bool = False) -> str:
        if not self.configured:
            raise WecomApiError("Missing WeCom corp_id or corp_secret.")

        if not force_refresh:
            cached = self._token_cache()
            token = str(cached.get("access_token") or "").strip()
            expires_at = float(cached.get("expires_at") or 0)
            if token and expires_at > time.time() + 30:
                LOGGER.info("wecom access_token cache hit expires_at=%s", int(expires_at))
                return token

        response = self.session.get(
            f"{self.api_base_url}/cgi-bin/gettoken",
            params={
                "corpid": self.corp_id,
                "corpsecret": self.corp_secret,
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        if int(payload.get("errcode", 0) or 0) != 0:
            raise WecomApiError(f"gettoken failed: {payload}")
        access_token = str(payload.get("access_token") or "").strip()
        expires_in = int(payload.get("expires_in") or 7200)
        LOGGER.info("wecom access_token refreshed expires_in=%s", expires_in)
        self.state_store.save_access_token_cache(
            {
                "access_token": access_token,
                "expires_at": time.time() + max(expires_in - 120, 60),
                "updated_at": int(time.time()),
            }
        )
        return access_token

    def _post_json(self, endpoint: str, payload: dict[str, Any], *, force_refresh: bool = False) -> dict[str, Any]:
        access_token = self.get_access_token(force_refresh=force_refresh)
        LOGGER.info("wecom api request endpoint=%s force_refresh=%s", endpoint, force_refresh)
        response = self.session.post(
            f"{self.api_base_url}{endpoint}",
            params={"access_token": access_token},
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        errcode = int(data.get("errcode", 0) or 0)
        if errcode == 40014 and not force_refresh:
            return self._post_json(endpoint, payload, force_refresh=True)
        if errcode != 0:
            raise WecomApiError(f"{endpoint} failed: {data}")
        return data

    def sync_messages(
        self,
        *,
        sync_token: str,
        open_kfid: str,
        cursor: str = "",
        limit: int = 1000,
    ) -> dict[str, Any]:
        return self._post_json(
            "/cgi-bin/kf/sync_msg",
            {
                "token": str(sync_token or "").strip(),
                "open_kfid": str(open_kfid or "").strip(),
                "cursor": str(cursor or "").strip(),
                "limit": int(limit),
                "voice_format": 0,
            },
        )

    def send_text_message(
        self,
        *,
        touser: str,
        open_kfid: str,
        content: str,
    ) -> dict[str, Any]:
        return self._post_json(
            "/cgi-bin/kf/send_msg",
            {
                "touser": str(touser or "").strip(),
                "open_kfid": str(open_kfid or "").strip(),
                "msgid": uuid.uuid4().hex,
                "msgtype": "text",
                "text": {
                    "content": str(content or "").strip(),
                },
            },
        )
