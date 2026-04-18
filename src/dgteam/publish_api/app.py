from __future__ import annotations

import argparse
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict
from urllib.parse import parse_qs, urlparse

from dgteam.core.console_encoding import configure_utf8_stdio
from dgteam.core.config import Settings, load_settings
from dgteam.core.logging import setup_logging
from dgteam.core.paths import ensure_runtime_dirs
from dgteam.publish_api.release_store import ReleaseStore
from dgteam.release.retention import prune_cloud_runtime


LOGGER = logging.getLogger("dgteam.publish_api.app")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DGTEAM publish API service")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    return parser.parse_args()


def _build_release_store(settings: Settings) -> ReleaseStore:
    ensure_runtime_dirs(settings)
    return ReleaseStore(
        settings.cloud_root,
        current_dir=settings.release.current_dir,
        previous_dir=settings.release.previous_dir,
        history_dir=settings.release.history_dir,
        staging_dir=settings.release.staging_dir,
        state_dir=settings.release.state_dir,
        uploads_dir=settings.publish_api.uploads_dir,
    )


class PublishApiHandler(BaseHTTPRequestHandler):
    server_version = "DGTEAMPublishAPI/2.0"

    @property
    def release_store(self) -> ReleaseStore:
        return self.server.release_store  # type: ignore[attr-defined]

    @property
    def settings(self) -> Settings:
        return self.server.settings  # type: ignore[attr-defined]

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def _read_bytes(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return b""
        return self.rfile.read(length)

    def _send_json(self, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_exception(self, exc: Exception) -> None:
        if isinstance(exc, FileNotFoundError):
            status = 404
        elif isinstance(exc, ValueError):
            status = 400
        elif isinstance(exc, PermissionError):
            status = 403
        else:
            status = 500
        self._send_json(status, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    def _require_auth(self) -> bool:
        token = str(self.settings.publish_api.shared_token or "").strip()
        if not token:
            return True
        supplied = str(self.headers.get("X-DGTEAM-Token") or "").strip()
        if supplied == token:
            return True
        self._send_json(401, {"ok": False, "error": "Unauthorized"})
        return False

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return

    def _cloud_retention_summary(self) -> Dict[str, Any]:
        if not self.settings.retention.enabled:
            return {}
        return prune_cloud_runtime(
            history_dir=self.release_store.history_dir,
            uploads_dir=self.release_store.uploads_dir,
            keep_release_dirs=self.settings.retention.keep_cloud_releases,
            keep_rollback_dirs=self.settings.retention.keep_cloud_rollbacks,
            clear_uploads=self.settings.retention.prune_cloud_uploads,
        )

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/health":
            self._send_json(200, {"ok": True, "service": "dgteam-publish-api"})
            return
        if path == "/api/status":
            self._send_json(
                200,
                {
                    "ok": True,
                    "service": "dgteam-publish-api",
                    "upload_token_configured": bool(str(self.settings.publish_api.shared_token or "").strip()),
                    "store": self.release_store.summary(),
                },
            )
            return
        if path == "/api/releases/validate":
            query = parse_qs(parsed.query or "")
            release_id = str((query.get("release_id") or [""])[0] or "").strip()
            if not release_id:
                self._send_json(400, {"ok": False, "error": "release_id is required."})
                return
            target = self.release_store.history_dir / release_id
            self._send_json(200, {"ok": True, "validation": self.release_store.validate_release_dir(target)})
            return
        if path == "/api/deployments/status":
            query = parse_qs(parsed.query or "")
            deployment_id = str((query.get("deployment_id") or [""])[0] or "").strip()
            if not deployment_id:
                self._send_json(400, {"ok": False, "error": "deployment_id is required."})
                return
            journal = self.release_store._journal(deployment_id)
            self._send_json(
                200,
                {
                    "ok": bool(journal.load()),
                    "deployment": journal.load(),
                    "status_files": journal.note_paths(),
                },
            )
            return
        self._send_json(404, {"ok": False, "error": f"Unknown path: {path}"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path != "/health" and not self._require_auth():
            return
        try:
            if path == "/api/releases/deploy":
                archive_bytes = self._read_bytes()
                release_id = str(self.headers.get("X-Release-Id") or "").strip()
                deployment_id = str(self.headers.get("X-Deployment-Id") or "").strip()
                checksum = str(self.headers.get("X-Checksum-Sha256") or "").strip()
                response = self.release_store.deploy_release_archive(
                    archive_bytes,
                    release_id=release_id,
                    deployment_id=deployment_id,
                    expected_sha256=checksum,
                )
                response["ok"] = True
                response["retention"] = self._cloud_retention_summary()
                self._send_json(200, response)
                return
            if path == "/api/releases/upload":
                archive_bytes = self._read_bytes()
                release_id = str(self.headers.get("X-Release-Id") or "").strip()
                checksum = str(self.headers.get("X-Checksum-Sha256") or "").strip()
                activate = str(self.headers.get("X-Activate") or "").strip().lower() in {"1", "true", "yes", "on"}
                imported = self.release_store.import_release_archive(
                    archive_bytes,
                    release_id=release_id,
                    expected_sha256=checksum,
                )
                response: Dict[str, Any] = {"ok": True, "imported": imported}
                if activate:
                    response["activated"] = self.release_store.activate_release(imported["release_id"])
                    response["retention"] = self._cloud_retention_summary()
                self._send_json(200, response)
                return

            payload = self._read_json()
            if path == "/api/releases/import-local":
                source_dir = Path(str(payload.get("source_dir") or "")).expanduser().resolve()
                release_id = str(payload.get("release_id") or "").strip()
                deployment_id = str(payload.get("deployment_id") or "").strip()
                activate = bool(payload.get("activate"))
                if activate:
                    deployed = self.release_store.deploy_local_release(
                        source_dir,
                        release_id=release_id,
                        deployment_id=deployment_id,
                    )
                    response = {"ok": True, **deployed, "retention": self._cloud_retention_summary()}
                else:
                    imported = self.release_store.import_local_release(source_dir, release_id=release_id)
                    response = {"ok": True, "imported": imported}
                self._send_json(200, response)
                return
            if path == "/api/releases/activate":
                release_id = str(payload.get("release_id") or "").strip()
                if not release_id:
                    raise ValueError("release_id is required.")
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "activated": self.release_store.activate_release(release_id),
                        "retention": self._cloud_retention_summary(),
                    },
                )
                return
            if path == "/api/releases/rollback":
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "rolled_back": self.release_store.rollback(),
                        "retention": self._cloud_retention_summary(),
                    },
                )
                return
            self._send_json(404, {"ok": False, "error": f"Unknown path: {path}"})
        except Exception as exc:
            LOGGER.exception("publish api request failed path=%s", path)
            self._send_exception(exc)


def main() -> None:
    configure_utf8_stdio()
    args = parse_args()
    settings = load_settings()
    setup_logging(settings.log_level)
    host = args.host or settings.publish_api.host
    port = args.port or settings.publish_api.port
    server = ThreadingHTTPServer((host, port), PublishApiHandler)
    server.settings = settings  # type: ignore[attr-defined]
    server.release_store = _build_release_store(settings)  # type: ignore[attr-defined]
    LOGGER.info("publish api serving on http://%s:%s", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
