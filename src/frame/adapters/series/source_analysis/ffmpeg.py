"""ffmpeg 기반 겉모습 읽기·샷 나누기 (ADR-0025). 추가 설치 없이 영상 하나를 분석의 재료로 만든다.

- FfmpegMediaProber : 크기·길이·fps·소리 유무.
- FfmpegShotDetector: ffmpeg 의 scene 점수로 컷을 찾는다. 딱 끊기는 컷은 잘 잡고, 서서히 넘어가는 전환
  (디졸브·페이드)은 놓칠 수 있다. 너무 가까운 컷은 하나로 본다 (min_shot_ms).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from frame.adapters.media_tools import MediaToolError, detect_cuts, probe_media
from frame.adapters.video_tools import require_ffmpeg
from frame.application.ports.series.source_analysis import (
    MediaProber,
    ShotDetector,
    VideoAnalysisError,
)
from frame.domain.media import MediaInfo
from frame.domain.series.source_analysis import Shot, shots_from_cuts

# 받는 영상 코덱. ffmpeg 는 글 파일도 '영상'(bintext·ansi …)으로 읽을 수 있다. 계약은 "영상이 아니면 오류" 다.
# MJPEG 는 없다: 영상 정보를 읽는 쪽(media_tools)이 그림 한 장(음악 파일의 표지)으로 본다 (감사 BTS-D36)
VIDEO_CODECS = frozenset(
    {"h264", "hevc", "vp8", "vp9", "av1", "mpeg4", "mpeg2video", "theora", "prores"}
)


class FfmpegMediaProber(MediaProber):
    def __init__(self, ffmpeg: str | None, *, timeout_s: float = 120.0) -> None:
        self._ffmpeg = require_ffmpeg(ffmpeg)
        self.timeout_s = timeout_s

    def probe(self, video: bytes) -> MediaInfo:
        with tempfile.TemporaryDirectory(prefix="frame-probe-") as tmp:
            path = Path(tmp) / "source"
            path.write_bytes(video)
            try:
                found = probe_media(self._ffmpeg, path, self.timeout_s)
            except MediaToolError as exc:
                raise VideoAnalysisError(f"영상을 읽지 못했습니다: {exc}") from exc
        if not found.has_video or not found.width or not found.height or not found.duration_ms:
            raise VideoAnalysisError(
                "영상 스트림이 없는 파일입니다 (그림 한 장뿐인 파일과 MJPEG 영상은 받지 않습니다)"
            )
        if (found.video_codec or "").lower() not in VIDEO_CODECS:
            raise VideoAnalysisError(f"받지 않는 영상 코덱입니다: {found.video_codec}")
        return MediaInfo(
            width=found.width,
            height=found.height,
            duration_ms=found.duration_ms,
            fps=found.fps,
            has_audio=found.has_audio,
            size_bytes=len(video),
            video_codec=found.video_codec,
        )


class FfmpegShotDetector(ShotDetector):
    def __init__(
        self,
        ffmpeg: str | None,
        *,
        threshold: float,  # 설정이 정한다 (FRAME_SHOT_THRESHOLD)
        min_shot_ms: int = 500,
        timeout_s: float = 300.0,
    ) -> None:
        self._ffmpeg = require_ffmpeg(ffmpeg)
        self.threshold = threshold  # scene 점수 하한 (낮출수록 컷을 더 많이 찾는다)
        self.min_shot_ms = min_shot_ms
        self.timeout_s = timeout_s

    def detect(self, video: bytes, media: MediaInfo) -> tuple[Shot, ...]:
        with tempfile.TemporaryDirectory(prefix="frame-shots-") as tmp:
            path = Path(tmp) / "source"
            path.write_bytes(video)
            try:
                cuts = detect_cuts(
                    self._ffmpeg, path, threshold=self.threshold, timeout_s=self.timeout_s
                )
            except MediaToolError as exc:
                raise VideoAnalysisError(f"영상을 읽지 못했습니다: {exc}") from exc
        return shots_from_cuts(cuts, media.duration_ms, min_shot_ms=self.min_shot_ms)
