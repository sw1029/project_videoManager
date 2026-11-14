"""소리 편집 포트 (US-06, ADR-0026): 영상·녹음에서 소리를 꺼내고, 녹음을 잘라 한 트랙으로 놓고, 영상에 붙인다.

음성을 만들지 않는다. 사용자가 녹음한 파일을 다룰 뿐이다.
구현: Fake(테스트), ffmpeg (추가 설치 없음).
"""

from __future__ import annotations

import math
from abc import abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass

from frame.application.errors import AdapterError
from frame.application.ports.sound import SoundReader
from frame.domain.errors import ValidationError
from frame.domain.series.narration import MAX_TRACK_TEMPO, MIN_TRACK_TEMPO


class AudioError(AdapterError):
    code = "AUDIO_ERROR"


@dataclass(frozen=True)
class AudioInfo:
    duration_ms: int
    sample_rate: int | None = None
    channels: int | None = None
    codec: str | None = None


@dataclass(frozen=True)
class TrackCut:
    """녹음 한 조각을 트랙의 어디에 놓는가."""

    source_index: int  # sources 의 몇 번째 녹음인가
    source_start_ms: int
    source_end_ms: int
    target_start_ms: int  # 트랙 안에서 시작하는 시각
    tempo: float = 1.0  # 1 보다 크면 그만큼 빠르게 (음높이는 그대로)

    def __post_init__(self) -> None:
        # 조각의 검증을 여기 한곳에 둔다. 어댑터마다 따로 보면 같은 입력에 가짜는 ZeroDivisionError,
        # 실제는 AudioError 를 냈다 (감사 BTS-27)
        if self.source_index < 0 or self.source_start_ms < 0 or self.target_start_ms < 0:
            raise ValidationError(f"트랙 조각의 번호·시각은 0 이상이어야 합니다: {self}")
        if self.source_end_ms <= self.source_start_ms:
            raise ValidationError(f"트랙 조각의 끝이 시작보다 뒤여야 합니다: {self}")
        if not (math.isfinite(self.tempo) and MIN_TRACK_TEMPO <= self.tempo <= MAX_TRACK_TEMPO):
            raise ValidationError(
                f"트랙 조각의 빠르기는 {MIN_TRACK_TEMPO}~{MAX_TRACK_TEMPO} 이어야 합니다: {self.tempo}"
            )


class AudioEditor(SoundReader):
    """계약 (소리 읽기 SoundReader 의 계약에 더해)
    - probe: 소리 스트림이 없으면 None. 파일을 읽지 못하면 AudioError.
    - render_track: 길이가 total_ms 인 트랙 하나. 조각이 없는 곳은 무음이고, 조각이 없으면 전체가 무음이다.
      조각이 total_ms 를 넘으면 넘는 부분은 잘린다. 조각이 가리키는 녹음이 없으면 AudioError.
    - mux: 영상의 그림은 그대로 두고 소리만 track 으로 바꾼다. 결과의 길이는 영상의 길이다.
    - track_suffix: render_track 이 돌려주는 트랙의 파일 확장자 (저장할 때 형식을 알 수 있게).
    """

    track_suffix: str = ".wav"

    @abstractmethod
    def probe(self, media: bytes) -> AudioInfo | None: ...

    @abstractmethod
    def render_track(
        self, sources: Sequence[bytes], cuts: Sequence[TrackCut], total_ms: int
    ) -> bytes: ...

    @abstractmethod
    def mux(self, video: bytes, track: bytes) -> bytes: ...
