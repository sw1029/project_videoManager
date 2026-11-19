"""객체 추적 (ObjectTracker 포트, US-03): 영상에서 객체의 위치를 따라간다.

밖에서 쓰는 이름
    FakeObjectTracker      결정적 가짜 구현 (장면 구간 전체에 걸친 박스)
    OpenCvObjectTracker    프레임마다 따라가는 박스 (FRAME_TRACKER=opencv, ADR-0036). tracking extra 의 OpenCV 가
                           있어야 만들어진다 — 모듈을 import 하는 것만으로는 cv2 를 불러오지 않는다
    EntityLocator          추적기가 시작 위치를 찾고 짚은 박스를 확인하는 방법 (추적기 안의 의존성)
    VlmEntityLocator       그 방법의 VLM 구현

모듈 지도
    fake      가짜 추적기
    opencv    OpenCV 추적기 (클립 읽기 · 찾는 시각마다 VLM · 새로 시작 / 다시 맞추기 / 끝내기 · 따라가기)
    locator   시작 위치 찾기와 박스 확인 (EntityLocator · VlmEntityLocator)
    _locate_question   그 둘이 VLM 에 묻는 글과 응답 형식
    _frames   픽셀 <-> 비율 박스 · 줄이기 · 잘라 내기 · JPEG · 겹침(IoU)
    _records  결과 기록 (TrackRecord 와 근거 · 배경 · 실패)
"""

from .fake import FakeObjectTracker
from .locator import EntityLocator, VlmEntityLocator
from .opencv import OpenCvObjectTracker

__all__ = ["EntityLocator", "FakeObjectTracker", "OpenCvObjectTracker", "VlmEntityLocator"]
