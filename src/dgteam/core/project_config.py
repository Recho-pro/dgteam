from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from .env import load_project_env_values
from .textio import read_json_utf8


DEFAULT_BASE_URL = "http://web.yilaitong.net"
DEFAULT_PROJECT_CONFIG_ENV = "DGTEAM_CONFIG_FILE"
DEFAULT_AUTH_FILE_ENV = "DGTEAM_AUTH_FILE"
DEFAULT_BASE_URL_ENV = "DGTEAM_BASE_URL"
DEFAULT_DB_PATH_ENV = "DGTEAM_DB_PATH"
DEFAULT_RULES_PATH_ENV = "DGTEAM_RULES_PATH"
DEFAULT_BLACKLIST_PATH_ENV = "DGTEAM_BLACKLIST_PATH"
DEFAULT_HISTORY_DAYS_ENV = "DGTEAM_HISTORY_DAYS"
DEFAULT_DELAY_SECONDS_ENV = "DGTEAM_DELAY_SECONDS"
DEFAULT_PROCESS_WORKERS_ENV = "DGTEAM_PROCESS_WORKERS"
DEFAULT_FIXED_CITY_ENV = "DGTEAM_FIXED_CITY"
DEFAULT_MAX_LOGIN_RETRIES_ENV = "DGTEAM_MAX_LOGIN_RETRIES"


def _clean_str(value: Any) -> str:
    return str(value or "").strip()


def _env_value(primary: str, env_values: Dict[str, str]) -> str:
    value = _clean_str(os.environ.get(primary))
    if value:
        return value
    return _clean_str(env_values.get(primary))


def _read_json_file(path: Path, *, label: str) -> Dict[str, Any]:
    try:
        payload = read_json_utf8(path)
    except FileNotFoundError as exc:
        raise ValueError(f"{label} does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must contain a JSON object: {path}")
    return payload


def _load_auth_payload_from_file(config_file: Path) -> Dict[str, Any]:
    payload = _read_json_file(config_file, label="Auth config file")
    auth_payload = payload.get("auth")
    if isinstance(auth_payload, dict):
        return auth_payload
    return payload


def _coerce_int(value: Any, fallback: int) -> int:
    cleaned = _clean_str(value)
    if not cleaned:
        return int(fallback)
    try:
        return int(float(cleaned))
    except (TypeError, ValueError):
        raise ValueError(f"Invalid integer value: {value!r}")


def _coerce_float(value: Any, fallback: float) -> float:
    cleaned = _clean_str(value)
    if not cleaned:
        return float(fallback)
    try:
        return float(cleaned)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid float value: {value!r}")


def _resolve_path_value(value: Any, *, base_dir: Path) -> Path:
    raw = _clean_str(value)
    if not raw:
        raise ValueError("Path value cannot be empty.")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def _optional_resolve_path_value(value: Any, *, base_dir: Path) -> Optional[Path]:
    raw = _clean_str(value)
    if not raw:
        return None
    return _resolve_path_value(raw, base_dir=base_dir)


def _merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(dict(merged[key]), value)
        else:
            merged[key] = value
    return merged


def build_auth_error_message(*, include_session_hint: bool = False) -> str:
    message = (
        "Missing DGTEAM auto-login credentials. "
        "Provide both --login-username and --login-password, or set "
        "DGTEAM_LOGIN_USERNAME and DGTEAM_LOGIN_PASSWORD, or set "
        f"{DEFAULT_AUTH_FILE_ENV} or {DEFAULT_PROJECT_CONFIG_ENV} to a JSON file containing "
        '{"auth":{"username":"...","password":"..."}}.'
    )
    if include_session_hint:
        message += " You can also sign in once in the browser profile and rerun."
    return message


@dataclass(slots=True)
class AuthConfig:
    username: str = ""
    password: str = ""
    max_login_retries: int = 3
    source: str = ""

    @property
    def has_credentials(self) -> bool:
        return bool(self.username and self.password)


@dataclass(slots=True)
class RuntimeConfig:
    history_days: int = 3
    delay_seconds: float = 0.2
    process_workers: int = 4
    fixed_city: str = "\u5168\u56fd"


@dataclass(slots=True)
class PathsConfig:
    project_root: Path
    db_path: Path
    rules_path: Path
    blacklist_path: Optional[Path]


