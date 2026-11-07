"""요청 스레드에서 즉시 실행하는 디스패처 (데모·단일 프로세스 개발용)."""

from __future__ import annotations

from frame.application.ports.jobs import Job, JobDispatcher, JobHandlerRegistry


class InlineJobDispatcher(JobDispatcher):
    def __init__(self, handlers: JobHandlerRegistry) -> None:
        self._handlers = handlers

    def dispatch(self, job: Job) -> None:
        self._handlers.handle(job)
