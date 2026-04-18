from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from dgteam.ops.trusted_runner import DEFAULT_RUNNER_ENV_FILE, build_preflight_report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate the DGTEAM trusted-runner release gate contract.")
    parser.add_argument("--project-root", default=str(PROJECT_ROOT))
    parser.add_argument("--workflow-path", default=str(PROJECT_ROOT / ".github" / "workflows" / "release_rehearsal.yml"))
    parser.add_argument("--runner-env-file", default=str(DEFAULT_RUNNER_ENV_FILE))
    parser.add_argument("--mode", choices=("fixture", "real-source"), default="real-source")
    parser.add_argument("--source-db", default="")
    parser.add_argument("--assert-gate-ready", action="store_true")
    parser.add_argument("--assert-registration-ready", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_preflight_report(
        project_root=Path(args.project_root).expanduser().resolve(),
        workflow_path=Path(args.workflow_path).expanduser().resolve(),
        mode=str(args.mode),
        runner_env_file=Path(args.runner_env_file).expanduser().resolve(),
        source_db=Path(args.source_db).expanduser().resolve() if str(args.source_db).strip() else None,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.assert_gate_ready and not bool(report.get("gate_ready")):
        return 1
    if args.assert_registration_ready and not bool(report.get("registration_ready")):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