@dataclass(slots=True)
class ProjectConfig:
    base_url: str
    auth: AuthConfig
    runtime: RuntimeConfig
    paths: PathsConfig

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["auth"]["password"] = "***" if self.auth.password else ""
        payload["paths"]["project_root"] = str(self.paths.project_root)
        payload["paths"]["db_path"] = str(self.paths.db_path)
        payload["paths"]["rules_path"] = str(self.paths.rules_path)
        payload["paths"]["blacklist_path"] = str(self.paths.blacklist_path) if self.paths.blacklist_path else None
        return payload


def _load_config_payload(
    config_file: Optional[Path],
    *,
    project_root: Path,
    env_values: Dict[str, str],
) -> tuple[Dict[str, Any], Optional[Path]]:
    candidate = config_file
    if candidate is None:
        raw_env_file = _env_value(DEFAULT_PROJECT_CONFIG_ENV, env_values)
        if raw_env_file:
            candidate = _resolve_path_value(raw_env_file, base_dir=project_root)
    if candidate is None:
        return {}, None
    return _read_json_file(candidate, label="Project config file"), candidate


def _load_env_overrides(*, project_root: Path, env_values: Dict[str, str]) -> Dict[str, Any]:
    overrides: Dict[str, Any] = {}

    base_url = _env_value(DEFAULT_BASE_URL_ENV, env_values)
    if base_url:
        overrides["base_url"] = base_url

    auth_retries = _env_value(DEFAULT_MAX_LOGIN_RETRIES_ENV, env_values)
    if auth_retries:
        overrides.setdefault("auth", {})["max_login_retries"] = _coerce_int(auth_retries, 3)

    history_days = _env_value(DEFAULT_HISTORY_DAYS_ENV, env_values)
    if history_days:
        overrides.setdefault("runtime", {})["history_days"] = _coerce_int(history_days, 3)

    delay_seconds = _env_value(DEFAULT_DELAY_SECONDS_ENV, env_values)
    if delay_seconds:
        overrides.setdefault("runtime", {})["delay_seconds"] = _coerce_float(delay_seconds, 0.2)

    process_workers = _env_value(DEFAULT_PROCESS_WORKERS_ENV, env_values)
    if process_workers:
        overrides.setdefault("runtime", {})["process_workers"] = _coerce_int(process_workers, 4)

    fixed_city = _env_value(DEFAULT_FIXED_CITY_ENV, env_values)
    if fixed_city:
        overrides.setdefault("runtime", {})["fixed_city"] = fixed_city

    db_path = _env_value(DEFAULT_DB_PATH_ENV, env_values)
    if db_path:
        overrides.setdefault("paths", {})["db_path"] = str(_resolve_path_value(db_path, base_dir=project_root))

    rules_path = _env_value(DEFAULT_RULES_PATH_ENV, env_values)
    if rules_path:
        overrides.setdefault("paths", {})["rules_path"] = str(_resolve_path_value(rules_path, base_dir=project_root))

    blacklist_path = _env_value(DEFAULT_BLACKLIST_PATH_ENV, env_values)
    if blacklist_path:
        overrides.setdefault("paths", {})["blacklist_path"] = str(
            _resolve_path_value(blacklist_path, base_dir=project_root)
        )

    return overrides


