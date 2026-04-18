from pathlib import Path

from dgteam.core.encoding_guard import attempt_mojibake_line_repair, scan_project_tree


def test_guard_flags_mojibake_tokens(tmp_path: Path):
    source = tmp_path / "src"
    source.mkdir(parents=True, exist_ok=True)
    target = source / "sample.py"
    bad_literal = "苹果".encode("utf-8").decode("gb18030")
    target.write_text(f'value = "{bad_literal}"\n', encoding="utf-8")

    issues = scan_project_tree(tmp_path)
    assert any(issue.kind == "mojibake_token" for issue in issues)


def test_guard_flags_missing_encoding_on_read_text(tmp_path: Path):
    source = tmp_path / "src"
    source.mkdir(parents=True, exist_ok=True)
    target = source / "sample.py"
    target.write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "value = Path('demo.txt').read_text()",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    issues = scan_project_tree(tmp_path)
    assert any(issue.kind == "missing_encoding" for issue in issues)


def test_attempt_mojibake_line_repair_recovers_known_text():
    bad_literal = "苹果".encode("utf-8").decode("gb18030")
    repaired = attempt_mojibake_line_repair(f'status = "{bad_literal}"\n')
    assert repaired == 'status = "苹果"\n'


def test_guard_flags_question_burst_in_csv(tmp_path: Path):
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    target = config_dir / "sample.csv"
    suspicious_note = f"2026-04-15{'?' * 8}0{'?' * 8}"
    target.write_text(
        "enabled,model_id,brand_title,series_title,model_title,reason,source_batch,note\n"
        f"1,100,苹果,iPhone 17,17 Pro Max,zero_row_ok_from_progress,2026-04-15,{suspicious_note}\n",
        encoding="utf-8",
    )

    issues = scan_project_tree(tmp_path)
    assert any(issue.kind == "question_burst" for issue in issues)
