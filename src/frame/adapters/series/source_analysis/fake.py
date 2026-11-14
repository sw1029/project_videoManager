"""테스트·오프라인용 겉모습 읽기·컷 찾기. 가짜 영상(fake_video)에 적힌 값을 그대로 돌려준다.

결정적이다. error 를 주면 그 오류를 던지고, calls 에 호출을 남긴다.
"""

from __future__ import annotations

from typing import Any

from frame.adapters.fake_media import decode_fake_media
from frame.application.ports.series.source_analysis import (
    MediaProber,
    ShotDetector,
    VideoAnalysisError,
)
from frame.domain.media import MediaInfo
from frame.domain.series.source_analysis import Shot, shots_from_cuts


def _video_part(video: bytes) -> dict[str, Any]:
    media = decode_fake_media(video)
    part = media.get("video") if media else None
    if not isinstance(part, dict):
        raise VideoAnalysisError("가짜 분석기는 fake_video() 로 만든 영상만 읽습니다")
    return part


class FakeMediaProber(MediaProber):
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self.calls = 0

    def probe(self, video: bytes) -> MediaInfo:
        self.calls += 1
        if self._error is not None:
            raise self._error
        part = _video_part(video)
        media = decode_fake_media(video) or {}
        return MediaInfo(
            width=int(part["width"]),
            height=int(part["height"]),
            duration_ms=int(part["duration_ms"]),
            fps=float(part["fps"]) if part.get("fps") is not None else None,
            has_audio=isinstance(media.get("audio"), dict),
            size_bytes=len(video),
            video_codec="fake",
        )


class FakeShotDetector(ShotDetector):
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self.calls = 0

    def detect(self, video: bytes, media: MediaInfo) -> tuple[Shot, ...]:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return shots_from_cuts(_video_part(video).get("cuts_ms") or (), media.duration_ms)
