"""Pipeline stages for the local DGTEAM agent."""

from .cleaner import CleanerStageResult, run_cleaner
from .collector import CollectorStageResult, run_collector
from .market_engine import MarketEngineStageResult, run_market_engine
from .ocr import OcrStageResult, run_ocr_stage
from .publisher import PublisherStageResult, run_publisher

__all__ = [
    "CleanerStageResult",
    "CollectorStageResult",
    "MarketEngineStageResult",
    "OcrStageResult",
    "PublisherStageResult",
    "run_cleaner",
    "run_collector",
    "run_market_engine",
    "run_ocr_stage",
    "run_publisher",
]
