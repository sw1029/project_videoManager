"""작업을 쌓아 두었다가 테스트가 원하는 시점에 실행하는 디스패처.

'접수 직후 QUEUED 인가' 와 '실행 후 COMPLETED 인가' 를 한 테스트에서 각각 단언할 수 있다.
"""

from __future__ import annotations

from frame.application.ports.jobs import Job, JobDispatcher, JobHandlerRegistry


class RecordingJobDispatcher(JobDispatcher):
    def __init__(self, handlers: JobHandlerRegistry) -> None:
        self._handlers = handlers
        self.pending: list[Job] = []
        self.dispatched: list[Job] = []

    def dispatch(self, job: Job) -> None:
        self.dispatched.append(job)
        self.pending.append(job)

    def run_next(self) -> Job:
        job = self.pending.pop(0)
        self._handlers.handle(job)
        return job

    def run_all(self) -> int:
        count = 0
        while self.pending:
            self.run_next()
            count += 1
        return count
