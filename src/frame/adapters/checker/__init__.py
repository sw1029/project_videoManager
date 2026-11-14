"""검수 (Checker 포트): 만든 영상이 요구를 지켰는지 판정한다.

맡는 개념
    검수 종류마다 구현이 하나씩 있다. 이미지를 읽는 모델(VLM)로 판정하는 것 셋과 화면 전체를 견주는 것 하나, 그리고
    테스트·시연용 가짜 셋이다. 검수기는 판정할 수 없으면 미판정(UNDETERMINED)을 돌려준다 - 통과로 세지 않는다.

밖에서 쓰는 이름
    VlmRequirementChecker     요구 반영 검수: 요구조건이 영상에 반영됐는가 (ADR-0018)
    VlmConsistencyChecker     일관성 검수: 객체가 장면 사이에서 같은 모습인가 (ADR-0015)
    VlmPreservationChecker    유지 요소 검수: 사용자가 고른 '유지할 요소' 가 그대로인가 (ADR-0018)
    SsimPreservationChecker   보존 검수: 다시 만든 장면이 부모 클립과 화면 전체로 얼마나 같은가 (ADR-0016)
    FakeRequirementChecker · FakeConsistencyChecker · FakePreservationChecker    결정적 가짜 구현

모듈 지도
    vlm_requirement · vlm_consistency · vlm_preservation    VLM 검수기 (프레임 표본 -> 질의 -> 판정).
        프레임은 shared/media 의 FrameSampler 로 뽑고, 질의는 shared/vlm 의 ask_json 으로 한다.
        검수기마다 다른 것은 질의 글 · 응답 형식 · 판정 규칙이다
    ssim_preservation                                       구조 유사도(SSIM)로 견주는 보존 검수기
    fake                                                    가짜 검수기 셋
"""

from .fake import FakeConsistencyChecker, FakePreservationChecker, FakeRequirementChecker
from .ssim_preservation import SsimPreservationChecker
from .vlm_consistency import VlmConsistencyChecker
from .vlm_preservation import VlmPreservationChecker
from .vlm_requirement import VlmRequirementChecker

__all__ = [
    "FakeConsistencyChecker",
    "FakePreservationChecker",
    "FakeRequirementChecker",
    "SsimPreservationChecker",
    "VlmConsistencyChecker",
    "VlmPreservationChecker",
    "VlmRequirementChecker",
]
