from __future__ import annotations

import json
import mimetypes
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from dgteam.core.config import WechatOfficialConfig
from dgteam.core.textio import read_json_utf8, write_json_utf8


class WechatOfficialApiError(RuntimeError):
    pass


class WechatOfficialClient:
    def __init__(
        self,
        *,
        config: WechatOfficialConfig,
        session: requests.Session | None = None,
    ):
        self.config = config
        self.session = session or requests.Session()
        self.config.state_dir.mkdir(parents=True, exist_ok=True)
        self._token_cache_path = self.config.state_dir / "access_token.json"

    @property
    def token_cache_path(self) -> Path:
        return self._token_cache_path

    def _require_credentials(self) -> None:
        if not self.config.app_id.strip():
            raise WechatOfficialApiError("DGTEAM_WECHAT_OFFICIAL_APP_ID is not configured.")
        if not self.config.app_secret.strip():
            raise WechatOfficialApiError("DGTEAM_WECHAT_OFFICIAL_APP_SECRET is not configured.")

    def _load_cached_token(self) -> dict[str, Any] | None:
        if not self._token_cache_path.exists():
            return None
        try:
            payload = read_json_utf8(self._token_cache_path)
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        token = str(payload.get("access_token") or "").strip()
        expires_at = int(payload.get("expires_at") or 0)
        if not token or expires_at <= 0:
            return None
        return {
            "access_token": token,
            "expires_at": expires_at,
        }

    def _save_cached_token(self, *, access_token: str, expires_in: int) -> dict[str, Any]:
        now = int(time.time())
        payload = {
            "access_token": str(access_token or "").strip(),
            "expires_in": int(expires_in or 0),
            "fetched_at": now,
            "expires_at": now + max(0, int(expires_in or 0)) - 120,
        }
        write_json_utf8(self._token_cache_path, payload)
        return payload

    def get_access_token(self, *, force_refresh: bool = False) -> str:
        self._require_credentials()
        if not force_refresh:
            cached = self._load_cached_token()
            if cached and int(cached["expires_at"]) > int(time.time()):
                return str(cached["access_token"])

        response = self.session.get(
            f"{self.config.api_base_url.rstrip('/')}/cgi-bin/token",
            params={
                "grant_type": "client_credential",
                "appid": self.config.app_id,
                "secret": self.config.app_secret,
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        if int(payload.get("errcode") or 0) != 0:
            raise WechatOfficialApiError(
                f"Failed to fetch access_token: {payload.get('errmsg') or payload}"
            )
        access_token = str(payload.get("access_token") or "").strip()
        expires_in = int(payload.get("expires_in") or 0)
        if not access_token:
            raise WechatOfficialApiError("Official account token response did not include access_token.")
        self._save_cached_token(access_token=access_token, expires_in=expires_in)
        return access_token

    def _api_post(self, path: str, *, payload: dict[str, Any]) -> dict[str, Any]:
        token = self.get_access_token()
        response = self.session.post(
            f"{self.config.api_base_url.rstrip('/')}{path}",
            params={"access_token": token},
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            timeout=20,
        )
        response.raise_for_status()
        result = response.json()
        if int(result.get("errcode") or 0) != 0:
            raise WechatOfficialApiError(f"Official account API error: {result.get('errmsg') or result}")
        return result

    def _api_get(self, path: str) -> dict[str, Any]:
        token = self.get_access_token()
        response = self.session.get(
            f"{self.config.api_base_url.rstrip('/')}{path}",
            params={"access_token": token},
            timeout=20,
        )
        response.raise_for_status()
        result = response.json()
        if int(result.get("errcode") or 0) != 0:
            raise WechatOfficialApiError(f"Official account API error: {result.get('errmsg') or result}")
        return result

    def create_menu(self, menu_payload: dict[str, Any]) -> dict[str, Any]:
        return self._api_post("/cgi-bin/menu/create", payload=menu_payload)

    def get_current_menu(self) -> dict[str, Any]:
        return self._api_get("/cgi-bin/get_current_selfmenu_info")

    def delete_menu(self) -> dict[str, Any]:
        token = self.get_access_token()
        response = self.session.get(
            f"{self.config.api_base_url.rstrip('/')}/cgi-bin/menu/delete",
            params={"access_token": token},
            timeout=20,
        )
        response.raise_for_status()
        result = response.json()
        if int(result.get("errcode") or 0) != 0:
            raise WechatOfficialApiError(f"Official account API error: {result.get('errmsg') or result}")
        return result

    def send_custom_text(self, *, open_id: str, content: str) -> dict[str, Any]:
        return self._api_post(
            "/cgi-bin/message/custom/send",
            payload={
                "touser": str(open_id or "").strip(),
                "msgtype": "text",
                "text": {"content": str(content or "")},
            },
        )

    def download_media(self, media_id: str) -> tuple[bytes, str, str]:
        token = self.get_access_token()
        response = self.session.get(
            f"{self.config.api_base_url.rstrip('/')}/cgi-bin/media/get",
            params={"access_token": token, "media_id": str(media_id or "").strip()},
            timeout=30,
        )
        response.raise_for_status()
        content_type = str(response.headers.get("Content-Type") or "").strip()
        if "application/json" in content_type.lower():
            payload = response.json()
            if int(payload.get("errcode") or 0) != 0:
                raise WechatOfficialApiError(f"Official account media API error: {payload.get('errmsg') or payload}")
        filename = self._filename_from_response(response, default_stem=str(media_id or "media"))
        return response.content, filename, content_type

    def download_image_url(self, url: str) -> tuple[bytes, str, str]:
        response = self.session.get(str(url or "").strip(), timeout=30)
        response.raise_for_status()
        content_type = str(response.headers.get("Content-Type") or "").strip()
        filename = self._filename_from_response(response, default_stem="wechat_image")
        if "." not in filename:
            extension = mimetypes.guess_extension(content_type.split(";")[0].strip()) or ".jpg"
            filename = f"{filename}{extension}"
        return response.content, filename, content_type

    @staticmethod
    def _filename_from_response(response: requests.Response, *, default_stem: str) -> str:
        disposition = str(response.headers.get("Content-Disposition") or "")
        if "filename=" in disposition:
            candidate = disposition.split("filename=", 1)[1].strip().strip('"').strip("'")
            if candidate:
                return Path(candidate).name
        parsed = urlparse(str(response.url))
        name = Path(parsed.path).name
        if name:
            return name
        return default_stem
