from __future__ import annotations

from typing import Any
from urllib.parse import quote


def _query_url(base_url: str, query: str) -> str:
    clean_base = str(base_url or "").rstrip("/")
    return f"{clean_base}/?q={quote(str(query or '').strip())}"


def build_default_menu(*, base_url: str = "https://dgtdnb.com") -> dict[str, Any]:
    clean_base = str(base_url or "").rstrip("/")
    return {
        "button": [
            {
                "type": "view",
                "name": "打开行情页",
                "url": f"{clean_base}/",
            },
            {
                "name": "热门机型",
                "sub_button": [
                    {
                        "type": "view",
                        "name": "iPhone17PM",
                        "url": _query_url(clean_base, "iPhone17ProMax"),
                    },
                    {
                        "type": "view",
                        "name": "红米K80",
                        "url": _query_url(clean_base, "Redmi K80"),
                    },
                    {
                        "type": "view",
                        "name": "iQOO15",
                        "url": _query_url(clean_base, "iQOO15"),
                    },
                ],
            },
            {
                "name": "查询帮助",
                "sub_button": [
                    {
                        "type": "click",
                        "name": "怎么查文字",
                        "key": "DG_HELP",
                    },
                    {
                        "type": "click",
                        "name": "怎么发截图",
                        "key": "DG_IMAGE_HELP",
                    },
                    {
                        "type": "click",
                        "name": "联系团队",
                        "key": "DG_CONTACT",
                    },
                ],
            },
        ]
    }

