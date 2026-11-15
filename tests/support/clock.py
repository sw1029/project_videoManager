"""테스트가 직접 움직이는 시계 (리스 만료 같은 시간 경과를 결정적으로 재현한다)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from frame.application.ports.common import Clock

START = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)


class ManualClock(Clock):
    """now() 는 advance() 하기 전까지 같은 시각을 돌려준다 (FixedClock 처럼 저절로 가지 않는다)."""

    def __init__(self, start: datetime = START) -> None:
        self.current = start

    def now(self) -> datetime:
        return self.current

    def advance(self, **delta: float) -> datetime:
        self.current += timedelta(**delta)
        return self.current
