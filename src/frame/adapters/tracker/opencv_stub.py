"""OpenCV 기반 추적 어댑터 뼈대 (Sprint 2, US-03).

구현 메모
- 참조 이미지/첫 프레임에서 객체 초기 박스를 얻고(검출기 또는 사용자 지정), cv2 트래커(CSRT/KCF)
  또는 SAM2 로 프레임을 따라간다.
- 결과는 TrackRecord(segments=...) 와 track_uri(JSON 산출물) 로 돌려준다.
- 객체를 끝내 찾지 못하면 status=FAILED 로 '미판정' 근거를 남긴다 (예외는 실행 불가일 때만).
"""

from __future__ import annotations

from frame.application.errors import TrackingError
from frame.application.ports.tracker import ObjectTracker, TrackingRequest
from frame.domain.tracking import TrackRecord


class OpenCvObjectTracker(ObjectTracker):
    def __init__(self, algorithm: str = "CSRT") -> None:
        self.algorithm = algorithm

    def track(self, request: TrackingRequest) -> tuple[TrackRecord, ...]:
        raise TrackingError(
            f"OpenCvObjectTracker({self.algorithm}) 는 아직 구현되지 않았습니다. "
            "opencv-python(ml extra) 설치 후 cv2.legacy.Tracker* 로 구현하세요."
        )
