from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from dgteam.ops.real_source_db import ACTIVE_RELEASE_SOURCE_DB_RELATIVE, LEGACY_WORKING_SOURCE_DB_RELATIVE

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _command_steps(command: str) -> list[tuple[str, list[str]]]:
    python = sys.executable

    def smoke_args(*, fixture: bool) -> list[str]:
        argv = [python, "scripts/smoke_linked_chain.py"]
        if fixture:
            argv.append("--fixture")
        else:
            argv.extend(["--mode", "real-source"])
            source_db = str(os.environ.get("DGTEAM_SMOKE_SOURCE_DB") or "").strip()
            if source_db:
                argv.extend(["--source-db", source_db])
        return argv

    if command == "install-ui-browser":
        return [
            ("playwright-install-chromium", [python, "-m", "playwright", "install", "chromium"]),
        ]
    if command == "quality":
        return [
            ("compileall", [python, "-m", "compileall", "src"]),
            ("encoding-guard", [python, "scripts/check_encoding.py"]),
        ]
    if command == "test":
        return [
            ("pytest", [python, "-m", "pytest", "-q"]),
        ]
    if command == "test-ui":
        return [
            ("pytest-query-ui", [python, "-m", "pytest", "-q", "tests/test_query_ui_e2e.py"]),
        ]
    if command == "ops-audit":
        return [
            ("runtime-audit", [python, "scripts/ops_runtime_audit.py", "--project-root", str(PROJECT_ROOT)]),
        ]
    if command == "smoke-linked-chain":
        return [
            ("release-rehearsal-smoke", smoke_args(fixture=True)),
        ]
    if command == "smoke-linked-chain-real-source":
        return [
            ("release-rehearsal-smoke-real-source", smoke_args(fixture=False)),
        ]
    if command == "candidate-gate":
        return _command_steps("candidate-gate-fixture")
    if command == "candidate-gate-fixture":
        return (
            _command_steps("quality")
            + [
                (
                    "pytest-release-contracts",
                    [python, "-m", "pytest", "-q", "tests/test_release_builder_atomicity.py", "tests/test_release_contracts.py", "tests/test_runtime_audit.py", "tests/test_dev_cli.py"],
                )
            ]
            + _command_steps("smoke-linked-chain")
        )
    if command == "candidate-gate-real-source":
        return (
            _command_steps("quality")
            + [
                (
                    "pytest-release-contracts",
                    [python, "-m", "pytest", "-q", "tests/test_release_builder_atomicity.py", "tests/test_release_contracts.py", "tests/test_runtime_audit.py", "tests/test_dev_cli.py"],
                )
            ]
            + _command_steps("smoke-linked-chain-real-source")
        )
    if command == "ci":
        return _command_steps("quality") + _command_steps("test")
    raise ValueError(f"Unsupported command: {command}")


def _run_steps(command: str) -> int:
    for step_name, argv in _command_steps(command):
        print(f"[dgteam-dev] running {step_name}: {' '.join(argv)}")
        result = subprocess.run(argv, cwd=PROJECT_ROOT)
        if result.returncode != 0:
            print(f"[dgteam-dev] {step_name} failed with exit code {result.returncode}")
            return result.returncode
    print(f"[dgteam-dev] {command} completed successfully")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Developer helper commands for the DGTEAM editable-install workflow. "
            "Install dev dependencies first with: python -m pip install -e .[dev]"
        )
    )
    parser.add_argument(
        "command",
        choices=(
            "install-ui-browser",
            "quality",
            "test",
            "test-ui",
            "ops-audit",
            "smoke-linked-chain",
            "smoke-linked-chain-real-source",
            "candidate-gate",
            "candidate-gate-fixture",
            "candidate-gate-real-source",
            "ci",
        ),
        help=(
            "install-ui-browser=install Playwright chromium once for browser E2E, "
            "quality=compileall+encoding guard, "
            "test=pytest, "
            "test-ui=query_ui browser baseline, "
            "ops-audit=runtime backup/backlog/residue audit, "
            "smoke-linked-chain=fixture-based release rehearsal, "
            f"smoke-linked-chain-real-source=real-source release rehearsal using {ACTIVE_RELEASE_SOURCE_DB_RELATIVE} by default "
            f"or {LEGACY_WORKING_SOURCE_DB_RELATIVE} only when DGTEAM_SMOKE_SOURCE_DB explicitly overrides it, "
            "candidate-gate=fixture gate alias, "
            "candidate-gate-fixture=quality+release contracts+fixture smoke rehearsal, "
            "candidate-gate-real-source=quality+release contracts+real-source smoke rehearsal, "
            "ci=quality+test"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return _run_steps(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
