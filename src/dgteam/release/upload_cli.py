from __future__ import annotations

import argparse
import json
from pathlib import Path

from dgteam.core.console_encoding import configure_utf8_stdio
from dgteam.core.encoding_guard import assert_project_encoding_clean

from .upload_client import rollback_release, upload_release_bundle


def _default_project_root() -> Path:
    cwd = Path.cwd().resolve()
    if (cwd / "src" / "dgteam").exists():
        return cwd
    return Path(__file__).resolve().parents[3]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DGTEAM release upload client")
    sub = parser.add_subparsers(dest="command", required=True)

    upload = sub.add_parser("upload", help="Upload a release archive to the publish API")
    upload.add_argument("--server-url", required=True)
    upload.add_argument("--archive-path", required=True)
    upload.add_argument("--token", default="")
    upload.add_argument("--release-id", default="")
    upload.add_argument("--no-activate", action="store_true")
    upload.add_argument("--project-root", default="")
    upload.add_argument("--skip-encoding-check", action="store_true")

    rollback = sub.add_parser("rollback", help="Ask the publish API to roll back to the previous release")
    rollback.add_argument("--server-url", required=True)
    rollback.add_argument("--token", default="")
    return parser.parse_args()


def main() -> None:
    configure_utf8_stdio()
    args = parse_args()
    if args.command == "upload":
        project_root = Path(str(args.project_root or "")).expanduser().resolve() if str(args.project_root or "").strip() else _default_project_root()
        if not bool(args.skip_encoding_check):
            assert_project_encoding_clean(project_root)
        response = upload_release_bundle(
            server_url=args.server_url,
            archive_path=Path(args.archive_path),
            token=args.token,
            release_id=args.release_id,
            activate=not bool(args.no_activate),
        )
    else:
        response = rollback_release(server_url=args.server_url, token=args.token)
    print(json.dumps(response, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
