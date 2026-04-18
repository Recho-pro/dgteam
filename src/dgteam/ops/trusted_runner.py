from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any

from dgteam.ops.real_source_db import default_real_source_db


TRUSTED_RUNNER_CONTRACT_VERSION = "trusted-runner-release-gate.v1"
DEFAULT_RUNNER_ROOT = Path("/srv/dgteam/runtime/cloud/github_runner")
DEFAULT_RUNNER_ENV_FILE = Path("/srv/dgteam/.runner.env")
DEFAULT_RUNNER_LABELS = "self-hosted,dgteam-trusted,linux,x64"
DEFAULT_RUNNER_GROUP = "Default"


def read_runner_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    payload: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        payload[key.strip()] = value.strip()
    return payload


def _venv_python_candidates(project_root: Path) -> list[Path]:
    return [
        project_root / ".venv" / "bin" / "python",
        project_root / ".venv" / "Scripts" / "python.exe",
    ]


def _workflow_checks(workflow_text: str) -> dict[str, bool]:
    return {
        "workflow_dispatch_present": "workflow_dispatch:" in workflow_text,
        "schedule_present": "schedule:" in workflow_text,
        "fixture_gate_command_present": "candidate-gate-fixture" in workflow_text,
        "real_source_gate_command_present": "candidate-gate-real-source" in workflow_text,
        "preflight_command_present": "trusted_runner_preflight.py" in workflow_text,
        "registration_dry_run_present": "trusted_runner_register.py" in workflow_text,
        "self_hosted_label_present": "self-hosted" in workflow_text,
        "trusted_label_present": "dgteam-trusted" in workflow_text,
        "gate_result_artifact_present": "gate_result.json" in workflow_text,
    }


def _source_db_checks(source_db: Path | None) -> dict[str, Any]:
    if source_db is None:
        return {
            "source_db_exists": False,
            "source_db_has_runs": False,
            "source_db_has_market_snapshots": False,
            "source_db_has_completed_runs": False,
            "source_db_error": "",
        }

    if not source_db.exists():
        return {
            "source_db_exists": False,
            "source_db_has_runs": False,
            "source_db_has_market_snapshots": False,
            "source_db_has_completed_runs": False,
            "source_db_error": "missing_source_db",
        }

    try:
        with sqlite3.connect(source_db) as conn:
            table_names = {
                str(row[0] or "").strip()
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            has_runs_table = "runs" in table_names
            has_snapshots_table = "market_snapshots" in table_names
            run_count = (
                int(conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] or 0)
                if has_runs_table
                else 0
            )
            completed_run_count = (
                int(
                    conn.execute(
                        "SELECT COUNT(*) FROM runs WHERE LOWER(COALESCE(status, '')) = 'completed'"
                    ).fetchone()[0]
                    or 0
                )
                if has_runs_table
                else 0
            )
            snapshot_count = (
                int(conn.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0] or 0)
                if has_snapshots_table
                else 0
            )
    except sqlite3.Error as exc:
        return {
            "source_db_exists": True,
            "source_db_has_runs": False,
            "source_db_has_market_snapshots": False,
            "source_db_has_completed_runs": False,
            "source_db_error": str(exc),
        }

    return {
        "source_db_exists": True,
        "source_db_has_runs": run_count > 0,
        "source_db_has_market_snapshots": snapshot_count > 0,
        "source_db_has_completed_runs": completed_run_count > 0,
        "source_db_error": "",
    }


