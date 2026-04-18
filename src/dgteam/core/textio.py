from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence


UTF8_READ_ENCODINGS: tuple[str, ...] = ("utf-8", "utf-8-sig")
EXTERNAL_TEXT_ENCODINGS: tuple[str, ...] = ("utf-8", "utf-8-sig", "gb18030")


@dataclass(frozen=True)
class TextDecodeResult:
    text: str
    encoding: str
    source: str = ""


def ensure_parent_dir(path: Path) -> Path:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def read_text_utf8(path: Path, *, allow_bom: bool = True) -> str:
    target = Path(path).expanduser().resolve()
    encodings: Sequence[str] = UTF8_READ_ENCODINGS if allow_bom else ("utf-8",)
    last_error: UnicodeDecodeError | None = None
    for encoding in encodings:
        try:
            return target.read_text(encoding=encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
    if last_error is not None:
        raise last_error
    return target.read_text(encoding="utf-8")


def write_text_utf8(path: Path, text: str) -> Path:
    target = ensure_parent_dir(path)
    target.write_text(str(text), encoding="utf-8")
    return target


def read_json_utf8(path: Path, *, allow_bom: bool = True) -> Any:
    return json.loads(read_text_utf8(path, allow_bom=allow_bom))


def write_json_utf8(path: Path, payload: Any, *, indent: int = 2) -> Path:
    return write_text_utf8(path, json.dumps(payload, ensure_ascii=False, indent=indent))


def read_jsonl_utf8(path: Path, *, allow_bom: bool = False) -> Iterator[Any]:
    for raw_line in read_text_utf8(path, allow_bom=allow_bom).splitlines():
        line = raw_line.strip()
        if not line:
            continue
        yield json.loads(line)


def decode_external_text_bytes(raw: bytes, *, source: str = "") -> TextDecodeResult:
    last_error: UnicodeDecodeError | None = None
    for encoding in EXTERNAL_TEXT_ENCODINGS:
        try:
            return TextDecodeResult(text=raw.decode(encoding), encoding=encoding, source=source)
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
    if last_error is not None:
        raise last_error
    raise UnicodeDecodeError("utf-8", raw, 0, 1, "Unable to decode external text bytes.")


def read_external_text(path: Path) -> TextDecodeResult:
    target = Path(path).expanduser().resolve()
    return decode_external_text_bytes(target.read_bytes(), source=str(target))


def iter_text_lines_utf8(path: Path, *, allow_bom: bool = False) -> Iterable[str]:
    return read_text_utf8(path, allow_bom=allow_bom).splitlines()
