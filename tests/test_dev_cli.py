from __future__ import annotations

import sys

from dgteam.dev_cli import PROJECT_ROOT, _command_steps, build_parser


def test_install_ui_browser_command_uses_playwright_chromium() -> None:
    steps = _command_steps("install-ui-browser")
    assert steps == [
        (
            "playwright-install-chromium",
            [sys.executable, "-m", "playwright", "install", "chromium"],
        )
    ]


def test_ops_audit_command_uses_runtime_audit_script() -> None:
    steps = _command_steps("ops-audit")
    assert steps == [
        (
            "runtime-audit",
            [sys.executable, "scripts/ops_runtime_audit.py", "--project-root", str(PROJECT_ROOT)],
        )
    ]


def test_candidate_gate_runs_release_contracts_and_fixture_smoke() -> None:
    steps = _command_steps("candidate-gate")
    step_names = [name for name, _ in steps]
    assert step_names == [
        "compileall",
        "encoding-guard",
        "pytest-release-contracts",
        "release-rehearsal-smoke",
    ]
    smoke_step = steps[-1][1]
    assert smoke_step[-1] == "--fixture"


def test_real_source_candidate_gate_reuses_same_contract_but_switches_smoke_mode(monkeypatch) -> None:
    monkeypatch.setenv("DGTEAM_SMOKE_SOURCE_DB", "/srv/dgteam/runtime/local/data/dgteam.db")
    steps = _command_steps("candidate-gate-real-source")
    step_names = [name for name, _ in steps]
    assert step_names == [
        "compileall",
        "encoding-guard",
        "pytest-release-contracts",
        "release-rehearsal-smoke-real-source",
    ]
    smoke_step = steps[-1][1]
    assert smoke_step[-4:] == [
        "--mode",
        "real-source",
        "--source-db",
        "/srv/dgteam/runtime/local/data/dgteam.db",
    ]


def test_parser_help_mentions_active_release_default_and_explicit_working_override() -> None:
    parser = build_parser()
    help_text = parser.format_help()

    assert "runtime/cloud/current/dgteam.db" in help_text
    assert "runtime/local/data/dgteam.db" in help_text
    assert "DGTEAM_SMOKE_SOURCE_DB" in help_text


def test_parser_exposes_round_17_commands() -> None:
    parser = build_parser()
    choices = set(parser._actions[1].choices)
    assert {
        "install-ui-browser",
        "ops-audit",
        "smoke-linked-chain",
        "smoke-linked-chain-real-source",
        "candidate-gate",
        "candidate-gate-fixture",
        "candidate-gate-real-source",
    }.issubset(choices)
