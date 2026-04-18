from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from dgteam.core.encoding_guard import attempt_mojibake_line_repair, scan_project_tree
from dgteam.core.textio import decode_external_text_bytes, write_text_utf8


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
    parser = argparse.ArgumentParser(description="Repair common DGTEAM source encoding problems.")
    parser.add_argument("paths", nargs="*", help="Optional file paths to repair. Defaults to the whole source tree.")
    parser.add_argument("--project-root", default=str(PROJECT_ROOT))
    parser.add_argument("--apply", action="store_true", help="Write the repaired files back to disk.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON output.")
    return parser.parse_args()


def _repair_text_content(text: str) -> tuple[str, bool]:
    changed = False
    repaired_lines: List[str] = []
    for line in text.splitlines(keepends=True):
        repaired = attempt_mojibake_line_repair(line)
        if repaired is not None and repaired != line:
            repaired_lines.append(repaired)
            changed = True
        else:
            repaired_lines.append(line)
    return "".join(repaired_lines), changed


def _repair_file(path: Path) -> Dict[str, Any]:
    target = Path(path).expanduser().resolve()
    raw = target.read_bytes()
    result: Dict[str, Any] = {
        "path": str(target),
        "changed": False,
        "repair_mode": "",
    }
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        decoded = decode_external_text_bytes(raw, source=str(target))
        result["changed"] = True
        result["repair_mode"] = f"normalize_from_{decoded.encoding}"
        result["text"] = decoded.text
        return result

    repaired_text, changed = _repair_text_content(text)
    result["changed"] = changed
    result["repair_mode"] = "mojibake_line_repair" if changed else ""
    result["text"] = repaired_text
    return result


def main() -> int:
    _configure_console_streams()
    args = parse_args()
    project_root = Path(args.project_root).expanduser().resolve()
    candidate_paths = [Path(value) for value in list(args.paths or [])]
    issues = scan_project_tree(project_root, paths=candidate_paths or None)
    unique_paths = sorted({Path(issue.path) for issue in issues})
    repairs: List[Dict[str, Any]] = []
    for path in unique_paths:
        repair = _repair_file(path)
        if repair["changed"] and args.apply:
            write_text_utf8(Path(repair["path"]), str(repair["text"]))
        repair.pop("text", None)
        repairs.append(repair)

    payload = {
        "ok": not any(item.get("changed") for item in repairs),
        "apply": bool(args.apply),
        "repair_count": sum(1 for item in repairs if item.get("changed")),
        "repairs": repairs,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["repair_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
