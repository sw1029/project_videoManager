"""음성 인식 포트: 말소리 -> 낱말 시각이 있는 받아쓰기.

테마 추출(원본의 말소리)과 녹음 붙이기(사용자의 녹음)가 함께 쓴다. 시리즈 기능 밖에서도 쓸 수 있는 개념이라 따로 둔다.
구현: Fake(테스트), OpenAI 호환 /audio/transcriptions, 결과를 남기는 래퍼(같은 소리는 한 번만 과금).
LLM·HTTP 호출의 오류는 SpeechRecognitionError 로 올라온다. 서비스를 쓸 수 없는 경우(키·인증·한도·연결)는
SpeechServiceError 로 가른다: 부른 쪽은 그 오류를 넘기고 다음 유료 단계로 가지 않는다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from frame.application.errors import AdapterError
from frame.domain.series.source_analysis import Transcript


class SpeechRecognitionError(AdapterError):
    """말소리를 받아 적지 못했다 (알아들을 수 없는 소리, 잘못된 응답)."""

    code = "SPEECH_RECOGNITION_ERROR"


class SpeechServiceError(SpeechRecognitionError):
    """인식 서비스를 쓸 수 없다: 키 없음·인증 실패·한도 초과·연결 실패·서버 오류.

    소리의 문제가 아니라 설정·환경의 문제다. 부른 쪽은 이 오류를 넘기고 다음 유료 단계로 가지 않는다
    (감사 BTS-29: 설정 오류인데 돈을 쓰고 내레이션 없는 테마가 저장됐다).
    """

    code = "SPEECH_SERVICE_ERROR"


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
