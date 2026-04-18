from __future__ import annotations

from pathlib import Path
from typing import Dict

try:
    from dotenv import dotenv_values as _dotenv_values
except ModuleNotFoundError:
    def _dotenv_values(path: str | Path) -> Dict[str, str]:
        resolved = Path(path).expanduser().resolve()
        values: Dict[str, str] = {}
        if not resolved.exists():
            return values
        for raw_line in resolved.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            if "=" not in line:
                continue
            key, raw_value = line.split("=", 1)
            key = str(key or "").strip()
            if not key:
                continue
            value = str(raw_value or "").strip()
            if value and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            values[key] = value
        return values


_LOADED_ENV_FILES: set[Path] = set()
_ENV_VALUE_CACHE: dict[Path, Dict[str, str]] = {}


def _project_env_path(project_root: Path) -> Path:
    return Path(project_root).resolve() / ".env"


def load_project_env_values(project_root: Path) -> Dict[str, str]:
    env_path = _project_env_path(project_root)
    cached = _ENV_VALUE_CACHE.get(env_path)
    if cached is not None:
        return dict(cached)
    if not env_path.exists():
        _ENV_VALUE_CACHE[env_path] = {}
        return {}
    raw_values = _dotenv_values(env_path)
    normalized: Dict[str, str] = {}
    for key, value in raw_values.items():
        if key is None or value is None:
            continue
        normalized[str(key)] = str(value).strip()
    _ENV_VALUE_CACHE[env_path] = dict(normalized)
    return normalized


def ensure_project_env_loaded(project_root: Path) -> Path:
    env_path = _project_env_path(project_root)
    if env_path in _LOADED_ENV_FILES:
        return env_path
    load_project_env_values(project_root)
    _LOADED_ENV_FILES.add(env_path)
    return env_path
