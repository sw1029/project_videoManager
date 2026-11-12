"""ffmpeg 기반 장면 클립 정규화·검사 (생성 어댑터가 쓰고, 이후 FfmpegVideoAssembler 도 같이 쓸 수 있다).

모델마다 fps(16·24·30)·해상도·길이(4n+1 프레임 격자 등)·오디오 유무가 달라, 장면 클립을 한 규격으로 맞춘다.
- 길이: frames = round(duration_ms x fps / 1000). 원본이 길면 자르고, 짧으면 마지막 프레임을 늘려 채운다.
  fps=30 이면 100 ms 단위(fps=24 면 125 ms 단위) 장면 길이가 정확히 맞고, 그 밖의 길이는 반 프레임 이내로 반올림된다.
- 해상도: 비율을 지킨 채 목표 크기를 덮도록 늘리거나 줄인 뒤 가운데를 자른다 (검은 띠 없음).
- 형식: H.264(yuv420p) mp4 + faststart. 오디오·자막·메타데이터는 뺀다.
  모든 클립이 같은 규격이라 조립(concat)이 단순해진다.

ffmpeg 실행 파일 찾는 순서: FRAME_VIDEO_FFMPEG -> PATH 의 ffmpeg -> imageio-ffmpeg 패키지(video extra).
ffprobe 없이 ffmpeg 하나로 검사한다 (imageio-ffmpeg 는 ffprobe 를 담고 있지 않다).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from frame.application.errors import ConfigurationError, GenerationError

_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_VIDEO_LINE = re.compile(r"Stream #\d+:\d+.*?: Video: .*")
_CODEC = re.compile(r"Video: (\w+)")
_SIZE = re.compile(r", (\d{2,5})x(\d{2,5})")
_FPS = re.compile(r"(\d+(?:\.\d+)?) (?:fps|tbr)")
_AUDIO_LINE = re.compile(r"Stream #\d+:\d+.*?: Audio: ")
_FRAMES = re.compile(r"frame=\s*(\d+)")


@dataclass(frozen=True)
class VideoProbe:
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    frames: int | None = None
    duration_ms: int | None = None
    has_audio: bool = False
    codec: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


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


def resolve_ffmpeg(explicit: str | None = None) -> str:
    """ffmpeg 실행 파일 경로. 못 찾으면 ConfigurationError (서버 기동 시점에 실패하도록 레지스트리에서 부른다)."""
    if explicit:
        if Path(explicit).is_file():
            return explicit
        raise ConfigurationError(
            f"FRAME_VIDEO_FFMPEG 에 지정한 ffmpeg 실행 파일이 없습니다: {explicit}"
        )
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg  # video extra (ffmpeg 정적 빌드 동봉)

        return str(imageio_ffmpeg.get_ffmpeg_exe())
    except (ImportError, RuntimeError):
        pass
    raise ConfigurationError(
        'ffmpeg 를 찾지 못했습니다. pip install -e ".[video]" (imageio-ffmpeg) 로 설치하거나, '
        "PATH 에 ffmpeg 를 두거나, FRAME_VIDEO_FFMPEG 로 실행 파일 경로를 지정하세요"
    )


def probe_video(ffmpeg: str, path: str | Path, timeout_s: float = 120.0) -> VideoProbe:
    """첫 영상 스트림의 코덱·크기·fps·프레임 수·길이와 오디오 유무 (스트림 복사로 끝까지 읽어 프레임을 센다)."""
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-i", str(path)]
    cmd += ["-map", "0:v:0", "-c", "copy", "-f", "null", "-"]
    code, log = _run(cmd, timeout_s)
    if code != 0:
        raise GenerationError(f"ffmpeg 가 영상을 읽지 못했습니다: {_tail(log)}", retryable=False)
    video_line = _VIDEO_LINE.search(log)
    if video_line is None:
        raise GenerationError(
            f"ffmpeg 가 영상 스트림을 찾지 못했습니다: {_tail(log)}", retryable=False
        )
    line = video_line.group(0)
    codec = _CODEC.search(line)
    size = _SIZE.search(line)
    fps = _FPS.search(line)
    frames = _FRAMES.findall(log)
    return VideoProbe(
        width=int(size.group(1)) if size else None,
        height=int(size.group(2)) if size else None,
        fps=float(fps.group(1)) if fps else None,
        frames=int(frames[-1]) if frames else None,
        duration_ms=_duration_ms(log),
        has_audio=_AUDIO_LINE.search(log) is not None,
        codec=codec.group(1) if codec else None,
    )


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
            source_probe = probe_video(self.ffmpeg, src, self.timeout_s)
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
                raise GenerationError(
                    f"ffmpeg 정규화에 실패했습니다: {_tail(log)}", retryable=False
                )
            probe = probe_video(self.ffmpeg, out, self.timeout_s)
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
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout_s,
            check=False,
            creationflags=getattr(
                subprocess, "CREATE_NO_WINDOW", 0
            ),  # Windows 콘솔 창을 띄우지 않음
        )
    except subprocess.TimeoutExpired as exc:
        raise GenerationError(
            f"ffmpeg 가 {timeout_s:g}초 안에 끝나지 않았습니다", retryable=False
        ) from exc
    except OSError as exc:
        raise GenerationError(f"ffmpeg 를 실행하지 못했습니다: {exc}", retryable=False) from exc
    return proc.returncode, proc.stderr.decode("utf-8", errors="replace")


def _duration_ms(log: str) -> int | None:
    match = _DURATION.search(log)
    if match is None:
        return None
    hours, minutes, seconds = int(match.group(1)), int(match.group(2)), float(match.group(3))
    return round((hours * 3600 + minutes * 60 + seconds) * 1000)


def _tail(log: str, lines: int = 3) -> str:
    useful = [line.strip() for line in log.replace("\r", "\n").splitlines() if line.strip()]
    return " | ".join(useful[-lines:])[-400:] or "(출력 없음)"
