from __future__ import annotations

import io
import json
import threading
import time
import urllib.error
import urllib.request
import zipfile
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Iterator

import pytest

from dgteam.core.config import load_settings
from dgteam.publish_api.app import PublishApiHandler
from dgteam.publish_api.release_store import ReleaseStore


def _build_store(tmp_path: Path) -> tuple[ReleaseStore, object]:
    settings = load_settings(project_root=tmp_path)
    store = ReleaseStore(
        tmp_path / "cloud",
        current_dir=settings.release.current_dir,
        previous_dir=settings.release.previous_dir,
        history_dir=settings.release.history_dir,
        staging_dir=settings.release.staging_dir,
        state_dir=settings.release.state_dir,
        uploads_dir=settings.publish_api.uploads_dir,
    )
    return store, settings


@contextmanager
def running_publish_api(tmp_path: Path) -> Iterator[str]:
    store, settings = _build_store(tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), PublishApiHandler)
    server.settings = settings  # type: ignore[attr-defined]
    server.release_store = store  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        time.sleep(0.1)


def _request(
    *,
    url: str,
    method: str = "GET",
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, object]]:
    request = urllib.request.Request(url=url, method=method, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _unsafe_archive_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("../escape.txt", "bad")
    return buffer.getvalue()


def test_publish_api_rejects_missing_auth_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DGTEAM_PUBLISH_TOKEN", "secret-token")
    with running_publish_api(tmp_path) as base_url:
        status, payload = _request(
            url=f"{base_url}/api/releases/rollback",
            method="POST",
            data=b"{}",
            headers={"Content-Type": "application/json"},
        )

    assert status == 401
    assert payload == {"ok": False, "error": "Unauthorized"}


def test_publish_api_activate_requires_release_id(tmp_path: Path) -> None:
    with running_publish_api(tmp_path) as base_url:
        status, payload = _request(
            url=f"{base_url}/api/releases/activate",
            method="POST",
            data=b"{}",
            headers={"Content-Type": "application/json"},
        )

    assert status == 400
    assert payload["ok"] is False
    assert "ValueError: release_id is required." == payload["error"]


def test_publish_api_rollback_requires_previous_release(tmp_path: Path) -> None:
    with running_publish_api(tmp_path) as base_url:
        status, payload = _request(
            url=f"{base_url}/api/releases/rollback",
            method="POST",
            data=b"{}",
            headers={"Content-Type": "application/json"},
        )

    assert status == 404
    assert payload["ok"] is False
    assert "No previous release is available for rollback." in str(payload["error"])


def test_publish_api_deploy_rejects_empty_archive(tmp_path: Path) -> None:
    with running_publish_api(tmp_path) as base_url:
        status, payload = _request(
            url=f"{base_url}/api/releases/deploy",
            method="POST",
            data=b"",
            headers={"Content-Type": "application/zip"},
        )

    assert status == 400
    assert payload["ok"] is False
    assert payload["error"] == "ValueError: Release archive payload is empty."


def test_publish_api_upload_rejects_checksum_mismatch(tmp_path: Path) -> None:
    with running_publish_api(tmp_path) as base_url:
        status, payload = _request(
            url=f"{base_url}/api/releases/upload",
            method="POST",
            data=b"not-a-real-zip",
            headers={
                "Content-Type": "application/zip",
                "X-Checksum-Sha256": "deadbeef",
            },
        )

    assert status == 400
    assert payload["ok"] is False
    assert payload["error"] == "ValueError: SHA256 mismatch for uploaded release archive."


def test_publish_api_upload_rejects_unsafe_archive_members(tmp_path: Path) -> None:
    with running_publish_api(tmp_path) as base_url:
        status, payload = _request(
            url=f"{base_url}/api/releases/upload",
            method="POST",
            data=_unsafe_archive_bytes(),
            headers={"Content-Type": "application/zip"},
        )

    assert status == 400
    assert payload["ok"] is False
    assert "Unsafe archive member path" in str(payload["error"])


def test_publish_api_get_contracts_require_identifiers(tmp_path: Path) -> None:
    with running_publish_api(tmp_path) as base_url:
        validate_status, validate_payload = _request(url=f"{base_url}/api/releases/validate")
        deployment_status, deployment_payload = _request(url=f"{base_url}/api/deployments/status")

    assert validate_status == 400
    assert validate_payload == {"ok": False, "error": "release_id is required."}
    assert deployment_status == 400
    assert deployment_payload == {"ok": False, "error": "deployment_id is required."}
