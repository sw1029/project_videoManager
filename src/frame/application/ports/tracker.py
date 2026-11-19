"""객체 추적 포트 (US-03, E3). 구현: OpenCV 트래커(ADR-0036). 후보: YOLO+ByteTrack, SAM2, Grounding DINO."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from frame.domain.artifacts import GeneratedClip
from frame.domain.ir import EntitySpec, SceneSpec
from frame.domain.tracking import TrackRecord


@dataclass(frozen=True)
class TrackingRequest:
    version_id: str
    scene: SceneSpec
    clip: GeneratedClip
    entity_keys: tuple[str, ...]
    # 찾을 객체의 종류·외형 설명 (entity_keys 순서). 화면에서 객체를 찾는 추적기가 쓴다. 비면 키만으로 찾는다
    entities: tuple[EntitySpec, ...] = ()


class ObjectTracker(ABC):
    """계약
    - 요청한 모든 entity_key 에 대해 TrackRecord 를 하나씩 돌려준다 (추적 실패는 status=FAILED).
    - segment 의 시간은 장면 구간 안에 있어야 한다. 박스의 t_ms 는 영상 타임라인의 시각이다
      (장면의 planned_start_ms 부터) — 재생 중인 영상의 시각으로 바로 찾아 그릴 수 있게.
    - 실행 자체가 불가능하면 TrackingError 를 던진다 (파이프라인 FAILED).
    """

    @abstractmethod
    def track(self, request: TrackingRequest) -> tuple[TrackRecord, ...]: ...
