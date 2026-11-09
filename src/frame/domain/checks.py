"""검수 결과 (CHECK_RESULT) 와 경고 구간."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .enums import CheckType, Verdict


@dataclass(frozen=True)
class WarningInterval:
    """일관성이 깨진 타임라인 구간 (US-03 AC-33 경고 마커)."""

    scene_key: str
    start_ms: int
    end_ms: int
    message: str


@dataclass
class CheckResult:
    check_id: str
    version_id: str
    check_type: CheckType
    verdict: Verdict
    score: float | None = None
    requirement_key: str | None = None  # REQUIREMENT 검수일 때 (같은 버전의 요구조건이어야 함)
    entity_key: str | None = None
    scene_key: str | None = None
    warnings: tuple[WarningInterval, ...] = ()
    detail: dict[str, Any] = field(default_factory=dict)
