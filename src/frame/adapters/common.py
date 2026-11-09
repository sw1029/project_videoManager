"""ID 생성기·시계 구현. 테스트에서는 결정적 구현(Sequential/Fixed)을 쓴다."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from frame.application.ports.common import Clock, IdGenerator


class UuidIdGenerator(IdGenerator):
    def new_id(self, prefix: str = "") -> str:
        token = uuid.uuid4().hex
        return f"{prefix}_{token}" if prefix else token


class SequentialIdGenerator(IdGenerator):
    """prj_0001, ver_0001 ... 처럼 예측 가능한 ID 를 만든다 (테스트 단언에 유용)."""

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}

    def new_id(self, prefix: str = "") -> str:
        n = self._counters.get(prefix, 0) + 1
        self._counters[prefix] = n
        return f"{prefix or 'id'}_{n:04d}"


class SystemClock(Clock):
    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock(Clock):
    """호출할 때마다 step 만큼 전진하는 시계 (정렬 순서가 결정적이 된다)."""

    def __init__(self, start: datetime | None = None, step_seconds: float = 1.0) -> None:
        self._current = start or datetime(2026, 1, 1, tzinfo=UTC)
        self._step = timedelta(seconds=step_seconds)

    def now(self) -> datetime:
        current = self._current
        self._current = current + self._step
        return current
