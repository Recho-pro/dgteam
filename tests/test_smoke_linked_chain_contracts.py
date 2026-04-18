from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_fixture_smoke_report_writes_gate_result_and_audit_chain(tmp_path: Path) -> None:
    explicit_report = tmp_path / "reports" / "fixture_smoke_report.json"
    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "smoke_linked_chain.py"),
            "--project-root",
            str(tmp_path),
            "--fixture",
            "--report-path",
            str(explicit_report),
        ],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(result.stdout)

    assert payload["ok"] is True
    assert payload["mode"] == "fixture"
    assert payload["gate"]["contract_version"] == "release-gate.v1"
    assert payload["gate"]["mode"] == "fixture"
    assert explicit_report.is_file()

    smoke_report_path = Path(payload["audit_chain"]["smoke_report_path"])
    gate_result_path = Path(payload["audit_chain"]["gate_result_path"])
    rollback_status_path = Path(payload["audit_chain"]["rollback"]["status_path"])
    rollback_events_path = Path(payload["audit_chain"]["rollback"]["events_path"])
    archived_current_dir = Path(payload["audit_chain"]["rollback_evidence"]["archived_current_dir"])

    assert smoke_report_path.is_file()
    assert gate_result_path.is_file()
    assert rollback_status_path.is_file()
    assert rollback_events_path.is_file()
    assert archived_current_dir.is_dir()

    gate_result = json.loads(gate_result_path.read_text(encoding="utf-8"))
    assert gate_result["ok"] is True
    assert gate_result["gate"]["mode"] == "fixture"
    assert gate_result["audit_chain"]["rollback"]["status"]["step"] == "rollback"
    assert Path(payload["smoke_root"], "runtime", "local").is_dir()
    assert Path(payload["smoke_root"], "runtime", "cloud").is_dir()
    assert payload["activation_checks"]["query_ui"]["ok"] is True
    assert payload["activation_checks"]["unknown_api"]["ok"] is True
    assert payload["activation_checks"]["wechat"]["ok"] is True
    assert payload["rollback_checks"]["query_ui"]["ok"] is True
    assert payload["rollback_checks"]["unknown_api"]["ok"] is True
    assert gate_result["activation_checks"]["query_ui"]["version"]
    assert gate_result["audit_chain"]["ops_audit"]["backup_freshness"]["latest_backup"]
    assert payload["source_db_contract"]["selection"] == "fixture"
    assert payload["source_db_contract"]["default_relative_path"] == "runtime/cloud/current/dgteam.db"
    assert payload["source_db_contract"]["legacy_override_relative_path"] == "runtime/local/data/dgteam.db"
