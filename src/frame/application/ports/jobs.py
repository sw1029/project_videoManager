"""비동기 작업 포트 (Command 패턴). 구현: inline(동기) / recording(테스트) / thread / celery(향후)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Job:
    name: str
    payload: Mapping[str, Any] = field(default_factory=dict)


# 핸들러가 돌려주는 값은 쓰지 않는다 (유스케이스의 실행 메서드를 그대로 올릴 수 있다)
JobHandler = Callable[[Job], object]


class JobHandlerRegistry:
    """작업 이름 -> 처리 함수. 디스패처 구현은 이 레지스트리만 알면 된다."""

    def __init__(self) -> None:
        self._handlers: dict[str, JobHandler] = {}

    def register(self, name: str, handler: JobHandler) -> None:
        self._handlers[name] = handler

    def handle(self, job: Job) -> None:
        try:
            handler = self._handlers[job.name]
        except KeyError as exc:
            raise LookupError(f"등록되지 않은 작업: {job.name}") from exc
        handler(job)

    def names(self) -> list[str]:
        return sorted(self._handlers)


class JobDispatcher(ABC):
    @abstractmethod
    def dispatch(self, job: Job) -> None: ...
