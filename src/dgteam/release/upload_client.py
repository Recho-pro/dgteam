from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict


def _open_json(request: urllib.request.Request) -> Dict[str, Any]:
    try:
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Upload request failed with HTTP {exc.code}: {body}") from exc


def _post_json(
    *,
    url: str,
    payload: Dict[str, Any],
    token: str = "",
) -> Dict[str, Any]:
    request = urllib.request.Request(
        url=url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            **({"X-DGTEAM-Token": token} if token else {}),
        },
    )
    return _open_json(request)


def upload_release_bundle(
    *,
    server_url: str,
    archive_path: Path,
    token: str = "",
    release_id: str = "",
    activate: bool = True,
) -> Dict[str, Any]:
    target = Path(archive_path).expanduser().resolve()
    payload = target.read_bytes()
    checksum = hashlib.sha256(payload).hexdigest()
    request = urllib.request.Request(
        url=f"{server_url.rstrip('/')}/api/releases/upload",
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/zip",
            "X-Checksum-Sha256": checksum,
            "X-Activate": "true" if activate else "false",
            **({"X-Release-Id": release_id} if release_id else {}),
            **({"X-DGTEAM-Token": token} if token else {}),
        },
    )
    return _open_json(request)


def deploy_release_bundle(
    *,
    server_url: str,
    archive_path: Path,
    token: str = "",
    release_id: str = "",
    deployment_id: str = "",
) -> Dict[str, Any]:
    target = Path(archive_path).expanduser().resolve()
    payload = target.read_bytes()
    checksum = hashlib.sha256(payload).hexdigest()
    request = urllib.request.Request(
        url=f"{server_url.rstrip('/')}/api/releases/deploy",
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/zip",
            "X-Checksum-Sha256": checksum,
            **({"X-Release-Id": release_id} if release_id else {}),
            **({"X-Deployment-Id": deployment_id} if deployment_id else {}),
            **({"X-DGTEAM-Token": token} if token else {}),
        },
    )
    return _open_json(request)


def rollback_release(*, server_url: str, token: str = "") -> Dict[str, Any]:
    return _post_json(
        url=f"{server_url.rstrip('/')}/api/releases/rollback",
        payload={},
        token=token,
    )


def activate_release(*, server_url: str, release_id: str, token: str = "") -> Dict[str, Any]:
    return _post_json(
        url=f"{server_url.rstrip('/')}/api/releases/activate",
        payload={"release_id": str(release_id or "").strip()},
        token=token,
    )
