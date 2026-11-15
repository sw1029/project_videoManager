"""파이프라인 진행 상황 값 객체 (이슈 #3).

버전에 저장되어, 처리 중에는 '지금 어느 단계의 몇 번째 장면인가', 실패 뒤에는 '어느 단계까지 무엇을 했는가' 를
보여 준다. 시작한 단계만 순서대로 담는다 (아직 시작하지 않은 단계는 없다). 값은 바뀌지 않고 갱신은 새 객체를 돌려준다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any


class StageStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class StageProgress:
    """한 단계의 진행. done/total 은 항목(장면) 단위이며, total=0 이면 항목 단위 진행이 없는 단계다."""

    stage: str
    status: StageStatus
    elapsed_ms: int = 0  # 단계 시작부터 마지막 기록까지 (끝났으면 단계 소요 시간)
    done: int = 0
    total: int = 0
    current: str | None = None  # 처리 중인 항목(장면 키). 실패했으면 실패한 항목

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "status": str(self.status),
            "elapsed_ms": self.elapsed_ms,
            "done": self.done,
            "total": self.total,
            "current": self.current,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> StageProgress:
        return cls(
            stage=str(data["stage"]),
            status=StageStatus(data["status"]),
            elapsed_ms=int(data.get("elapsed_ms", 0)),
            done=int(data.get("done", 0)),
            total=int(data.get("total", 0)),
            current=data.get("current"),
        )


@dataclass(frozen=True)
class PipelineProgress:
    stages: tuple[StageProgress, ...] = ()

    @property
    def current(self) -> StageProgress | None:
        """마지막으로 시작한 단계 (처리 중이거나, 끝났거나, 실패한 단계)."""
        return self.stages[-1] if self.stages else None

    @property
    def elapsed_ms(self) -> int:
        return sum(s.elapsed_ms for s in self.stages)

    def stage(self, name: str) -> StageProgress | None:
        return next((s for s in self.stages if s.stage == name), None)

    # ---- 갱신 (새 객체) ----------------------------------------------------
    def started(self, stage: str) -> PipelineProgress:
        return PipelineProgress((*self.stages, StageProgress(stage, StageStatus.RUNNING)))

    def advanced(
        self, *, done: int, total: int, current: str | None, elapsed_ms: float
    ) -> PipelineProgress:
        return self._update_running(
            done=done, total=total, current=current, elapsed_ms=_ms(elapsed_ms)
        )

    def finished(self, elapsed_ms: float) -> PipelineProgress:
        running = self._running()
        if running is None:
            return self
        return self._update_running(
            status=StageStatus.COMPLETED,
            elapsed_ms=_ms(elapsed_ms),
            done=running.total,
            current=None,
        )

    def failed(self, elapsed_ms: float) -> PipelineProgress:
        return self._update_running(status=StageStatus.FAILED, elapsed_ms=_ms(elapsed_ms))

    def _running(self) -> StageProgress | None:
        current = self.current
        return current if current is not None and current.status is StageStatus.RUNNING else None

    def _update_running(self, **changes: Any) -> PipelineProgress:
        running = self._running()
        if running is None:  # 진행 중인 단계가 없으면 바꿀 것이 없다
            return self
        return PipelineProgress((*self.stages[:-1], replace(running, **changes)))

    # ---- 직렬화 ------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {"stages": [s.to_dict() for s in self.stages]}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PipelineProgress:
        return cls(tuple(StageProgress.from_dict(s) for s in data.get("stages", [])))


# 반올림하면 1000ms 가 되는 값부터는 초로 적는다 (999.5ms 를 '1000ms' 로 적지 않는다)
_SHOWN_IN_SECONDS_FROM_MS = 999.5


def format_elapsed_ms(ms: float) -> str:
    """사람이 읽는 소요 시간: 1초 미만은 ms, 그 이상은 소수 한 자리 초."""
    return f"{ms:.0f}ms" if ms < _SHOWN_IN_SECONDS_FROM_MS else f"{ms / 1000:.1f}s"


def _ms(value: float) -> int:
    return max(0, round(value))
