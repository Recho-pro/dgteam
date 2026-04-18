from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass(slots=True)
class QuoteDecision:
    keep: bool
    reason: str
    scope: str
    note: str = ""
    matched_positive_tags: List[str] = field(default_factory=list)
    matched_negative_tags: List[str] = field(default_factory=list)
    matched_sale_tags: List[str] = field(default_factory=list)
    condition_bucket: str = ""
    is_target_price: bool = False
    needs_review: bool = False

    def to_clean_fields(self) -> Dict[str, str]:
        return {
            "clean_scope": self.scope,
            "condition_bucket": self.condition_bucket,
            "is_target_price": "1" if self.is_target_price else "0",
            "needs_review": "1" if self.needs_review else "0",
            "matched_positive_tags": "|".join(self.matched_positive_tags),
            "matched_negative_tags": "|".join(self.matched_negative_tags),
            "matched_sale_tags": "|".join(self.matched_sale_tags),
            "exclude_reason": self.reason,
            "rule_note": self.note,
        }


@dataclass(slots=True)
class ImportResult:
    run_key: str
    task_count: int = 0
    quote_row_count: int = 0
    blacklist_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_key": self.run_key,
            "task_count": self.task_count,
            "quote_row_count": self.quote_row_count,
            "blacklist_count": self.blacklist_count,
        }
