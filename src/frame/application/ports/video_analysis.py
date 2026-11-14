"""영상 분석 포트 (US-06, ADR-0025): 영상 한 편에서 테마를 뽑는 데 필요한 네 가지 일.

- MediaProber     : 겉모습 (크기·길이·fps·오디오 유무). 숏폼 규격 판정의 입력이다.
- ShotDetector    : 컷을 찾아 샷으로 나눈다.
- SpeechRecognizer: 말소리를 구간(가능하면 낱말) 시각이 붙은 글로 받아 적는다.
                    원본 영상의 내레이션을 읽을 때와, 사용자가 녹음한 음성이 대본과 맞는지 볼 때 쓴다.
- ThemeExtractor  : 위의 관찰값과 영상에서 테마와 원본 IR 을 만든다.

구현 후보
- MediaProber·ShotDetector: ffmpeg (추가 설치 없음), PySceneDetect·TransNetV2 (설치 필요)
- SpeechRecognizer: OpenAI 호환 음성 인식 API, 로컬 Whisper (설치 필요)
- ThemeExtractor: 프레임을 읽는 LLM (FRAME_VLM_MODEL), 영상을 직접 읽는 모델
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field

from frame.application.errors import AdapterError
from frame.domain.ir import IntermediateRepresentation
from frame.domain.series.shortform import MediaInfo
from frame.domain.series.source_analysis import ScreenText, Shot, Transcript, VideoAnalysis
from frame.domain.series.theme import DEFAULT_MIN_SLOT_MS, SourceVideo, Theme


class VideoAnalysisError(AdapterError):
    """영상을 읽거나 나누지 못했다."""

    code = "VIDEO_ANALYSIS_ERROR"


class SpeechRecognitionError(AdapterError):
    """말소리를 받아 적지 못했다 (알아들을 수 없는 소리, 잘못된 응답)."""

    code = "SPEECH_RECOGNITION_ERROR"


class SpeechServiceError(SpeechRecognitionError):
    """인식 서비스를 쓸 수 없다: 키 없음·인증 실패·한도 초과·연결 실패·서버 오류.

    소리의 문제가 아니라 설정·환경의 문제다. 부른 쪽은 이 오류를 넘기고 다음 유료 단계로 가지 않는다
    (감사 BTS-29: 설정 오류인데 돈을 쓰고 내레이션 없는 테마가 저장됐다).
    """

    code = "SPEECH_SERVICE_ERROR"


class ThemeExtractionError(AdapterError):
    code = "THEME_EXTRACTION_ERROR"


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


@dataclass(frozen=True)
class SpeechAudio:
    """인식기에 넘길 음성. 영상에서 뽑았거나 사용자가 녹음한 것."""

    data: bytes
    media_type: str = "audio/wav"
    duration_ms: int | None = None


class SpeechRecognizer(ABC):
    """계약
    - 구간은 시간 순서이고 서로 겹치지 않는다. 음성 길이를 알면 그 안에 있다.
    - 말이 없으면 구간이 빈 Transcript 다 (예외가 아니다).
    - hint 는 인식을 돕는 낱말이다 (메뉴 이름 같은 고유명). 받아 적을 내용을 정하지는 않는다.
    - 호출 실패는 SpeechRecognitionError. API 키 같은 비밀을 예외·로그·반환값에 남기지 않는다.
    """

    @abstractmethod
    def transcribe(
        self, audio: SpeechAudio, *, language: str | None = None, hint: str = ""
    ) -> Transcript: ...


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
