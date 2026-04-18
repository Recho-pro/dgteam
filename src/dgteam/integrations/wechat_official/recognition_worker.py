from __future__ import annotations

import logging
from typing import Protocol

from dgteam.integrations.wechat_official.models import (
    WechatOfficialRecognitionResult,
    WechatOfficialRecognitionTask,
)
from dgteam.integrations.wechat_official.recognition_queue import WechatOfficialRecognitionQueue


LOGGER = logging.getLogger("dgteam.wechat_official.recognition_worker")


class WechatOfficialImageRecognizer(Protocol):
    def recognize(self, task: WechatOfficialRecognitionTask) -> WechatOfficialRecognitionResult:
        ...


class WechatOfficialRecognitionResultHandler(Protocol):
    def handle(self, task: WechatOfficialRecognitionTask, result: WechatOfficialRecognitionResult) -> str:
        ...


class NullWechatOfficialImageRecognizer:
    def __init__(self, *, primary_model: str, fallback_model: str):
        self.primary_model = str(primary_model or "").strip()
        self.fallback_model = str(fallback_model or "").strip()

    def recognize(self, task: WechatOfficialRecognitionTask) -> WechatOfficialRecognitionResult:
        return WechatOfficialRecognitionResult(
            task_id=task.task_id,
            status="deferred",
            recognized_query="",
            confidence="unknown",
            model=self.primary_model,
            candidates=[],
            summary=(
                "Image recognition worker is scaffolded but AI recognition is not enabled yet. "
                f"Primary model reserved: {self.primary_model or 'unset'}, "
                f"fallback model reserved: {self.fallback_model or 'unset'}."
            ),
            raw_payload={},
        )


class WechatOfficialRecognitionWorker:
    def __init__(
        self,
        *,
        queue: WechatOfficialRecognitionQueue,
        recognizer: WechatOfficialImageRecognizer,
        result_handler: WechatOfficialRecognitionResultHandler | None = None,
    ):
        self.queue = queue
        self.recognizer = recognizer
        self.result_handler = result_handler

    def run_once(self) -> WechatOfficialRecognitionTask | None:
        task = self.queue.claim_next()
        if task is None:
            return None
        try:
            result = self.recognizer.recognize(task)
        except Exception as exc:
            LOGGER.exception("wechat official recognition worker failed task_id=%s", task.task_id)
            failure_result = WechatOfficialRecognitionResult(
                task_id=task.task_id,
                status="failed",
                recognized_query=str(task.query_hint or "").strip(),
                confidence="unknown",
                model="",
                candidates=[],
                summary=str(exc or "").strip() or "image recognition failed",
                raw_payload={"task_id": task.task_id, "worker_error": str(exc or "").strip()},
            )
            if self.result_handler is not None:
                self.result_handler.handle(task, failure_result)
            self.queue.fail(task, error=str(exc))
            raise
        if str(result.status or "").strip().lower() == "failed":
            if self.result_handler is not None:
                self.result_handler.handle(task, result)
            self.queue.fail(task, error=result.summary or "recognition failed")
            return task
        if self.result_handler is not None:
            self.result_handler.handle(task, result)
        self.queue.complete(task, result)
        return task
