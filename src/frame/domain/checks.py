"""검수 결과 (CHECK_RESULT) 와 경고 구간."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .enums import CheckType, Verdict

# PRESERVATION 결과의 detail["method"]: 그 결과를 무엇으로 쟀는가.
# 보존 검수기가 이 이름을 적고, 지표 1 평가(evaluation)가 기준별로 비대상 변경률을 낼 때 같은 이름으로 가른다
PRESERVATION_BY_KEEP = "vlm_keep"  # 유지 요소 판정 (요소 하나가 결과 하나, 그대로 1.0 · 달라짐 0.0)
PRESERVATION_BY_SSIM = "ssim"  # 부모 클립 대비 구조 유사도 (다시 만든 장면 하나가 결과 하나)
PRESERVATION_REUSED = "identical"  # 부모 클립을 그대로 쓴 장면 (재지 않는다. 어느 검수기든 1.0)


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
