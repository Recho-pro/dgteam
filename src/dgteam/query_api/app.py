from __future__ import annotations

import argparse

from dgteam.core.console_encoding import configure_utf8_stdio
from dgteam.core.config import load_settings
from dgteam.core.logging import setup_logging
from dgteam.core.project_config import load_project_config
from dgteam.query_api import server


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DGTEAM query API service")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--db-path")
    return parser.parse_args()


def main() -> None:
    configure_utf8_stdio()
    args = parse_args()
    settings = load_settings()
    project_config = load_project_config()
    setup_logging(settings.log_level)
    argv = ["dgteam-query-api"]
    argv.extend(["--host", args.host or settings.query_api.host])
    argv.extend(["--port", str(args.port or settings.query_api.port)])
    argv.extend(["--db-path", str(args.db_path or project_config.paths.db_path)])
    import sys

    previous_argv = list(sys.argv)
    try:
        sys.argv = argv
        server.main()
    finally:
        sys.argv = previous_argv


if __name__ == "__main__":
    main()
