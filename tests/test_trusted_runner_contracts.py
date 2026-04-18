from __future__ import annotations

import sqlite3
from pathlib import Path

from dgteam.ops.trusted_runner import (
    DEFAULT_RUNNER_LABELS,
    TRUSTED_RUNNER_CONTRACT_VERSION,
    build_preflight_report,
)


def _write_workflow(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """
name: Release Rehearsal

on:
  schedule:
    - cron: "30 18 * * *"
  workflow_dispatch:

jobs:
  fixture:
    runs-on: ubuntu-latest
    steps:
      - run: python scripts/trusted_runner_preflight.py --mode fixture
      - run: python -m dgteam.dev_cli candidate-gate-fixture
  real-source:
    runs-on:
      - self-hosted
      - dgteam-trusted
    steps:
      - run: python scripts/trusted_runner_preflight.py --mode real-source
      - run: python scripts/trusted_runner_register.py --dry-run
      - run: python -m dgteam.dev_cli candidate-gate-real-source
      - run: echo gate_result.json
""".strip(),
        encoding="utf-8",
    )


def _write_source_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY, run_key TEXT, status TEXT)")
        conn.execute("CREATE TABLE market_snapshots (id INTEGER PRIMARY KEY, run_key TEXT)")
        conn.execute("INSERT INTO runs (run_key, status) VALUES ('prod_2026', 'completed')")
        conn.execute("INSERT INTO market_snapshots (run_key) VALUES ('prod_2026')")
        conn.commit()


def test_preflight_report_marks_fixture_and_real_source_gate_ready(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    workflow_path = project_root / ".github" / "workflows" / "release_rehearsal.yml"
    runner_env = project_root / ".runner.env"
    source_db = project_root / "runtime" / "cloud" / "current" / "dgteam.db"
    venv_python = project_root / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True, exist_ok=True)
    venv_python.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    _write_workflow(workflow_path)
    source_db.parent.mkdir(parents=True, exist_ok=True)
    _write_source_db(source_db)
    runner_env.write_text(
        "\n".join(
            [
                "DGTEAM_GITHUB_REPOSITORY=owner/repo",
                "DGTEAM_GITHUB_RUNNER_NAME=dgteam-trusted-live",
                f"DGTEAM_GITHUB_RUNNER_LABELS={DEFAULT_RUNNER_LABELS}",
                f"DGTEAM_TRUSTED_RUNNER_SOURCE_DB={source_db}",
            ]
        ),
        encoding="utf-8",
    )

    fixture = build_preflight_report(
        project_root=project_root,
        workflow_path=workflow_path,
        mode="fixture",
        runner_env_file=runner_env,
    )
    real_source = build_preflight_report(
        project_root=project_root,
        workflow_path=workflow_path,
        mode="real-source",
        runner_env_file=runner_env,
        source_db=source_db,
    )

    assert fixture["contract_version"] == TRUSTED_RUNNER_CONTRACT_VERSION
    assert fixture["gate_ready"] is True
    assert real_source["gate_ready"] is True
    assert real_source["registration_ready"] is True
    assert real_source["checks"]["source_db_has_completed_runs"] is True
    assert real_source["workflow_contract"]["real_source_gate_command"].endswith("candidate-gate-real-source")


def test_preflight_defaults_to_project_current_db_when_env_does_not_override(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    workflow_path = project_root / ".github" / "workflows" / "release_rehearsal.yml"
    runner_env = project_root / ".runner.env"
    source_db = project_root / "runtime" / "cloud" / "current" / "dgteam.db"

    _write_workflow(workflow_path)
    source_db.parent.mkdir(parents=True, exist_ok=True)
    _write_source_db(source_db)
    runner_env.write_text(
        "\n".join(
            [
                "DGTEAM_GITHUB_REPOSITORY=owner/repo",
                "DGTEAM_GITHUB_RUNNER_NAME=dgteam-trusted-live",
                f"DGTEAM_GITHUB_RUNNER_LABELS={DEFAULT_RUNNER_LABELS}",
            ]
        ),
        encoding="utf-8",
    )

    real_source = build_preflight_report(
        project_root=project_root,
        workflow_path=workflow_path,
        mode="real-source",
        runner_env_file=runner_env,
    )

    assert real_source["gate_ready"] is True
    assert real_source["source_db"] == str(source_db.resolve())
    assert real_source["checks"]["source_db_has_completed_runs"] is True


def test_runner_rollout_templates_are_shipped() -> None:
    project_root = Path(__file__).resolve().parents[1]
    workflow = (project_root / ".github" / "workflows" / "release_rehearsal.yml").read_text(encoding="utf-8")
    production = (project_root / "docs" / "PRODUCTION_DEPLOYMENT.md").read_text(encoding="utf-8")
    operations = (project_root / "docs" / "OPERATIONS.md").read_text(encoding="utf-8")
    runner_env = (project_root / "deploy" / "linux" / "env" / "dgteam.runner.env.example").read_text(encoding="utf-8")
    runner_service = (project_root / "deploy" / "linux" / "systemd" / "dgteam-github-runner.service").read_text(encoding="utf-8")
    preflight_wrapper = (project_root / "deploy" / "linux" / "bin" / "dgteam-runner-preflight.sh").read_text(encoding="utf-8")
    register_wrapper = (project_root / "deploy" / "linux" / "bin" / "dgteam-runner-register.sh").read_text(encoding="utf-8")

    assert "trusted_runner_preflight.py" in workflow
    assert "trusted_runner_register.py" in workflow
    assert "self-hosted" in workflow
    assert "dgteam-trusted" in workflow
    assert "DGTEAM_TRUSTED_RUNNER_SOURCE_DB" in runner_env
    assert "ExecStart=/srv/dgteam/runtime/cloud/github_runner/run.sh" in runner_service
    assert "trusted_runner_preflight.py" in preflight_wrapper
    assert "trusted_runner_register.py" in register_wrapper
    assert "dgteam-github-runner.service" in production
    assert "dgteam-runner-register.sh --dry-run" in production
    assert "dgteam-github-runner.service" in operations
    assert "github-runner.log" in operations
