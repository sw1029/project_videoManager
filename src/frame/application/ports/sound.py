"""소리 읽기 포트 (ADR-0028): 영상·음성 파일에서 인식할 소리를 꺼내고, 소리가 없는 구간을 잰다.

대본을 뽑기 전의 일이다. 음성 인식(ports/speech.py)처럼 시리즈 기능 밖에서도 쓰는 개념이라 따로 둔다.
구현: 소리 편집 어댑터(Fake, ffmpeg)가 함께 구현한다 — 시리즈의 AudioEditor(ports/series/audio.py)가 이 포트를 잇는다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from frame.application.ports.speech import SpeechAudio


@dataclass(frozen=True)
class SoundProfile:
    """소리의 겉모습: 길이와 무음 구간. 말이 아닌 소리(음악·소음)를 가를 때의 재료다."""

    duration_ms: int
    silences: tuple[tuple[int, int], ...] = ()  # (시작, 끝) ms. 시간 순서이고 겹치지 않는다


class SoundReader(ABC):
    """계약
    - speech_audio: 음성 인식에 넘길 소리 (모노). 소리가 없으면 None.
    - sound_profile: 소리의 길이와 무음 구간. 소리가 없으면 None.
      무음 구간은 시간 순서이고 서로 겹치지 않으며 길이 안에 있다. 내내 소리가 나면 비어 있다.
    - 파일을 읽지 못하면 AdapterError.
    """

    @abstractmethod
    def speech_audio(self, media: bytes) -> SpeechAudio | None: ...

    @abstractmethod
    def sound_profile(self, media: bytes) -> SoundProfile | None: ...
