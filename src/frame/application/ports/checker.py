"""검수 포트 (US-03/US-06). CheckType 별로 Strategy 를 하나씩 구현하고 CheckSuite 가 묶는다.

구현 후보
- REQUIREMENT : VLM/CLIP 기반 속성 판정, 색상 히스토그램 판정
- CONSISTENCY : 추적 박스 영역의 색상/형상 특징 시계열 비교 (cosine 유사도)
- PRESERVATION: 부모 클립과의 프레임 차분/SSIM (비대상 영역)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from frame.domain.artifacts import GeneratedClip
from frame.domain.checks import WarningInterval
from frame.domain.enums import CheckType, Verdict
from frame.domain.ir import IntermediateRepresentation
from frame.domain.models import VideoVersion
from frame.domain.tracking import TrackRecord


@dataclass
class CheckContext:
    version: VideoVersion
    ir: IntermediateRepresentation
    clips: Mapping[str, GeneratedClip]
    tracks: Sequence[TrackRecord]
    parent: VideoVersion | None = None
    parent_ir: IntermediateRepresentation | None = None
    parent_clips: Mapping[str, GeneratedClip] = field(default_factory=dict)
    changed_scene_keys: frozenset[str] = frozenset()
    reused_scene_keys: frozenset[str] = frozenset()

    @property
    def is_revision(self) -> bool:
        return self.parent is not None


@dataclass(frozen=True)
class CheckOutcome:
    check_type: CheckType
    verdict: Verdict
    score: float | None = None
    requirement_key: str | None = None
    entity_key: str | None = None
    scene_key: str | None = None
    warnings: tuple[WarningInterval, ...] = ()
    detail: Mapping[str, Any] = field(default_factory=dict)


class Checker(ABC):
    check_type: CheckType

    @abstractmethod
    def check(self, ctx: CheckContext) -> Sequence[CheckOutcome]:
        """판정할 수 없으면 FAIL 이 아니라 UNDETERMINED 를 돌려준다 (미판정은 통과로 집계되지 않음)."""
