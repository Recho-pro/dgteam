from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, List, Mapping, Sequence


APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = Path(__file__).resolve().parents[3]

from . import server  # noqa: E402
from dgteam.release.live_market import build_live_market_payload  # noqa: E402
from dgteam.market.market_engine import build_capacity_market_breakdown, build_market_v1  # noqa: E402
from dgteam.market.price_cleaning import dedupe_latest_rows, select_recent_sample_window  # noqa: E402


def zh(codepoint_text: str) -> str:
    return codepoint_text.encode("ascii").decode("unicode_escape")


@dataclass(frozen=True)
class SearchCase:
    query: str
    expected_top: str


DEFAULT_SEARCH_CASES: List[SearchCase] = [
    SearchCase(zh(r"\u7ea2\u7c73k80"), "Redmi K80"),
    SearchCase(zh(r"\u7ea2\u7c73 k80"), "Redmi K80"),
    SearchCase("hong mi k80", "Redmi K80"),
    SearchCase("k80p", "Redmi K80 Pro"),
    SearchCase("k80u", zh(r"Redmi K80 \u81f3\u5c0a\u7248")),
    SearchCase(zh(r"\u7ea2\u7c73k80\u81f3\u5c0a"), zh(r"Redmi K80 \u81f3\u5c0a\u7248")),
    SearchCase(zh(r"\u7ea2\u7c73k8o"), "Redmi K80"),
    SearchCase(zh(r"\u82f9\u679c17"), "iPhone 17"),
    SearchCase(zh(r"\u82f9\u679c\u5341\u4e03"), "iPhone 17"),
    SearchCase("pingguo17", "iPhone 17"),
    SearchCase("iphone17", "iPhone 17"),
    SearchCase(zh(r"\u82f9\u679c17p"), "iPhone 17 Pro"),
    SearchCase(zh(r"\u82f9\u679c17pm"), "iPhone 17 Pro Max"),
    SearchCase("pingguo17pm", "iPhone 17 Pro Max"),
    SearchCase("ping guo 17 pm", "iPhone 17 Pro Max"),
    SearchCase(zh(r"\u82f9\u679c \u5341\u4e03 pro max"), "iPhone 17 Pro Max"),
    SearchCase("17 pro max", "iPhone 17 Pro Max"),
    SearchCase(zh(r"\u82f9\u679c17pro"), "iPhone 17 Pro"),
    SearchCase(zh(r"\u82f9\u679c20w"), zh(r"Apple\u5145\u7535\u5934/\u6570\u636e\u7ebf")),
    SearchCase("pingguo20w", zh(r"Apple\u5145\u7535\u5934/\u6570\u636e\u7ebf")),
    SearchCase(zh(r"\u82f9\u679c\u5145\u7535\u5934"), zh(r"Apple\u5145\u7535\u5934/\u6570\u636e\u7ebf")),
    SearchCase(zh(r"\u82f9\u679c\u9f20\u6807"), zh(r"Magic Mouse(\u9f20\u6807)")),
    SearchCase(zh(r"\u82f9\u679c\u89e6\u63a7\u7b14"), zh(r"Apple Pencil (\u624b\u5199\u7b14)")),
    SearchCase(zh(r"\u82f9\u679c\u624b\u673a\u58f3"), zh(r"Apple\u539f\u88c5\u4fdd\u62a4\u58f3")),
    SearchCase(zh(r"\u534e\u4e3a\u8868\u58f3"), zh(r"\u534e\u4e3a\u8868\u58f3")),
    SearchCase(zh(r"\u82f9\u679c\u5e73\u677fair"), zh(r"iPad Air 8\u4ee3 11\u82f1\u5bf8(2026\u6b3e)")),
    SearchCase("hongmik80", "Redmi K80"),
    SearchCase("vivo x200 pro", "X200Pro"),
    SearchCase("mate80rs", zh(r"Mate80RS \u975e\u51e1\u5927\u5e08")),
    SearchCase(zh(r"mate80\u4fdd\u65f6\u6377"), zh(r"Mate80RS \u975e\u51e1\u5927\u5e08")),
    SearchCase(zh(r"mate80\u5927\u5e08"), zh(r"Mate80RS \u975e\u51e1\u5927\u5e08")),
    SearchCase("mate80baoshijie", zh(r"Mate80RS \u975e\u51e1\u5927\u5e08")),
    SearchCase("hua wei mate 80 rs", zh(r"Mate80RS \u975e\u51e1\u5927\u5e08")),
    SearchCase("mate80pm", "Mate80Pro Max"),
    SearchCase(zh(r"\u8363\u8000\u4fdd\u65f6\u6377"), "Magic8 RSR"),
    SearchCase(zh(r"\u8363\u8000magic\u4fdd\u65f6\u6377"), "Magic8 RSR"),
    SearchCase("rongyaobaoshijie", "Magic8 RSR"),
    SearchCase("iphnoe17", "iPhone 17"),
    SearchCase("aple17", "iPhone 17"),
]


