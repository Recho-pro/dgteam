from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from dgteam.ops.runtime_audit import RuntimeAuditThresholds, build_runtime_audit


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit DGTEAM runtime backup, residue, backlog, and deployment evidence.")
    parser.add_argument("--project-root", default=str(PROJECT_ROOT))
    parser.add_argument("--backup-root", default="/var/backups/dgteam")
    parser.add_argument("--disk-warn-percent", type=int, default=80)
    parser.add_argument("--max-backup-age-hours", type=float, default=30.0)
    parser.add_argument("--max-staging-age-hours", type=float, default=6.0)
    parser.add_argument("--max-upload-age-hours", type=float, default=24.0)
    parser.add_argument("--max-worker-backlog", type=int, default=10)
    parser.add_argument("--max-failed-tasks", type=int, default=0)
    parser.add_argument("--assert-ok", action="store_true", help="Exit with code 1 when any warning-level alert is present.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_runtime_audit(
        project_root=Path(args.project_root).expanduser().resolve(),
        backup_root=Path(args.backup_root).expanduser().resolve(),
        thresholds=RuntimeAuditThresholds(
            disk_warn_percent=max(1, int(args.disk_warn_percent)),
            max_backup_age_hours=max(0.0, float(args.max_backup_age_hours)),
            max_staging_age_hours=max(0.0, float(args.max_staging_age_hours)),
            max_upload_age_hours=max(0.0, float(args.max_upload_age_hours)),
            max_worker_backlog=max(0, int(args.max_worker_backlog)),
            max_failed_tasks=max(0, int(args.max_failed_tasks)),
        ),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.assert_ok and not bool(report.get("ok")):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
