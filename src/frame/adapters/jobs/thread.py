"""스레드 풀 디스패처 (단일 서버 데모·개발용). 다중 워커가 필요하면 Celery/RQ 어댑터로 교체한다."""

from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor

from frame.application.ports.jobs import Job, JobDispatcher, JobHandlerRegistry

log = logging.getLogger(__name__)


class ThreadPoolJobDispatcher(JobDispatcher):
    def __init__(self, handlers: JobHandlerRegistry, max_workers: int = 2) -> None:
        self._handlers = handlers
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="frame-job")
        self.futures: list[Future[None]] = []

    def dispatch(self, job: Job) -> None:
        future = self._executor.submit(self._run, job)
        self.futures.append(future)

    def _run(self, job: Job) -> None:
        try:
            self._handlers.handle(job)
        except Exception:  # noqa: BLE001 - 워커 스레드는 죽지 않고 기록만 남긴다
            log.exception("job failed: %s", job.name)

    def wait(self) -> None:
        for future in list(self.futures):
            future.result()

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True)
