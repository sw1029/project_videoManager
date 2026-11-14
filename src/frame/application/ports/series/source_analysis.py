"""원본 영상 분석 포트 (ADR-0025): 겉모습 읽기와 컷 찾기. 모델을 쓰지 않는 결정적인 단계다.

구현: Fake(가짜 영상의 값), ffmpeg (겉모습은 `ffmpeg -i` 의 출력, 컷은 장면 점수).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from frame.application.errors import AdapterError
from frame.domain.media import MediaInfo
from frame.domain.series.source_analysis import Shot


class VideoAnalysisError(AdapterError):
    """영상을 읽거나 나누지 못했다."""

    code = "VIDEO_ANALYSIS_ERROR"


class MediaProber(ABC):
    """계약
    - 영상의 겉모습을 돌려준다. size_bytes 는 넘겨받은 바이트 수다.
    - 영상이 아니거나 읽지 못하면 VideoAnalysisError.
    """

    @abstractmethod
    def probe(self, video: bytes) -> MediaInfo: ...


class ShotDetector(ABC):
    """계약
    - 0 ms 에서 시작해 media.duration_ms 에서 끝나고 서로 이어진 샷을 하나 이상 돌려준다.
    - 컷을 찾지 못하면 영상 전체가 한 샷이다 (예외가 아니다).
    - 영상을 읽지 못하면 VideoAnalysisError.
    """

    @abstractmethod
    def detect(self, video: bytes, media: MediaInfo) -> tuple[Shot, ...]: ...
