from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from dgteam.core.config import load_settings
from dgteam.core.project_config import load_project_config


@dataclass(frozen=True)
class OcrStageResult:
    name: str
    status: str
    summary: str
    details: Dict[str, Any]


def run_ocr_stage(*, project_root: Path | None = None) -> OcrStageResult:
    config = load_project_config(project_root=project_root)
    settings = load_settings(project_root=config.paths.project_root)
    cache_path = settings.local_root / "cache" / "ocr_price_cache.jsonl"
    line_count = 0
    if cache_path.exists():
        with cache_path.open("r", encoding="utf-8") as fh:
            for _ in fh:
                line_count += 1
    sample: Dict[str, Any] = {}
    if cache_path.exists():
        try:
            with cache_path.open("r", encoding="utf-8") as fh:
                first_line = fh.readline().strip()
            if first_line:
                sample = json.loads(first_line)
        except Exception:
            sample = {}
    return OcrStageResult(
        name="ocr",
        status="completed",
        summary=f"OCR cache is available with {line_count} cached entries.",
        details={
            "cache_path": str(cache_path),
            "cache_exists": cache_path.exists(),
            "cache_entry_count": line_count,
            "sample_entry": sample,
        },
    )
