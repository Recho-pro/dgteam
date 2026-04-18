"""WeChat Official Account integration package for DGTEAM."""

from .client import WechatOfficialApiError, WechatOfficialClient
from .fast_lane import WechatOfficialImageFastLane
from .image_recognizer import WechatOfficialEcommerceImageRecognizer
from .ingress import WechatOfficialDecodedCallback, WechatOfficialIngress, parse_xml
from .menu import build_default_menu
from .models import (
    WechatOfficialFastLaneOutcome,
    WechatOfficialImageCandidateResolution,
    WechatOfficialInboundMessage,
    WechatOfficialMarketReplyPlan,
    WechatOfficialRecognitionResult,
    WechatOfficialRecognitionTask,
    WechatOfficialSessionState,
)
from .recognition_queue import WechatOfficialRecognitionQueue
from .recognition_worker import (
    NullWechatOfficialImageRecognizer,
    WechatOfficialRecognitionResultHandler,
    WechatOfficialRecognitionWorker,
)
from .result_dispatcher import WechatOfficialImageResultDispatcher
from .response_layer import WechatOfficialMarketResponseLayer
from .session_store import WechatOfficialSessionStore
from .service import WechatOfficialService
from .workflow import WechatOfficialWorkflow


def build_app():
    from .app import build_app as _build_app

    return _build_app()


def run_worker():
    from .worker_cli import main as _run_worker

    return _run_worker()

__all__ = [
    "WechatOfficialApiError",
    "WechatOfficialClient",
    "WechatOfficialDecodedCallback",
    "WechatOfficialEcommerceImageRecognizer",
    "WechatOfficialFastLaneOutcome",
    "WechatOfficialImageCandidateResolution",
    "WechatOfficialImageFastLane",
    "WechatOfficialIngress",
    "WechatOfficialInboundMessage",
    "WechatOfficialMarketReplyPlan",
    "WechatOfficialRecognitionQueue",
    "WechatOfficialRecognitionResult",
    "WechatOfficialRecognitionTask",
    "WechatOfficialRecognitionResultHandler",
    "WechatOfficialRecognitionWorker",
    "WechatOfficialSessionState",
    "WechatOfficialSessionStore",
    "WechatOfficialService",
    "WechatOfficialImageResultDispatcher",
    "WechatOfficialMarketResponseLayer",
    "WechatOfficialWorkflow",
    "NullWechatOfficialImageRecognizer",
    "run_worker",
    "build_default_menu",
    "build_app",
    "parse_xml",
]
