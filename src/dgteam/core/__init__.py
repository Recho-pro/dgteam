"""Shared core utilities for DGTEAM."""

from .config import Settings, load_settings
from .encoding_guard import assert_project_encoding_clean, scan_project_tree
from .project_config import ProjectConfig, build_auth_error_message, load_auth_config, load_project_config
from .storage import DGTeamStorage
from .textio import read_json_utf8, read_text_utf8, write_json_utf8, write_text_utf8

__all__ = [
    "DGTeamStorage",
    "ProjectConfig",
    "Settings",
    "assert_project_encoding_clean",
    "build_auth_error_message",
    "load_auth_config",
    "load_project_config",
    "load_settings",
    "read_json_utf8",
    "read_text_utf8",
    "scan_project_tree",
    "write_json_utf8",
    "write_text_utf8",
]