def load_auth_config(
    *,
    username: str = "",
    password: str = "",
    config_file: Optional[Path] = None,
    max_login_retries: int = 3,
    env_values: Optional[Dict[str, str]] = None,
) -> AuthConfig:
    resolved_env_values = dict(env_values or {})
    cli_username = _clean_str(username)
    cli_password = _clean_str(password)
    if cli_username or cli_password:
        if not cli_username or not cli_password:
            raise ValueError("Both login username and password must be provided together.")
        return AuthConfig(
            username=cli_username,
            password=cli_password,
            max_login_retries=max_login_retries,
            source="cli",
        )

    file_candidate = config_file
    if file_candidate is None:
        raw_project_file = _env_value(DEFAULT_PROJECT_CONFIG_ENV, resolved_env_values)
        if raw_project_file:
            file_candidate = Path(raw_project_file).expanduser().resolve()

    if file_candidate:
        auth_payload = _load_auth_payload_from_file(file_candidate)
        file_username = _clean_str(auth_payload.get("username"))
        file_password = _clean_str(auth_payload.get("password"))
        file_max_retries = _coerce_int(auth_payload.get("max_login_retries"), max_login_retries)
        if file_username and file_password:
            return AuthConfig(
                username=file_username,
                password=file_password,
                max_login_retries=file_max_retries,
                source=f"file:{file_candidate}",
            )

    raw_env_file = _env_value(DEFAULT_AUTH_FILE_ENV, resolved_env_values)
    if raw_env_file:
        auth_file_candidate = Path(raw_env_file).expanduser().resolve()
        auth_payload = _load_auth_payload_from_file(auth_file_candidate)
        file_username = _clean_str(auth_payload.get("username"))
        file_password = _clean_str(auth_payload.get("password"))
        file_max_retries = _coerce_int(auth_payload.get("max_login_retries"), max_login_retries)
        if file_username and file_password:
            return AuthConfig(
                username=file_username,
                password=file_password,
                max_login_retries=file_max_retries,
                source=f"file:{auth_file_candidate}",
            )

    env_username = _env_value("DGTEAM_LOGIN_USERNAME", resolved_env_values)
    env_password = _env_value("DGTEAM_LOGIN_PASSWORD", resolved_env_values)
    if env_username or env_password:
        if not env_username or not env_password:
            raise ValueError(
                "DGTEAM_LOGIN_USERNAME and DGTEAM_LOGIN_PASSWORD must both be set when using environment credentials."
            )
        env_max_retries = _coerce_int(
            _env_value(DEFAULT_MAX_LOGIN_RETRIES_ENV, resolved_env_values),
            max_login_retries,
        )
        return AuthConfig(
            username=env_username,
            password=env_password,
            max_login_retries=env_max_retries,
            source="env",
        )

    return AuthConfig(max_login_retries=max_login_retries, source="")


def load_project_config(project_root: Optional[Path] = None, config_file: Optional[Path] = None) -> ProjectConfig:
    root = (project_root or Path(__file__).resolve().parents[3]).resolve()
    env_values = load_project_env_values(root)
    defaults: Dict[str, Any] = {
        "base_url": DEFAULT_BASE_URL,
        "auth": {
            "username": "",
            "password": "",
            "max_login_retries": 3,
            "source": "",
        },
        "runtime": {
            "history_days": 3,
            "delay_seconds": 0.2,
            "process_workers": 4,
            "fixed_city": "\u5168\u56fd",
        },
        "paths": {
            "db_path": str(root / "runtime" / "local" / "data" / "dgteam.db"),
            "rules_path": str(root / "rules" / "default_rules.json"),
            "blacklist_path": None,
        },
    }

    file_payload, resolved_config_file = _load_config_payload(config_file, project_root=root, env_values=env_values)
    merged = _merge(defaults, file_payload)
    merged = _merge(merged, _load_env_overrides(project_root=root, env_values=env_values))

    auth_config = load_auth_config(
        config_file=resolved_config_file,
        max_login_retries=_coerce_int(merged["auth"].get("max_login_retries"), 3),
        env_values=env_values,
    )
    auth_max_login_retries = _coerce_int(merged["auth"].get("max_login_retries"), auth_config.max_login_retries)

    merged_auth = dict(merged.get("auth") or {})
    merged_auth["username"] = auth_config.username
    merged_auth["password"] = auth_config.password
    merged_auth["max_login_retries"] = auth_max_login_retries
    merged_auth["source"] = auth_config.source

    paths = dict(merged["paths"])
    path_base_dir = resolved_config_file.parent if resolved_config_file else root
    db_path = _resolve_path_value(paths["db_path"], base_dir=path_base_dir if resolved_config_file else root)
    rules_path = _resolve_path_value(paths["rules_path"], base_dir=path_base_dir if resolved_config_file else root)
    blacklist_path = _optional_resolve_path_value(paths.get("blacklist_path"), base_dir=path_base_dir if resolved_config_file else root)

    return ProjectConfig(
        base_url=str(merged["base_url"]),
        auth=AuthConfig(**merged_auth),
        runtime=RuntimeConfig(
            history_days=_coerce_int(merged["runtime"].get("history_days"), 3),
            delay_seconds=_coerce_float(merged["runtime"].get("delay_seconds"), 0.2),
            process_workers=_coerce_int(merged["runtime"].get("process_workers"), 4),
            fixed_city=str(merged["runtime"].get("fixed_city") or "\u5168\u56fd"),
        ),
        paths=PathsConfig(
            project_root=root,
            db_path=db_path,
            rules_path=rules_path,
            blacklist_path=blacklist_path,
        ),
    )
