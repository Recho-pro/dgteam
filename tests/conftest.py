from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"

src_path = str(SRC_ROOT)
if src_path not in sys.path:
    # Keep pytest usable from a clean checkout without relying on shell PYTHONPATH state.
    sys.path.insert(0, src_path)
