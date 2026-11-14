"""ffmpeg 로 영상·소리를 읽는 공용 도구 (영상 분석·소리 편집·문구 합성 어댑터가 함께 쓴다, ADR-0025).

- probe_media: 겉모습 (영상 스트림의 크기·fps·코덱, 소리 스트림의 유무·표본화율·채널, 길이).
- detect_cuts: 화면이 크게 바뀌는 시각 (ffmpeg 의 scene 점수).
- detect_silences: 소리가 없는 구간.
- region_ssim: 두 영상의 구조 유사도를 화면의 한 영역 안에서만, 또는 그 영역을 뺀 나머지에서만 잰다.
  국소 수정이 '고칠 곳만 바꾸고 나머지는 그대로 뒀는지' 를 재는 데 쓴다 (ADR-0027).
ffprobe 없이 ffmpeg 하나로 읽는다 (imageio-ffmpeg 는 ffprobe 를 담고 있지 않다). 실패는 MediaToolError 다.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from frame.application.errors import AdapterError

_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_VIDEO_LINE = re.compile(r"Stream #\d+:\d+.*?: Video: (\w+).*")
_AUDIO_LINE = re.compile(r"Stream #\d+:\d+.*?: Audio: (\w+).*")
_SIZE = re.compile(r", (\d{2,5})x(\d{2,5})")
_FPS = re.compile(r"(\d+(?:\.\d+)?) (?:fps|tbr)")
_SAMPLE_RATE = re.compile(r"(\d+) Hz")
_FRAMES = re.compile(r"frame=\s*(\d+)")
_PTS_TIME = re.compile(r"Parsed_showinfo.*?pts_time:\s*([0-9.]+)")
_SILENCE_START = re.compile(r"silence_start:\s*(-?[0-9.]+)")
_SILENCE_END = re.compile(r"silence_end:\s*([0-9.]+)")
_SSIM_ALL = re.compile(r"SSIM .*?All:([0-9.]+|nan|inf)")
_CHANNELS = {"mono": 1, "stereo": 2}
# 겉으로는 영상 스트림이지만 그림 한 장인 것 (음악 파일의 표지 등)
_STILL_CODECS = frozenset({"mjpeg", "png", "bmp", "gif", "webp"})


class MediaToolError(AdapterError):
    """ffmpeg 로 영상·소리를 읽거나 바꾸지 못했다."""

    code = "MEDIA_TOOL_ERROR"


@dataclass(frozen=True)
class MediaProbe:
    duration_ms: int | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    frames: int | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    sample_rate: int | None = None
    channels: int | None = None

    @property
    def has_video(self) -> bool:
        return self.video_codec is not None and self.width is not None

    @property
    def has_audio(self) -> bool:
        return self.audio_codec is not None


def probe_media(ffmpeg: str, path: str | Path, timeout_s: float = 120.0) -> MediaProbe:
    """파일의 겉모습. 영상 스트림이 있으면 끝까지 읽어 프레임을 세고, 길이를 프레임 수로 다시 잰다."""
    _, header = run_ffmpeg([ffmpeg, "-hide_banner", "-nostdin", "-i", str(path)], timeout_s)
    video = _VIDEO_LINE.search(header)
    audio = _AUDIO_LINE.search(header)
    if video is None and audio is None:
        raise MediaToolError(f"영상·소리 스트림을 찾지 못했습니다: {tail(header)}")
    duration = _duration_ms(header)
    probe = MediaProbe(duration_ms=duration)
    if audio is not None:
        line = audio.group(0)
        rate = _SAMPLE_RATE.search(line)
        channels = next((n for name, n in _CHANNELS.items() if f", {name}" in line), None)
        probe = MediaProbe(
            duration_ms=duration,
            audio_codec=audio.group(1),
            sample_rate=int(rate.group(1)) if rate else None,
            channels=channels,
        )
    if video is None or video.group(1) in _STILL_CODECS:
        return probe
    line = video.group(0)
    size, fps = _SIZE.search(line), _FPS.search(line)
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-i", str(path)]
    cmd += ["-map", "0:v:0", "-c", "copy", "-f", "null", "-"]
    code, log = run_ffmpeg(cmd, timeout_s)
    counted = _FRAMES.findall(log)
    frames = int(counted[-1]) if code == 0 and counted else None
    rate = float(fps.group(1)) if fps else None
    exact = round(frames * 1000 / rate) if frames and rate else duration
    return MediaProbe(
        duration_ms=exact,
        width=int(size.group(1)) if size else None,
        height=int(size.group(2)) if size else None,
        fps=rate,
        frames=frames,
        video_codec=video.group(1),
        audio_codec=probe.audio_codec,
        sample_rate=probe.sample_rate,
        channels=probe.channels,
    )


def detect_cuts(
    ffmpeg: str, path: str | Path, *, threshold: float = 0.3, timeout_s: float = 300.0
) -> list[int]:
    """화면이 앞 프레임과 크게 달라지는 시각(ms). threshold 는 ffmpeg 의 scene 점수(0..1) 하한이다."""
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-i", str(path), "-an"]
    cmd += ["-vf", f"select='gt(scene,{threshold:g})',showinfo", "-f", "null", "-"]
    code, log = run_ffmpeg(cmd, timeout_s)
    if code != 0:
        raise MediaToolError(f"컷을 찾지 못했습니다: {tail(log)}")
    return [round(float(value) * 1000) for value in _PTS_TIME.findall(log)]


def detect_silences(
    ffmpeg: str,
    path: str | Path,
    *,
    noise_db: float = -45.0,
    min_ms: int = 300,
    timeout_s: float = 300.0,
) -> list[tuple[int, int | None]]:
    """소리가 noise_db 보다 작은 구간 (시작, 끝). 파일 끝까지 조용하면 끝이 None 이다."""
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-i", str(path), "-vn"]
    cmd += ["-af", f"silencedetect=noise={noise_db:g}dB:d={min_ms / 1000:g}", "-f", "null", "-"]
    code, log = run_ffmpeg(cmd, timeout_s)
    if code != 0:
        raise MediaToolError(f"조용한 구간을 찾지 못했습니다: {tail(log)}")
    starts = [max(0, round(float(v) * 1000)) for v in _SILENCE_START.findall(log)]
    ends = [round(float(v) * 1000) for v in _SILENCE_END.findall(log)]
    return [(start, ends[i] if i < len(ends) else None) for i, start in enumerate(starts)]


def region_ssim(
    ffmpeg: str,
    first: bytes,
    second: bytes,
    box: tuple[float, float, float, float],
    *,
    inside: bool,
    span_ms: tuple[int, int] | None = None,
    timeout_s: float = 300.0,
) -> float:
    """두 영상의 SSIM 평균 (0..1) 을 화면의 일부에서만 잰다.

    box 는 정규화 좌표 (x, y, w, h). inside=True 면 그 영역만 잘라 견주고, False 면 그 영역을 검게 칠한 뒤
    나머지를 견준다. span_ms 를 주면 그 시간 구간의 프레임만 본다. 크기가 다르면 second 를 first 에 맞춘다.
    """
    x, y, w, h = box
    with tempfile.TemporaryDirectory(prefix="frame-ssim-") as tmp:
        a, b = Path(tmp) / "a.bin", Path(tmp) / "b.bin"
        a.write_bytes(first)
        b.write_bytes(second)
        size = probe_media(ffmpeg, a, timeout_s)
        if not size.has_video or not size.height:
            raise MediaToolError("견줄 영상에 영상 스트림이 없습니다")
        steps: list[str] = []
        if span_ms is not None:
            start, end = span_ms
            steps.append(f"trim=start={start / 1000:.3f}:end={end / 1000:.3f},setpts=PTS-STARTPTS")
        if inside:
            steps.append(f"crop=iw*{w:.6f}:ih*{h:.6f}:iw*{x:.6f}:ih*{y:.6f}")
        else:
            steps.append(
                f"drawbox=x=iw*{x:.6f}:y=ih*{y:.6f}:w=iw*{w:.6f}:h=ih*{h:.6f}:color=black:t=fill"
            )
        ops = ",".join(steps)
        graph = (
            f"[0:v]{ops}[a];[1:v]scale={size.width}:{size.height},setsar=1,{ops}[b];"
            "[a][b]ssim=shortest=1"
        )
        cmd = [ffmpeg, "-hide_banner", "-nostdin", "-i", str(a), "-i", str(b)]
        cmd += ["-filter_complex", graph, "-an", "-f", "null", "-"]
        code, log = run_ffmpeg(cmd, timeout_s)
    match = _SSIM_ALL.search(log)
    if code != 0 or match is None:
        raise MediaToolError(f"두 영상을 견주지 못했습니다: {tail(log)}")
    try:
        value = float(match.group(1))
    except ValueError as exc:
        raise MediaToolError(f"SSIM 값을 읽지 못했습니다: {match.group(1)}") from exc
    if value != value or value in (float("inf"), float("-inf")):
        raise MediaToolError(f"SSIM 값이 수가 아닙니다: {match.group(1)}")
    return max(0.0, min(1.0, value))


def run_ffmpeg(cmd: list[str], timeout_s: float, cwd: str | Path | None = None) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout_s,
            check=False,
            cwd=str(cwd) if cwd is not None else None,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),  # Windows 콘솔 창 안 띄움
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaToolError(f"ffmpeg 가 {timeout_s:g}초 안에 끝나지 않았습니다") from exc
    except OSError as exc:
        raise MediaToolError(f"ffmpeg 를 실행하지 못했습니다: {exc}") from exc
    return proc.returncode, proc.stderr.decode("utf-8", errors="replace")


def tail(log: str, lines: int = 3) -> str:
    useful = [line.strip() for line in log.replace("\r", "\n").splitlines() if line.strip()]
    return " | ".join(useful[-lines:])[-400:] or "(출력 없음)"


def _duration_ms(log: str) -> int | None:
    match = _DURATION.search(log)
    if match is None:
        return None
    hours, minutes, seconds = int(match.group(1)), int(match.group(2)), float(match.group(3))
    return round((hours * 3600 + minutes * 60 + seconds) * 1000)
