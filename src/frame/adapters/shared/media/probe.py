"""영상 정보 읽기: 영상·이미지의 크기(video_size), 영상 스트림의 정보(probe_video), 파일 전체의 겉모습(probe_media),
프레임 세기(read_video_packets).

ffprobe 없이 ffmpeg 하나로 읽는다 (imageio-ffmpeg 는 ffprobe 를 담고 있지 않다). 실패는 VideoToolError 다.

읽는 것은 둘이다. ffmpeg 가 입력 파일에 대해 찍는 줄(스트림 · 길이)을 읽는 식은 한 벌을 같이 쓴다.
- probe_video: 첫 영상 스트림을 검사한다 (코덱 · 크기 · fps · 프레임 수). 영상 스트림이 없거나 ffmpeg 가 읽다
  실패하면 오류다.
  생성기의 클립 정규화와 최종 영상 조립이 만든 영상을 확인할 때 쓴다.
- probe_media: 파일에 무엇이 들었는지 본다 (영상 · 소리 스트림의 유무와 모양). 소리만 있는 파일도 읽고, 그림 한 장뿐인
  '영상'(음악 파일의 표지)은 영상으로 치지 않는다. 길이는 프레임 수로 다시 잰 값이다.
  시리즈의 영상 분석 · 소리 편집 · 화면 문구 합성이 받은 파일을 읽을 때 쓴다.

프레임 수는 ffmpeg 가 패킷마다 한 줄씩 적는 목록(framecrc 형식)의 줄 수로 센다. 진행 줄의 `frame=` 에서 읽지 않는다 —
그 줄은 ffmpeg 의 판에 따라 다르다. imageio-ffmpeg 가 Linux 에 가져오는 7.0.2 에서는 스트림 복사의 진행 줄에서
프레임 수가 읽히지 않아 None 이 됐고, 클립 정규화와 최종 영상 조립이 늘 실패했다 (CI 의 첫 실행에서 드러났다.
Windows 의 7.1 에서는 읽혔다). 스트림 복사라 디코드하지 않으므로 세는 데 드는 시간은 예전과 같다.
"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from .ffmpeg import READ_TIMEOUT_S, VideoToolError, run_reading, tail

__all__ = [
    "MediaProbe",
    "VideoProbe",
    "count_packets",
    "probe_media",
    "probe_video",
    "read_video_packets",
    "video_size",
]

# ffmpeg 가 입력 파일에 대해 찍는 줄을 읽는 식 (probe_video · probe_media 가 함께 쓴다)
_SIZE = re.compile(r"Stream #\d+:\d+.*?: Video: .*?, (\d{2,5})x(\d{2,5})")
_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_VIDEO_LINE = re.compile(r"Stream #\d+:\d+.*?: Video: (\w+).*")  # 그룹 1: 코덱
_AUDIO_LINE = re.compile(r"Stream #\d+:\d+.*?: Audio: (\w+).*")  # 그룹 1: 코덱
_LINE_SIZE = re.compile(r", (\d{2,5})x(\d{2,5})")
_FPS = re.compile(r"(\d+(?:\.\d+)?) (?:fps|tbr)")
_SAMPLE_RATE = re.compile(r"(\d+) Hz")
_CHANNELS = {"mono": 1, "stereo": 2}
# 겉으로는 영상 스트림이지만 그림 한 장인 것 (음악 파일의 표지 등)
_STILL_CODECS = frozenset({"mjpeg", "png", "bmp", "gif", "webp"})
# 패킷 목록(framecrc)의 한 줄: "스트림 번호, dts, pts, 길이, 크기, crc". 머리말은 # 으로 시작한다
_PACKET_LINE = re.compile(r"^\d+,", re.MULTILINE)

# ffmpeg 명령을 돌려 (종료 코드, 출력)을 돌려주는 것 — 어댑터마다 실행하지 못했을 때의 오류 종류가 다르다
Runner = Callable[[Sequence[str], float], tuple[int, str]]


@dataclass(frozen=True)
class VideoProbe:
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    frames: int | None = None  # 첫 영상 스트림의 패킷 수 (프레임 하나가 패킷 하나다)
    duration_ms: int | None = None
    has_audio: bool = False
    codec: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class MediaProbe:
    """파일의 겉모습: 영상 스트림과 소리 스트림 (없는 쪽의 값은 None)."""

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


def video_size(ffmpeg: str, media: bytes, timeout_s: float = 60.0) -> tuple[int, int]:
    """영상 또는 이미지의 (가로, 세로)."""
    with tempfile.TemporaryDirectory(prefix="frame-frames-") as tmp:
        source = Path(tmp) / "media.bin"
        source.write_bytes(media)
        _, log = run_reading([ffmpeg, "-hide_banner", "-nostdin", "-i", str(source)], timeout_s)
    match = _SIZE.search(log)
    if match is None:
        raise VideoToolError(f"영상 크기를 읽지 못했습니다: {tail(log)}")
    return int(match.group(1)), int(match.group(2))


def probe_video(ffmpeg: str, path: str | Path, timeout_s: float = READ_TIMEOUT_S) -> VideoProbe:
    """첫 영상 스트림의 코덱·크기·fps·프레임 수·길이와 오디오 유무 (스트림 복사로 끝까지 읽어 프레임을 센다).

    읽지 못하면 VideoToolError 다. 생성기의 정규화와 조립기는 그것을 생성 오류로 바꿔 올린다.
    """
    code, log, packets = read_video_packets(ffmpeg, path, timeout_s)
    if code != 0:
        raise VideoToolError(f"ffmpeg 가 영상을 읽지 못했습니다: {tail(log)}")
    video = _VIDEO_LINE.search(log)
    if video is None:
        raise VideoToolError(f"ffmpeg 가 영상 스트림을 찾지 못했습니다: {tail(log)}")
    width, height, fps = _shape(video.group(0))
    return VideoProbe(
        width=width,
        height=height,
        fps=fps,
        frames=packets,
        duration_ms=_duration_ms(log),
        has_audio=_AUDIO_LINE.search(log) is not None,
        codec=video.group(1),
    )


def probe_media(ffmpeg: str, path: str | Path, timeout_s: float = READ_TIMEOUT_S) -> MediaProbe:
    """파일의 겉모습. 영상 스트림이 있으면 끝까지 읽어 프레임을 세고, 길이를 프레임 수로 다시 잰다.

    영상 · 소리 스트림이 하나도 없으면 VideoToolError 다. 소리만 있는 파일은 오류가 아니다.
    """
    _, header = run_reading([ffmpeg, "-hide_banner", "-nostdin", "-i", str(path)], timeout_s)
    video = _VIDEO_LINE.search(header)
    audio = _AUDIO_LINE.search(header)
    if video is None and audio is None:
        raise VideoToolError(f"영상·소리 스트림을 찾지 못했습니다: {tail(header)}")
    duration = _duration_ms(header)
    probe = MediaProbe(duration_ms=duration)
    if audio is not None:
        line = audio.group(0)
        sampled = _SAMPLE_RATE.search(line)
        channels = next((n for name, n in _CHANNELS.items() if f", {name}" in line), None)
        probe = MediaProbe(
            duration_ms=duration,
            audio_codec=audio.group(1),
            sample_rate=int(sampled.group(1)) if sampled else None,
            channels=channels,
        )
    if video is None or video.group(1) in _STILL_CODECS:
        return probe
    width, height, rate = _shape(video.group(0))
    # 프레임은 패킷 목록의 줄로 센다. 진행 줄의 frame= 은 ffmpeg 의 판마다 달라 쓰지 않는다 (이 모듈의 설명)
    _, _, frames = read_video_packets(ffmpeg, path, timeout_s)
    exact = round(frames * 1000 / rate) if frames and rate else duration
    return MediaProbe(
        duration_ms=exact,
        width=width,
        height=height,
        fps=rate,
        frames=frames,
        video_codec=video.group(1),
        audio_codec=probe.audio_codec,
        sample_rate=probe.sample_rate,
        channels=probe.channels,
    )


def read_video_packets(
    ffmpeg: str, path: str | Path, timeout_s: float, run: Runner | None = None
) -> tuple[int, str, int | None]:
    """첫 영상 스트림을 끝까지 읽는다 (스트림 복사 - 디코드하지 않는다). (종료 코드, ffmpeg 의 출력, 패킷 수).

    패킷 수는 ffmpeg 가 임시 파일에 적은 패킷 목록의 줄 수다. ffmpeg 가 실패했거나 목록을 읽지 못하면 None 이다.
    출력에는 입력 파일의 정보(스트림 · 길이)가 들어 있어, 부르는 쪽이 코덱 · 크기 · fps 를 거기서 읽는다.
    run 은 명령을 돌리는 것이다 (주지 않으면 run_reading: 실행하지 못하면 VideoToolError).
    """
    runner = run if run is not None else run_reading
    with tempfile.TemporaryDirectory(prefix="frame-packets-") as tmp:
        listing = Path(tmp) / "packets.txt"
        cmd = [ffmpeg, "-hide_banner", "-nostdin", "-i", str(path)]
        cmd += ["-map", "0:v:0", "-c", "copy", "-f", "framecrc", str(listing)]
        code, log = runner(cmd, timeout_s)
        packets = _listed_packets(listing) if code == 0 else None
    return code, log, packets


def count_packets(listing: str) -> int:
    """패킷 목록(framecrc 형식)의 패킷 줄 수. 머리말(# 으로 시작하는 줄)과 빈 줄은 세지 않는다."""
    return len(_PACKET_LINE.findall(listing))


def _listed_packets(listing: Path) -> int | None:
    try:
        return count_packets(listing.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None


def _shape(video_line: str) -> tuple[int | None, int | None, float | None]:
    """영상 스트림 줄의 (가로, 세로, fps). 줄에 없는 값은 None 이다."""
    size, fps = _LINE_SIZE.search(video_line), _FPS.search(video_line)
    return (
        int(size.group(1)) if size else None,
        int(size.group(2)) if size else None,
        float(fps.group(1)) if fps else None,
    )


def _duration_ms(log: str) -> int | None:
    match = _DURATION.search(log)
    if match is None:
        return None
    hours, minutes, seconds = int(match.group(1)), int(match.group(2)), float(match.group(3))
    return round((hours * 3600 + minutes * 60 + seconds) * 1000)