def build_preflight_report(
    *,
    project_root: Path,
    workflow_path: Path,
    mode: str,
    runner_env_file: Path | None = None,
    source_db: Path | None = None,
) -> dict[str, Any]:
    runner_env_file = runner_env_file or DEFAULT_RUNNER_ENV_FILE
    env_values = read_runner_env_file(runner_env_file)

    runner_root = Path(
        env_values.get("DGTEAM_GITHUB_RUNNER_ROOT") or str(DEFAULT_RUNNER_ROOT)
    ).expanduser()
    runner_workdir = Path(
        env_values.get("DGTEAM_GITHUB_RUNNER_WORKDIR") or str(runner_root / "_work")
    ).expanduser()
    source_db = source_db or Path(
        env_values.get("DGTEAM_TRUSTED_RUNNER_SOURCE_DB") or str(default_real_source_db(project_root))
    ).expanduser()

    workflow_text = workflow_path.read_text(encoding="utf-8", errors="replace") if workflow_path.is_file() else ""
    workflow_checks = _workflow_checks(workflow_text)
    source_checks = _source_db_checks(source_db if mode == "real-source" else None)

    venv_python = next((candidate for candidate in _venv_python_candidates(project_root) if candidate.exists()), None)
    active_python = Path(sys.executable).expanduser().resolve() if str(sys.executable).strip() else None
    registration_inputs = {
        "repository": env_values.get("DGTEAM_GITHUB_REPOSITORY", ""),
        "runner_name": env_values.get("DGTEAM_GITHUB_RUNNER_NAME", ""),
        "runner_labels": env_values.get("DGTEAM_GITHUB_RUNNER_LABELS", DEFAULT_RUNNER_LABELS),
        "runner_group": env_values.get("DGTEAM_GITHUB_RUNNER_GROUP", DEFAULT_RUNNER_GROUP),
        "runner_root": str(runner_root),
        "runner_workdir": str(runner_workdir),
        "source_db": str(source_db),
        "asset_url": env_values.get("DGTEAM_GITHUB_RUNNER_ASSET_URL", ""),
    }
    missing_registration_inputs = [
        name
        for name, value in (
            ("DGTEAM_GITHUB_REPOSITORY", registration_inputs["repository"]),
            ("DGTEAM_GITHUB_RUNNER_NAME", registration_inputs["runner_name"]),
            ("DGTEAM_GITHUB_RUNNER_LABELS", registration_inputs["runner_labels"]),
        )
        if not str(value or "").strip()
    ]

    checks = {
        "project_root_exists": project_root.is_dir(),
        "venv_python_exists": venv_python is not None,
        "active_python_exists": bool(active_python and active_python.exists()),
        "workflow_exists": workflow_path.is_file(),
        "runner_env_file_exists": runner_env_file.is_file(),
        "runner_root_exists": runner_root.is_dir(),
        "runner_binary_exists": (runner_root / "run.sh").is_file() and (runner_root / "config.sh").is_file(),
        "runner_configured": (runner_root / ".runner").is_file(),
        **workflow_checks,
        **source_checks,
    }

    gate_ready = (
        checks["project_root_exists"]
        and (checks["venv_python_exists"] or checks["active_python_exists"])
        and checks["workflow_exists"]
        and checks["workflow_dispatch_present"]
        and checks["fixture_gate_command_present"]
        and checks["real_source_gate_command_present"]
        and checks["preflight_command_present"]
        and checks["registration_dry_run_present"]
        and checks["gate_result_artifact_present"]
    )
    if mode == "real-source":
        gate_ready = gate_ready and checks["source_db_exists"] and checks["source_db_has_completed_runs"]

    registration_ready = not missing_registration_inputs

    return {
        "ok": bool(gate_ready),
        "contract_version": TRUSTED_RUNNER_CONTRACT_VERSION,
        "mode": mode,
        "project_root": str(project_root),
        "workflow_path": str(workflow_path),
        "runner_env_file": str(runner_env_file),
        "runner_root": str(runner_root),
        "runner_workdir": str(runner_workdir),
        "venv_python": str(venv_python) if venv_python else "",
        "active_python": str(active_python) if active_python else "",
        "source_db": str(source_db),
        "expected_runner_labels": DEFAULT_RUNNER_LABELS.split(","),
        "checks": checks,
        "workflow_contract": {
            "workflow_name": "Release Rehearsal",
            "fixture_gate_command": "python -m dgteam.dev_cli candidate-gate-fixture",
            "real_source_gate_command": "python -m dgteam.dev_cli candidate-gate-real-source",
            "preflight_command": "python scripts/trusted_runner_preflight.py",
            "registration_dry_run_command": "python scripts/trusted_runner_register.py --dry-run",
            "artifact_fields": [
                "smoke_report.json",
                "gate_result.json",
                "trusted-runner-preflight.json",
                "trusted-runner-registration.json",
            ],
        },
        "registration_inputs": registration_inputs,
        "missing_registration_inputs": missing_registration_inputs,
        "gate_ready": bool(gate_ready),
        "registration_ready": bool(registration_ready),
    }
