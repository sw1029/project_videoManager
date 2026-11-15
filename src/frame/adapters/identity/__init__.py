"""정체성 기준 이미지 뽑기 (IdentityReferenceExtractor 포트, ADR-0015).

맡는 개념
    만든 장면 클립에서 객체의 모습을 뽑아 정체성 기준 이미지로 저장한다. 다음 장면을 만들 때 이 이미지를 함께 넘겨
    같은 객체가 같은 모습으로 나오게 한다.

밖에서 쓰는 이름
    VlmCropIdentityExtractor    VLM 이 짚은 객체의 자리만 잘라 낸다 (FRAME_IDENTITY=vlm_crop)
    FrameIdentityExtractor      프레임 한 장을 통째로 쓴다 (FRAME_IDENTITY=frame)
    FakeIdentityExtractor       결정적 가짜 구현

모듈 지도
    vlm_crop          잘라 내기 추출기의 흐름: 프레임 뽑기 -> 묻기 -> 자르기 -> 색인
    _crop_question    잘라 내기 추출기가 모델에 묻는 것(지시문 · 응답 형식)과 답 읽기
    frame             프레임 추출기
    fake              가짜 추출기
    keys              기준 이미지와 색인의 저장 키
"""

from .fake import FakeIdentityExtractor
from .frame import FrameIdentityExtractor
from .vlm_crop import VlmCropIdentityExtractor

__all__ = ["FakeIdentityExtractor", "FrameIdentityExtractor", "VlmCropIdentityExtractor"]
