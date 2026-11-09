"""객체 추적 포트 (US-03, E3). 구현 후보: OpenCV 트래커, YOLO+ByteTrack, SAM2, Grounding DINO."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from frame.domain.artifacts import GeneratedClip
from frame.domain.ir import SceneSpec
from frame.domain.tracking import TrackRecord


@dataclass(frozen=True)
class TrackingRequest:
    version_id: str
    scene: SceneSpec
    clip: GeneratedClip
    entity_keys: tuple[str, ...]


class ObjectTracker(ABC):
    """계약
    - 요청한 모든 entity_key 에 대해 TrackRecord 를 하나씩 돌려준다 (추적 실패는 status=FAILED).
    - segment 의 시간은 장면 구간 안에 있어야 한다.
    - 실행 자체가 불가능하면 TrackingError 를 던진다 (파이프라인 FAILED).
    """

    @abstractmethod
    def track(self, request: TrackingRequest) -> tuple[TrackRecord, ...]: ...
