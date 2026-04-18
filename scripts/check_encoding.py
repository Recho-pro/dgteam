from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from dgteam.core.encoding_guard import scan_project_tree, summarize_issues


def _configure_console_streams() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            continue


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan DGTEAM source files for encoding and mojibake issues.")
    parser.add_argument("paths", nargs="*", help="Optional file paths to check. Defaults to the whole tracked source tree.")
    parser.add_argument("--project-root", default=str(PROJECT_ROOT))
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON output.")
    return parser.parse_args()


def main() -> int:
    _configure_console_streams()
    args = parse_args()
    project_root = Path(args.project_root).expanduser().resolve()
    paths = [Path(value) for value in list(args.paths or [])]
    issues = scan_project_tree(project_root, paths=paths or None)
    if args.json:
        payload = {
            "ok": not issues,
            "issue_count": len(issues),
            "issues": [issue.to_dict() for issue in issues],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(summarize_issues(issues))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
