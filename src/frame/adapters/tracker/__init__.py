"""객체 추적 (ObjectTracker 포트, US-03): 영상에서 객체의 위치를 따라간다.

밖에서 쓰는 이름
    FakeObjectTracker      결정적 가짜 구현 (장면 구간 전체에 걸친 박스)
    OpenCvObjectTracker    뼈대 - 구현이 아직 없다. FRAME_TRACKER=opencv 는 기동에서 거절한다

모듈 지도
    fake · opencv_stub    구현 하나가 모듈 하나
"""

from .fake import FakeObjectTracker
from .opencv_stub import OpenCvObjectTracker

__all__ = ["FakeObjectTracker", "OpenCvObjectTracker"]
