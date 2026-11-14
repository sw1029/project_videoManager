"""테마 추출 포트 (ADR-0025): 분석 결과 + 영상 -> 테마·원본 IR·화면 문구.

구현: Fake, 프레임을 읽는 LLM. LLM 호출의 오류(LLMTimeout·LLMUnavailable·LLMRateLimited·LLMBadResponse)는
ThemeExtractionError 로 바꾸지 않고 그대로 올라온다 (부른 쪽이 종류를 가려 다시 시도할 수 있게).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field

from frame.application.errors import AdapterError
from frame.domain.ir import IntermediateRepresentation
from frame.domain.series.source_analysis import ScreenText, VideoAnalysis
from frame.domain.series.theme import DEFAULT_MIN_SLOT_MS, SourceVideo, Theme


class ThemeExtractionError(AdapterError):
    code = "THEME_EXTRACTION_ERROR"


@dataclass(frozen=True)
class ThemeExtractionRequest:
    theme_id: str
    source: SourceVideo
    video: bytes
    analysis: VideoAnalysis
    title: str = ""  # 사용자가 준 제목. 비어 있으면 추출기가 짓는다
    min_slot_ms: int = DEFAULT_MIN_SLOT_MS  # 다시 만들 수 있는 장면 자리의 길이 하한


@dataclass(frozen=True)
class ThemeExtraction:
    theme: Theme
    source_ir: IntermediateRepresentation  # 원본 영상의 IR. 장면은 테마의 자리와 같다
    screen_texts: tuple[ScreenText, ...] = ()  # 화면에서 읽은 문구 (문구 규칙의 근거)
    notes: tuple[str, ...] = ()  # 고치거나 버린 것
    detail: Mapping[str, object] = field(default_factory=dict)  # 방법·모델·토큰 수 등


class ThemeExtractor(ABC):
    """계약
    - theme.validate() 와 source_ir.validate() 를 통과한다.
    - theme.theme_id 는 요청의 theme_id, theme.source 는 요청의 source 그대로다.
    - 원본 IR 의 장면은 테마의 자리와 1:1 이다 (키·순서·길이). 자리 길이의 합은 영상 길이를 100 ms 단위로
      맞춘 값이다. 자리는 요청의 min_slot_ms 보다 짧지 않다 (영상 전체가 그보다 짧을 때만 예외).
    - 고정 출연은 모두 원본 IR 의 객체다.
    - 외부 영상(EXTERNAL)에서는 기준 이미지를 붙이지 않는다 (Theme.validate 가 막는다).
    - 화면 문구의 시각은 영상 길이 안이다.
    - 실패는 ThemeExtractionError 또는 다른 AdapterError (LLM 호출 실패 등).
    - signature 는 추출기 설정의 지문이다 (모델·프롬프트 판·프레임 수). 같은 영상에 다른 결과를 낼 수 있는 설정이
      바뀌면 달라진다. 같은 영상·같은 지문의 테마가 있으면 유스케이스가 다시 뽑지 않는다 (감사 BTS-12).
    """

    @property
    @abstractmethod
    def signature(self) -> str: ...

    @abstractmethod
    def extract(self, request: ThemeExtractionRequest) -> ThemeExtraction: ...