def make_row(
    *,
    merchant: str,
    group_title: str,
    imported_at: str,
    gprice: str,
    price_text: str,
    row_id: int,
    dstatus: str = zh(r"\u516c\u53f8\u7eaf\u539f\u5c01"),
    brand_title: str = zh(r"\u82f9\u679c"),
    series_title: str = "iPhone 17",
    model_title: str = zh(r"17 Pro Max 6.9\u5bf8 \u56fd\u884c"),
) -> dict[str, Any]:
    return {
        "id": row_id,
        "brand_title": brand_title,
        "series_title": series_title,
        "model_title": model_title,
        "group_title": group_title,
        "condition_bucket": "apple_company_pure_sealed_target",
        "sname": merchant,
        "sno": "",
        "gid": str(4000000000000 + row_id),
        "gprice": gprice,
        "gprice_two": "",
        "price_text": price_text,
        "dstatus": dstatus,
        "imported_at": imported_at,
    }


def run_search_cases(app: server.QueryApp, cases: Iterable[SearchCase], limit: int = 6) -> int:
    failures = 0
    for case in cases:
        payload = app.search_payload(case.query, limit=limit)
        labels = [str(item.get("label") or "") for item in payload.get("results") or []]
        actual_top = labels[0] if labels else ""
        ok = actual_top == case.expected_top
        marker = "PASS" if ok else "FAIL"
        print(f"[{marker}] search {case.query} -> {actual_top or '--'}")
        if not ok:
            failures += 1
            print(f"       expected: {case.expected_top}")
            print(f"       top{min(limit, len(labels))}: {labels[:limit]}")
    return failures


def assert_equal(name: str, actual: Any, expected: Any) -> int:
    ok = actual == expected
    marker = "PASS" if ok else "FAIL"
    print(f"[{marker}] {name}: {actual!r}")
    if ok:
        return 0
    print(f"       expected: {expected!r}")
    return 1


class FakeStorage:
    def __init__(self, run_key: str, rows: Sequence[Mapping[str, Any]]):
        self.run_key = run_key
        self.rows = [dict(row) for row in rows]

    def get_preferred_run_key(self) -> str:
        return self.run_key

    def get_external_reference_map(self) -> dict[str, Any]:
        return {}

    def get_latest_reference_import_run(self) -> None:
        return None

    def get_run_quote_rows(self, run_key: str) -> List[dict[str, Any]]:
        return [dict(row) for row in self.rows if str(row.get("run_key") or self.run_key) == run_key]


