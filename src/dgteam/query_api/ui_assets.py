from __future__ import annotations

import shutil
from typing import Any
from hashlib import sha256
from pathlib import Path

from dgteam.core.textio import read_json_utf8, write_json_utf8
from dgteam.query_api.static_assets import WEB_DIR


RELEASE_UI_DIRNAME = "query_ui"
UI_ASSET_MANIFEST_NAME = "asset-manifest.json"
UI_TEMPLATE_FILE = "index.html"
VERSIONED_ASSET_FILENAMES = (
    "app.js",
    "styles.css",
)
REQUIRED_UI_ASSET_FILES = (
    UI_TEMPLATE_FILE,
    *VERSIONED_ASSET_FILENAMES,
    UI_ASSET_MANIFEST_NAME,
)


def _asset_dir(asset_dir: Path | None = None) -> Path:
    return Path(asset_dir or WEB_DIR).expanduser().resolve()


def _asset_manifest_path(asset_dir: Path | None = None) -> Path:
    return _asset_dir(asset_dir) / UI_ASSET_MANIFEST_NAME


def _hash_files(paths: tuple[Path, ...]) -> str:
    hasher = sha256()
    for path in paths:
        data = path.read_bytes()
        hasher.update(path.name.encode("utf-8"))
        hasher.update(len(data).to_bytes(8, "big"))
        hasher.update(data)
    return hasher.hexdigest()[:12]


def read_ui_asset_manifest(asset_dir: Path | None = None) -> dict[str, Any]:
    manifest_path = _asset_manifest_path(asset_dir)
    if not manifest_path.is_file():
        return {}
    try:
        payload = read_json_utf8(manifest_path)
    except Exception:
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


def build_ui_asset_version(asset_dir: Path | None = None) -> str:
    manifest = read_ui_asset_manifest(asset_dir)
    manifest_version = str(manifest.get("version") or "").strip()
    if manifest_version:
        return manifest_version

    root = _asset_dir(asset_dir)
    return _hash_files(
        (
            root / UI_TEMPLATE_FILE,
            *(root / filename for filename in VERSIONED_ASSET_FILENAMES),
        )
    )


def versioned_asset_href(filename: str, *, version: str | None = None, asset_dir: Path | None = None) -> str:
    asset_version = str(version or build_ui_asset_version(asset_dir)).strip()
    return f"./{Path(filename).name}?v={asset_version}"


def render_index_html(asset_dir: Path | None = None) -> str:
    root = _asset_dir(asset_dir)
    version = build_ui_asset_version(root)
    template = (root / UI_TEMPLATE_FILE).read_text(encoding="utf-8")
    return (
        template.replace("__DGTEAM_ASSET_VERSION__", version)
        .replace("__DGTEAM_STYLES_HREF__", versioned_asset_href("styles.css", version=version, asset_dir=root))
        .replace("__DGTEAM_APP_HREF__", versioned_asset_href("app.js", version=version, asset_dir=root))
    )


def resolve_release_ui_asset_dir(db_path: Path | None = None) -> Path:
    """Prefer release-scoped UI assets next to dgteam.db, with source assets as dev fallback."""
    if db_path is not None:
        release_asset_dir = Path(db_path).expanduser().resolve().parent / RELEASE_UI_DIRNAME
        if (release_asset_dir / UI_TEMPLATE_FILE).is_file():
            return release_asset_dir
    return WEB_DIR


def package_query_ui_assets(target_dir: Path, *, source_dir: Path | None = None) -> dict[str, Any]:
    source = _asset_dir(source_dir)
    target = Path(target_dir).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)

    copied_files: list[dict[str, Any]] = []
    for filename in (UI_TEMPLATE_FILE, *VERSIONED_ASSET_FILENAMES):
        source_path = source / filename
        if not source_path.is_file():
            raise FileNotFoundError(f"Query UI asset is missing: {source_path}")
        target_path = target / filename
        shutil.copy2(source_path, target_path)
        data = target_path.read_bytes()
        copied_files.append(
            {
                "path": filename,
                "size": int(len(data)),
                "sha256": sha256(data).hexdigest(),
                "cache_policy": "no-store" if filename == UI_TEMPLATE_FILE else "immutable",
            }
        )

    version = _hash_files(
        (
            target / UI_TEMPLATE_FILE,
            *(target / filename for filename in VERSIONED_ASSET_FILENAMES),
        )
    )
    manifest = {
        "contract_version": "dgteam-query-ui-assets.v1",
        "version": version,
        "source_dir": str(source),
        "files": copied_files,
        "entrypoint": UI_TEMPLATE_FILE,
        "versioned_assets": list(VERSIONED_ASSET_FILENAMES),
        "release_lifecycle": "current_previous_rollback",
    }
    write_json_utf8(target / UI_ASSET_MANIFEST_NAME, manifest)
    return manifest
