import csv
from pathlib import Path

from dgteam.core.storage import load_blacklist_rows, normalize_blacklist_row, write_blacklist_rows


def test_normalize_blacklist_row_repairs_garbled_progress_note():
    suspicious_note = f"2026-04-15{'?' * 8}0{'?' * 8}"
    row = normalize_blacklist_row(
        {
            "enabled": "1",
            "model_id": "123",
            "brand_title": "苹果",
            "series_title": "iPhone 17",
            "model_title": "17 Pro Max",
            "reason": "zero_row_ok_from_progress",
            "source_batch": "2026-04-15",
            "note": suspicious_note,
        }
    )
    assert row["note"] == (
        "Imported from progress review on 2026-04-15: zero matching rows remained after filtering, "
        "so this model stays blacklisted."
    )


def test_load_and_write_blacklist_rows_persist_clean_notes(tmp_path: Path):
    target = tmp_path / "model_blacklist.csv"
    suspicious_note = "??/" + ("?" * 13)
    target.write_text(
        "enabled,model_id,brand_title,series_title,model_title,reason,source_batch,note\n"
        f"1,456,苹果,iPhone 17,17 Pro,final_code_3_from_progress,2026-04-09,{suspicious_note}\n",
        encoding="utf-8",
    )

    rows = load_blacklist_rows(target)
    assert rows[0]["note"] == (
        "Imported from progress review on 2026-04-09: task ended with code 3 and was intentionally "
        "added to the blacklist."
    )

    rewritten = tmp_path / "rewritten.csv"
    write_blacklist_rows(rewritten, rows)

    with rewritten.open("r", encoding="utf-8", newline="") as fh:
        saved = next(csv.DictReader(fh))
    assert "?" not in saved["note"]
