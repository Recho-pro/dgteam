from __future__ import annotations

import argparse
import json

from dgteam.core.config import load_settings
from dgteam.core.console_encoding import configure_utf8_stdio
from dgteam.integrations.wechat_official.client import WechatOfficialClient
from dgteam.integrations.wechat_official.menu import build_default_menu


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage DGTEAM official account custom menus")
    parser.add_argument("--base-url", default="https://dgtdnb.com")
    parser.add_argument("--show-default", action="store_true")
    parser.add_argument("--publish-default", action="store_true")
    parser.add_argument("--get-current", action="store_true")
    parser.add_argument("--delete-current", action="store_true")
    return parser.parse_args()


def main() -> None:
    configure_utf8_stdio()
    args = parse_args()
    settings = load_settings()
    client = WechatOfficialClient(config=settings.wechat_official)

    if args.show_default:
        print(json.dumps(build_default_menu(base_url=args.base_url), ensure_ascii=False, indent=2))
        return

    if args.publish_default:
        result = client.create_menu(build_default_menu(base_url=args.base_url))
        print(json.dumps({"ok": True, "action": "publish_default_menu", "result": result}, ensure_ascii=False, indent=2))
        return

    if args.get_current:
        result = client.get_current_menu()
        print(json.dumps({"ok": True, "action": "get_current_menu", "result": result}, ensure_ascii=False, indent=2))
        return

    if args.delete_current:
        result = client.delete_menu()
        print(json.dumps({"ok": True, "action": "delete_current_menu", "result": result}, ensure_ascii=False, indent=2))
        return

    print(
        json.dumps(
            {
                "ok": True,
                "hint": "Use --show-default, --publish-default, --get-current, or --delete-current.",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
