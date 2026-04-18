from __future__ import annotations

import argparse
import json
import sys

from dgteam.agent import crawler
from dgteam.agent.pipeline import run_pipeline
from dgteam.core.console_encoding import configure_utf8_stdio
from dgteam.core.config import load_settings
from dgteam.core.logging import setup_logging
from dgteam.core.paths import ensure_runtime_dirs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DGTEAM local agent runner")
    sub = parser.add_subparsers(dest="command")

    pipeline = sub.add_parser("pipeline", help="Run the local DGTEAM production pipeline")
    pipeline.add_argument("--mode", default="dry-run", choices=["dry-run", "publish", "collect-and-publish"])
    pipeline.add_argument("--run-key", default="")
    pipeline.add_argument("--release-id", default="")
    pipeline.add_argument("crawler_args", nargs=argparse.REMAINDER)

    crawler_cmd = sub.add_parser("crawler", help="Run the migrated crawler entrypoint")
    crawler_cmd.add_argument("crawler_args", nargs=argparse.REMAINDER)
    return parser


def main() -> None:
    configure_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args()

    settings = load_settings()
    setup_logging(settings.log_level)
    ensure_runtime_dirs(settings)

    if args.command == "crawler":
        forwarded = ["dgteam-crawler", *list(args.crawler_args or [])]
        previous = list(sys.argv)
        try:
            sys.argv = forwarded
            crawler.main()
        finally:
            sys.argv = previous
        return

    mode = getattr(args, "mode", "dry-run")
    result = run_pipeline(
        mode,
        run_key=str(getattr(args, "run_key", "") or ""),
        release_id=str(getattr(args, "release_id", "") or ""),
        crawler_args=list(getattr(args, "crawler_args", []) or []),
    )
    print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
