from __future__ import annotations

import ast
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from .textio import decode_external_text_bytes


PROJECT_TEXT_DIRS: tuple[str, ...] = ("src", "scripts", "tests", "web", "docs", "config", "rules")
PROJECT_ROOT_TEXT_FILES: tuple[str, ...] = (
    "README.md",
    "pyproject.toml",
    ".env.example",
    ".editorconfig",
    ".gitattributes",
    ".pre-commit-config.yaml",
    ".gitignore",
)
TEXT_FILE_SUFFIXES: frozenset[str] = frozenset(
    {
        ".py",
        ".md",
        ".txt",
        ".json",
        ".jsonl",
        ".toml",
        ".yml",
        ".yaml",
        ".ini",
        ".cfg",
        ".html",
        ".css",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".csv",
        ".ps1",
        ".bat",
        ".sh",
    }
)
SKIP_DIR_NAMES: frozenset[str] = frozenset(
    {"runtime", "dist", "build", ".git", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache"}
)
ALLOW_LITERAL_MOJIBAKE_FILES: frozenset[str] = frozenset({"encoding_guard.py"})
SUSPICIOUS_TOKENS: tuple[str, ...] = (
    "鑻规灉",
    "绾㈢背",
    "璇烽€夋嫨",
    "鍏ㄩ儴",
    "涓嶉檺",
    "瀹樻崲",
    "璧勬簮鏈",
    "婕旂ず",
)
QUESTION_BURST_THRESHOLD = 6


@dataclass(frozen=True)
class EncodingIssue:
    kind: str
    path: str
    message: str
    line: int = 0
    column: int = 0
    evidence: str = ""
    suggestion: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _looks_like_text_file(path: Path) -> bool:
    return path.suffix.lower() in TEXT_FILE_SUFFIXES or path.name in PROJECT_ROOT_TEXT_FILES


def _iter_candidate_files(project_root: Path, paths: Sequence[Path] | None = None) -> Iterable[Path]:
    root = Path(project_root).expanduser().resolve()
    if paths:
        for raw_path in paths:
            target = Path(raw_path)
            if not target.is_absolute():
                target = (root / target).resolve()
            if target.is_file() and _looks_like_text_file(target):
                yield target
        return

    seen: set[Path] = set()
    for name in PROJECT_ROOT_TEXT_FILES:
        path = root / name
        if path.exists() and path.is_file():
            seen.add(path)
            yield path
    for dirname in PROJECT_TEXT_DIRS:
        folder = root / dirname
        if not folder.exists():
            continue
        for path in folder.rglob("*"):
            if not path.is_file():
                continue
            if any(part in SKIP_DIR_NAMES for part in path.parts):
                continue
            if not _looks_like_text_file(path):
                continue
            if path in seen:
                continue
            seen.add(path)
            yield path


def _normalize_line_endings(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def attempt_mojibake_line_repair(text: str) -> str | None:
    if not any(token in text for token in SUSPICIOUS_TOKENS):
        return None
    try:
        repaired = text.encode("gb18030").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return None
    if repaired == text:
        return None
    return repaired


def _scan_text_content(path: Path, text: str) -> List[EncodingIssue]:
    issues: List[EncodingIssue] = []
    normalized = _normalize_line_endings(text)
    if path.name in ALLOW_LITERAL_MOJIBAKE_FILES:
        return issues
    if "\uFFFD" in normalized:
        issues.append(
            EncodingIssue(
                kind="replacement_char",
                path=str(path),
                message="File contains replacement characters, which usually means a decode failure already happened.",
                suggestion="Recover this file from a clean UTF-8 source or run the repair script in dry-run mode first.",
            )
        )
    for line_no, line in enumerate(normalized.split("\n"), start=1):
        if not line:
            continue
        if line.count("?") >= QUESTION_BURST_THRESHOLD and any("\u4e00" <= ch <= "\u9fff" for ch in line):
            issues.append(
                EncodingIssue(
                    kind="question_burst",
                    path=str(path),
                    line=line_no,
                    message="File contains a suspicious burst of question marks alongside Chinese text.",
                    evidence=line.strip(),
                    suggestion="Replace placeholder-like question marks with clean UTF-8 text before this file is reused.",
                )
            )
        for token in SUSPICIOUS_TOKENS:
            if token not in line:
                continue
            repaired = attempt_mojibake_line_repair(line)
            issues.append(
                EncodingIssue(
                    kind="mojibake_token",
                    path=str(path),
                    line=line_no,
                    message="File contains a suspicious mojibake token.",
                    evidence=line.strip(),
                    suggestion=repaired.strip() if repaired else "Inspect and restore the intended UTF-8 text.",
                )
            )
            break
    return issues


def _encoding_keyword_present(call: ast.Call) -> bool:
    return any(keyword.arg == "encoding" for keyword in call.keywords if keyword.arg)


def _call_mode_value(call: ast.Call) -> str:
    if len(call.args) >= 2 and isinstance(call.args[1], ast.Constant) and isinstance(call.args[1].value, str):
        return call.args[1].value
    for keyword in call.keywords:
        if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
            return keyword.value.value
    return "r"


def _is_binary_mode(mode: str) -> bool:
    return "b" in str(mode or "")


def _looks_like_path_target(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        lowered = node.id.lower()
        return lowered.endswith(("path", "file", "filepath", "filename", "target"))
    if isinstance(node, ast.Attribute):
        lowered = node.attr.lower()
        return lowered.endswith(("path", "file", "filepath", "filename", "target"))
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name) and func.id == "Path":
            return True
        if isinstance(func, ast.Attribute) and func.attr in {"resolve", "expanduser", "with_suffix", "with_name"}:
            return _looks_like_path_target(func.value)
    return False


def _scan_python_source(path: Path, text: str) -> List[EncodingIssue]:
    issues: List[EncodingIssue] = []
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        issues.append(
            EncodingIssue(
                kind="syntax_error",
                path=str(path),
                line=int(exc.lineno or 0),
                column=int(exc.offset or 0),
                message=f"Python file could not be parsed: {exc.msg}",
                evidence=(exc.text or "").strip(),
            )
        )
        return issues

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "open":
            mode = _call_mode_value(node)
            if not _is_binary_mode(mode) and not _encoding_keyword_present(node):
                issues.append(
                    EncodingIssue(
                        kind="missing_encoding",
                        path=str(path),
                        line=int(getattr(node, "lineno", 0) or 0),
                        column=int(getattr(node, "col_offset", 0) or 0) + 1,
                        message="Built-in open() is used without an explicit encoding in text mode.",
                        suggestion='Add encoding="utf-8" or route the call through dgteam.core.textio.',
                    )
                )
        if isinstance(func, ast.Attribute):
            if func.attr == "open" and _looks_like_path_target(func.value):
                mode = _call_mode_value(node)
                if not _is_binary_mode(mode) and not _encoding_keyword_present(node):
                    issues.append(
                        EncodingIssue(
                            kind="missing_encoding",
                            path=str(path),
                            line=int(getattr(node, "lineno", 0) or 0),
                            column=int(getattr(node, "col_offset", 0) or 0) + 1,
                            message="Path.open() is used without an explicit encoding in text mode.",
                            suggestion='Add encoding="utf-8" or route the call through dgteam.core.textio.',
                        )
                    )
            if func.attr in {"read_text", "write_text"} and not _encoding_keyword_present(node):
                issues.append(
                    EncodingIssue(
                        kind="missing_encoding",
                        path=str(path),
                        line=int(getattr(node, "lineno", 0) or 0),
                        column=int(getattr(node, "col_offset", 0) or 0) + 1,
                        message=f"Path.{func.attr}() is used without an explicit encoding.",
                        suggestion='Add encoding="utf-8" or route the call through dgteam.core.textio.',
                    )
                )
    return issues


def scan_file(path: Path) -> List[EncodingIssue]:
    target = Path(path).expanduser().resolve()
    issues: List[EncodingIssue] = []
    raw = target.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        repair_hint = ""
        try:
            guessed = decode_external_text_bytes(raw, source=str(target))
            repair_hint = f"File can be normalized to UTF-8 by decoding as {guessed.encoding} and rewriting it."
        except UnicodeDecodeError:
            repair_hint = "This file is not valid UTF-8 and could not be repaired automatically."
        issues.append(
            EncodingIssue(
                kind="non_utf8_file",
                path=str(target),
                line=int(exc.start or 0),
                message="File is not valid UTF-8.",
                suggestion=repair_hint,
            )
        )
        return issues

    issues.extend(_scan_text_content(target, text))
    if target.suffix.lower() == ".py":
        issues.extend(_scan_python_source(target, text))
    return issues


def scan_project_tree(project_root: Path, paths: Sequence[Path] | None = None) -> List[EncodingIssue]:
    issues: List[EncodingIssue] = []
    for path in _iter_candidate_files(project_root, paths=paths):
        issues.extend(scan_file(path))
    return issues


def summarize_issues(issues: Sequence[EncodingIssue], *, limit: int = 20) -> str:
    if not issues:
        return "No encoding issues found."
    lines = [f"Detected {len(issues)} encoding issue(s):"]
    for issue in list(issues)[:limit]:
        location = issue.path
        if issue.line:
            location += f":{issue.line}"
            if issue.column:
                location += f":{issue.column}"
        lines.append(f"- [{issue.kind}] {location} -> {issue.message}")
        if issue.evidence:
            lines.append(f"  evidence: {issue.evidence}")
        if issue.suggestion:
            lines.append(f"  fix: {issue.suggestion}")
    if len(issues) > limit:
        lines.append(f"... {len(issues) - limit} more issue(s) omitted")
    return "\n".join(lines)


def assert_project_encoding_clean(project_root: Path, paths: Sequence[Path] | None = None) -> None:
    issues = scan_project_tree(project_root, paths=paths)
    if issues:
        raise ValueError(summarize_issues(issues))