def run_core_semantics() -> int:
    failures = 0

    duplicate_rows = [
        make_row(
            merchant="A",
            group_title=zh(r"\u9ed1\u8272256G"),
            imported_at="2026-04-13 10:00:00",
            gprice="04-12",
            price_text="9580",
            row_id=1,
        ),
        make_row(
            merchant="A",
            group_title=zh(r"\u9ed1\u8272256G"),
            imported_at="2026-04-13 12:00:00",
            gprice="04-13",
            price_text="9610",
            row_id=2,
        ),
        make_row(
            merchant="B",
            group_title=zh(r"\u767d\u8272256G"),
            imported_at="2026-04-13 11:30:00",
            gprice="04-13",
            price_text="9620",
            row_id=3,
        ),
    ]
    deduped = dedupe_latest_rows(duplicate_rows)
    failures += assert_equal("dedupe keeps latest merchant row", deduped[0]["price_text"], "9610")
    failures += assert_equal("dedupe result count", len(deduped), 2)

    window_rows = [
        make_row(merchant="A", group_title=zh(r"\u9ed1\u8272256G"), imported_at="2026-04-13 12:00:00", gprice="04-13", price_text="9600", row_id=10),
        make_row(merchant="B", group_title=zh(r"\u767d\u8272256G"), imported_at="2026-04-13 11:00:00", gprice="04-13", price_text="9610", row_id=11),
        make_row(merchant="C", group_title=zh(r"\u6df1\u84dd\u8272256G"), imported_at="2026-04-12 15:00:00", gprice="04-12", price_text="9590", row_id=12),
        make_row(merchant="D", group_title=zh(r"\u661f\u5b87\u6a59\u8272256G"), imported_at="2026-04-12 14:00:00", gprice="04-12", price_text="9605", row_id=13),
        make_row(merchant="E", group_title=zh(r"\u94f6\u8272256G"), imported_at="2026-04-12 13:00:00", gprice="04-12", price_text="9625", row_id=14),
    ]
    selected_rows, selected_labels = select_recent_sample_window(window_rows, min_samples=4, max_labels=3)
    failures += assert_equal("recent window backfills label list", selected_labels, ["04-13", "04-12"])
    failures += assert_equal("recent window backfills row count", len(selected_rows), 5)

    market = build_market_v1(
        rows=window_rows,
        brand_title=zh(r"\u82f9\u679c"),
        series_title="iPhone 17",
        model_title=zh(r"17 Pro Max 6.9\u5bf8 \u56fd\u884c"),
        group_title=zh(r"\u9ed1\u8272256G"),
    )
    failures += assert_equal("market selection mode", market["selection"]["mode"], "latest_valid_day_only")
    failures += assert_equal("market fallback applied", market["selection"]["fallback_applied"], False)
    failures += assert_equal("market scope level", market["market_scope"]["scope_level"], "variant")

    capacity_rows = [
        make_row(merchant="A", group_title=zh(r"\u9ed1\u8272256G"), imported_at="2026-04-13 12:00:00", gprice="04-13", price_text="9600", row_id=20),
        make_row(merchant="B", group_title=zh(r"\u767d\u8272256G"), imported_at="2026-04-13 11:00:00", gprice="04-13", price_text="9620", row_id=21),
        make_row(merchant="C", group_title=zh(r"\u94f6\u8272512G"), imported_at="2026-04-13 10:30:00", gprice="04-13", price_text="11480", row_id=22),
        make_row(merchant="D", group_title=zh(r"\u661f\u5b87\u6a59\u8272512G"), imported_at="2026-04-13 10:00:00", gprice="04-13", price_text="11510", row_id=23),
    ]
    breakdown = build_capacity_market_breakdown(
        rows=capacity_rows,
        brand_title=zh(r"\u82f9\u679c"),
        series_title="iPhone 17",
        model_title=zh(r"17 Pro Max 6.9\u5bf8 \u56fd\u884c"),
    )
    failures += assert_equal("capacity breakdown order", [item["capacity_label"] for item in breakdown], ["256G", "512G"])
    failures += assert_equal("capacity aggregate scope", breakdown[0]["market_v1"]["market_scope"]["scope_level"], "capacity")
    failures += assert_equal("capacity aggregate row count", breakdown[0]["row_count"], 2)

    live_rows = [
        make_row(
            merchant="A",
            group_title=zh(r"\u9ed1\u8272256G"),
            imported_at="2026-04-13 12:00:00",
            gprice="04-13",
            price_text="9600",
            row_id=30,
        )
        | {"run_key": "demo_run"},
        make_row(
            merchant="B",
            group_title=zh(r"\u767d\u8272256G"),
            imported_at="2026-04-13 11:00:00",
            gprice="04-13",
            price_text="",
            row_id=31,
            brand_title=zh(r"\u534e\u4e3a"),
            series_title="Mate 80",
            model_title=zh(r"Mate80 Pro"),
        )
        | {"run_key": "demo_run"},
    ]
    payload = build_live_market_payload(FakeStorage("demo_run", live_rows), "demo_run")
    summary = dict(payload.get("summary") or {})
    counts = dict(summary.get("counts") or {})
    failures += assert_equal("live payload skipped candidate count", counts.get("skipped_candidates"), 1)
    failures += assert_equal("live payload skipped reason", dict(summary.get("skipped_reason_counts") or {}).get("no_numeric_rows"), 1)
    failures += assert_equal("live payload kept snapshot count", counts.get("published_snapshots"), 1)

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Run search and core semantic regressions against the local query dataset.")
    parser.add_argument("--db", type=Path, default=server.DEFAULT_DB_PATH, help="Path to dgteam.db")
    parser.add_argument("--limit", type=int, default=6, help="Search result limit")
    parser.add_argument("--skip-search", action="store_true", help="Skip search relevance checks")
    parser.add_argument("--skip-core", action="store_true", help="Skip core semantic checks")
    args = parser.parse_args()

    failures = 0
    if not args.skip_search:
        app = server.QueryApp(args.db)
        failures += run_search_cases(app, DEFAULT_SEARCH_CASES, limit=max(1, min(args.limit, 10)))
    if not args.skip_core:
        failures += run_core_semantics()

    print(f"\nTotal failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
