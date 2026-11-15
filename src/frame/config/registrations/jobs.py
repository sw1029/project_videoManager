"""작업 디스패처 (JobDispatcher) 의 등록."""

from __future__ import annotations

from frame.adapters.jobs import InlineJobDispatcher, RecordingJobDispatcher, ThreadPoolJobDispatcher
from frame.application.ports import JobDispatcher

from ..registry import AdapterRegistry
from ._dependencies import job_handlers


def register(registry: AdapterRegistry) -> None:
    registry.register(JobDispatcher, "inline", lambda ctx: InlineJobDispatcher(job_handlers(ctx)))
    registry.register(
        JobDispatcher, "recording", lambda ctx: RecordingJobDispatcher(job_handlers(ctx))
    )
    registry.register(
        JobDispatcher, "thread", lambda ctx: ThreadPoolJobDispatcher(job_handlers(ctx))
    )
