"""ffmpeg 기반 장면 클립 정규화 (FRAME_GENERATOR=external_api).

모델마다 fps(16·24·30)·해상도·길이(4n+1 프레임 격자 등)·오디오 유무가 달라, 장면 클립을 한 규격으로 맞춘다.
- 길이: frames = round(duration_ms x fps / 1000). 원본이 길면 자르고, 짧으면 마지막 프레임을 늘려 채운다.
  fps=30 이면 100 ms 단위(fps=24 면 125 ms 단위) 장면 길이가 정확히 맞고, 그 밖의 길이는 반 프레임 이내로 반올림된다.
- 해상도: 비율을 지킨 채 목표 크기를 덮도록 늘리거나 줄인 뒤 가운데를 자른다 (검은 띠 없음).
- 형식: H.264(yuv420p) mp4 + faststart. 오디오·자막·메타데이터는 뺀다.
  모든 클립이 같은 규격이라 조립(adapters/assembler/)이 스트림 복사로 끝난다.

ffmpeg 찾기·실행과 클립 검사(probe_video)는 frame.adapters.shared.media 에 있다.
"""

from __future__ import annotations

import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from pathlib import Path

from frame.adapters.shared.media.ffmpeg import FfmpegNotRun, VideoToolError, run_ffmpeg, tail
from frame.adapters.shared.media.probe import VideoProbe, probe_video
from frame.application.errors import GenerationError

__all__ = ["FfmpegVideoNormalizer", "NormalizedVideo", "VideoNormalizer"]


@dataclass(frozen=True)
class NormalizedVideo:
    data: bytes
    probe: VideoProbe  # 정규화한 결과
    source: VideoProbe  # 모델이 돌려준 원본


class VideoNormalizer(ABC):
    @abstractmethod
    def normalize(
        self, source: bytes, *, duration_ms: int, fps: int, width: int, height: int
    ) -> NormalizedVideo:
        """원본을 duration_ms(프레임 단위 반올림)·fps·width x height 의 무음 mp4 로 바꾼다. 실패는 GenerationError."""


class FfmpegVideoNormalizer(VideoNormalizer):
    def __init__(
        self, ffmpeg: str, *, crf: int = 18, preset: str = "medium", timeout_s: float = 300.0
    ) -> None:
        self.ffmpeg = ffmpeg
        self.crf = crf
        self.preset = preset
        self.timeout_s = timeout_s

    def normalize(
        self, source: bytes, *, duration_ms: int, fps: int, width: int, height: int
    ) -> NormalizedVideo:
        if duration_ms <= 0 or fps <= 0 or width <= 0 or height <= 0:
            raise GenerationError(
                f"정규화 목표가 잘못되었습니다: {duration_ms} ms, {fps} fps, {width}x{height}",
                retryable=False,
            )
        frames = max(1, round(duration_ms * fps / 1000))
        with tempfile.TemporaryDirectory(prefix="frame-ffmpeg-") as tmp:
            src = Path(tmp) / "source.mp4"
            out = Path(tmp) / "normalized.mp4"
            src.write_bytes(source)
            source_probe = _probe(self.ffmpeg, src, self.timeout_s)
            filters = ",".join(
                [
                    f"scale={width}:{height}:force_original_aspect_ratio=increase",
                    f"crop={width}:{height}",
                    "setsar=1",
                    f"fps={fps}",
                    # 원본이 짧으면 마지막 프레임을 늘린다. 남는 부분은 -frames:v 가 잘라 낸다
                    f"tpad=stop_mode=clone:stop_duration={duration_ms / 1000 + 1:g}",
                ]
            )
            cmd = [self.ffmpeg, "-hide_banner", "-nostdin", "-y", "-i", str(src)]
            cmd += ["-map", "0:v:0", "-an", "-sn", "-dn", "-map_metadata", "-1"]
            cmd += ["-vf", filters, "-frames:v", str(frames)]
            cmd += ["-c:v", "libx264", "-preset", self.preset, "-crf", str(self.crf)]
            cmd += ["-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]
            code, log = _run(cmd, self.timeout_s)
            if code != 0 or not out.is_file():
                raise GenerationError(f"ffmpeg 정규화에 실패했습니다: {tail(log)}", retryable=False)
            probe = _probe(self.ffmpeg, out, self.timeout_s)
            if probe.frames != frames or (probe.width, probe.height) != (width, height):
                raise GenerationError(
                    f"ffmpeg 정규화 결과가 목표와 다릅니다: {probe.width}x{probe.height} "
                    f"{probe.frames} 프레임 (목표 {width}x{height} {frames} 프레임)",
                    retryable=False,
                )
            data = out.read_bytes()
        # 컨테이너가 보고하는 길이는 1/100 초 단위라, 길이는 프레임 수로 계산한다
        exact = replace(probe, duration_ms=round(frames * 1000 / fps))
        return NormalizedVideo(data=data, probe=exact, source=source_probe)


def _run(cmd: list[str], timeout_s: float) -> tuple[int, str]:
    """공용 실행기로 돌린다. 실행 자체가 안 되면 다시 해도 소용없는 생성 오류로 올린다."""
    try:
        return run_ffmpeg(cmd, timeout_s)
    except FfmpegNotRun as exc:
        raise GenerationError(str(exc), retryable=False) from exc


def _probe(ffmpeg: str, path: Path, timeout_s: float) -> VideoProbe:
    """클립 검사. 읽지 못하면 다시 해도 소용없는 생성 오류로 올린다."""
    try:
        return probe_video(ffmpeg, path, timeout_s)
    except VideoToolError as exc:
        raise GenerationError(str(exc), retryable=False) from exc
