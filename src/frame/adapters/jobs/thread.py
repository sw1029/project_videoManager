"""스레드 풀 디스패처 (단일 서버 데모·개발용). 다중 워커가 필요하면 Celery/RQ 어댑터로 교체한다.

- 핸들러의 예외는 워커를 죽이지 않는다. 기록만 남긴다 (버전의 실패 기록은 유스케이스가 한다).
- 끝난 작업은 쥐고 있지 않는다. futures 에는 아직 끝나지 않은 작업만 있다.
- 서버를 끝낼 때 shutdown() 을 부르면 받아 둔 작업이 끝나기를 기다린다.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor

from frame.application.ports.jobs import Job, JobDispatcher, JobHandlerRegistry

log = logging.getLogger(__name__)


class ThreadPoolJobDispatcher(JobDispatcher):
    def __init__(self, handlers: JobHandlerRegistry, max_workers: int = 2) -> None:
        self._handlers = handlers
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="frame-job")
        self._lock = threading.Lock()
        self.futures: list[Future[None]] = []  # 아직 끝나지 않은 작업

    def dispatch(self, job: Job) -> None:
        future = self._executor.submit(self._run, job)
        with self._lock:
            self.futures.append(future)
        # 끝나면 목록에서 뺀다. 이미 끝났으면 바로 불린다
        future.add_done_callback(self._forget)

    def _run(self, job: Job) -> None:
        try:
            self._handlers.handle(job)
        except Exception:  # noqa: BLE001 - 워커 스레드는 죽지 않고 기록만 남긴다
            log.exception("job failed: %s", job.name)

    def _forget(self, future: Future[None]) -> None:
        with self._lock, contextlib.suppress(ValueError):
            self.futures.remove(future)

    def wait(self) -> None:
        """지금까지 받은 작업이 모두 끝날 때까지 기다린다 (테스트·종료 정리용)."""
        with self._lock:
            pending = list(self.futures)
        for future in pending:
            future.result()

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True)
