from __future__ import annotations

from typing import Any


def format_no_result(query: str) -> str:
    return (
        f"没找到“{str(query or '').strip()}”对应的行情。\n"
        "你可以换一种更接近机型的写法再发一次，例如：\n"
        "iPhone17ProMax\n"
        "苹果17pro\n"
        "红米k80"
    )


def format_ambiguous_result(query: str, results: list[dict[str, Any]]) -> str:
    lines = [f"“{str(query or '').strip()}”可能对应这些机型："]
    for index, item in enumerate(results[:6], start=1):
        label = str(item.get("label") or item.get("model_title") or "").strip() or "未命名机型"
        meta = str(item.get("meta") or "").strip()
        line = f"{index}. {label}"
        if meta:
            line += f"｜{meta}"
        lines.append(line)
    lines.append("直接回复更精确的型号继续查，例如：iPhone17ProMax 256G")
    return "\n".join(lines)


def _branch_capacity_lines(snapshot: dict[str, Any], *, max_capacities: int = 4) -> list[str]:
    branches = list(snapshot.get("branches") or [])
    if not branches:
        return []
    first_branch = dict(branches[0] or {})
    capacity_groups = list(first_branch.get("capacity_groups") or [])
    lines: list[str] = []
    for group in capacity_groups[:max_capacities]:
        label = str(group.get("capacity_label") or "").strip()
        price_range = str(group.get("price_range") or "--").strip()
        if label:
            lines.append(f"{label}：{price_range}")
    return lines


def format_market_snapshot(*, candidate: dict[str, Any], snapshot: dict[str, Any]) -> str:
    title = str(
        snapshot.get("header", {}).get("title")
        or candidate.get("label")
        or candidate.get("family_title")
        or candidate.get("model_title")
        or "当前机型"
    ).strip()
    hero = dict(snapshot.get("hero") or {})
    market_v1 = dict(snapshot.get("market_v1") or {})
    latest_labels = list(snapshot.get("header", {}).get("selected_gprice_labels") or [])
    range_text = str(market_v1.get("price_range") or "--").strip()
    market_price = hero.get("market_price")

    lines = [title]
    if range_text and range_text != "--":
        lines.append(f"行情区间：{range_text}")
    if market_price:
        lines.append(f"主流参考：¥{int(market_price):,}")
    if latest_labels:
        lines.append(f"最近报价日：{' / '.join(str(label) for label in latest_labels[:3])}")

    capacity_lines = _branch_capacity_lines(snapshot)
    if capacity_lines:
        lines.append("主要规格：")
        lines.extend(capacity_lines)

    lines.append("回复更精确的容量或颜色，也可以继续细查。")
    return "\n".join(lines)
