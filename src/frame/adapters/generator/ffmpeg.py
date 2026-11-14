"""ffmpeg 기반 장면 클립 정규화·검사와 최종 영상 조립 (FRAME_GENERATOR=external_api, FRAME_ASSEMBLER=ffmpeg).

모델마다 fps(16·24·30)·해상도·길이(4n+1 프레임 격자 등)·오디오 유무가 달라, 장면 클립을 한 규격으로 맞춘다.
- 길이: frames = round(duration_ms x fps / 1000). 원본이 길면 자르고, 짧으면 마지막 프레임을 늘려 채운다.
  fps=30 이면 100 ms 단위(fps=24 면 125 ms 단위) 장면 길이가 정확히 맞고, 그 밖의 길이는 반 프레임 이내로 반올림된다.
- 해상도: 비율을 지킨 채 목표 크기를 덮도록 늘리거나 줄인 뒤 가운데를 자른다 (검은 띠 없음).
- 형식: H.264(yuv420p) mp4 + faststart. 오디오·자막·메타데이터는 뺀다.
  모든 클립이 같은 규격이라 조립(concat)이 단순해진다.
- 조립: 규격이 같은 클립은 스트림 복사로 잇고(재인코딩 없음), 다르면 목표 규격으로 다시 인코딩한다.

ffmpeg 실행 파일 찾는 순서: FRAME_VIDEO_FFMPEG -> PATH 의 ffmpeg -> imageio-ffmpeg 패키지(video extra).
ffprobe 없이 ffmpeg 하나로 검사한다 (imageio-ffmpeg 는 ffprobe 를 담고 있지 않다).
"""

from __future__ import annotations

import re
import shutil
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from frame.adapters.shared.media.ffmpeg import FfmpegNotRun, run_ffmpeg, tail
from frame.application.errors import ConfigurationError, GenerationError
from frame.application.ports.artifact_store import ArtifactStore
from frame.application.ports.generator import GeneratedClip, VideoArtifact, VideoAssembler

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
        raise GenerationError(f"ffmpeg 가 영상을 읽지 못했습니다: {tail(log)}", retryable=False)
    video_line = _VIDEO_LINE.search(log)
    if video_line is None:
        raise GenerationError(
            f"ffmpeg 가 영상 스트림을 찾지 못했습니다: {tail(log)}", retryable=False
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
                raise GenerationError(f"ffmpeg 정규화에 실패했습니다: {tail(log)}", retryable=False)
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


class FfmpegVideoAssembler(VideoAssembler):
    """장면 클립을 순서대로 이어 한 mp4 로 만든다 (VideoAssembler 포트, FRAME_ASSEMBLER=ffmpeg).

    - 모든 클립이 목표 규격(H.264·크기·fps)과 같으면 concat demuxer 로 스트림을 복사한다.
      재인코딩이 없어 빠르고 화질 손실이 없다. FfmpegVideoNormalizer 를 거친 클립은 이 경로를 탄다.
    - 규격이 다르면(설정을 바꾸기 전에 만든 부모 클립을 재사용하는 경우 등) concat 필터로
      목표 규격에 맞춰 다시 인코딩한다.
      스트림 복사 결과가 디코딩되지 않거나 프레임 수가 맞지 않아도 다시 인코딩한다.
    - 영상 길이는 클립 duration_ms 의 합이다 (Fake 조립기와 같은 계약). 오디오는 넣지 않는다.
    """

    def __init__(
        self,
        store: ArtifactStore,
        ffmpeg: str,
        *,
        fps: int = 30,
        width: int = 720,
        height: int = 1280,
        crf: int = 18,
        preset: str = "medium",
        timeout_s: float = 600.0,
    ) -> None:
        self._store = store
        self.ffmpeg = ffmpeg
        self.fps = int(fps)
        self.width = int(width)
        self.height = int(height)
        self.crf = crf
        self.preset = preset
        self.timeout_s = timeout_s
        self.last_method: str | None = None  # "copy" | "reencode" (관찰·테스트용)

    def assemble(self, version_id: str, clips: Sequence[GeneratedClip]) -> VideoArtifact:
        if not clips:
            raise GenerationError("이어 붙일 장면 클립이 없습니다", retryable=False)
        with tempfile.TemporaryDirectory(prefix="frame-concat-") as tmp:
            folder = Path(tmp)
            paths = [
                self._materialize(clip, folder / f"clip_{i:03d}.mp4")
                for i, clip in enumerate(clips)
            ]
            probes = [probe_video(self.ffmpeg, path, self.timeout_s) for path in paths]
            out = folder / "video.mp4"
            data: bytes | None = None
            if all(self._matches(probe) for probe in probes):
                expected = sum(probe.frames or 0 for probe in probes)
                data = self._concat_copy(folder, paths, out, expected)
                self.last_method = "copy"
            if data is None:
                expected = sum(self._target_frames(clip.duration_ms) for clip in clips)
                data = self._concat_reencode(paths, clips, out, expected)
                self.last_method = "reencode"
        uri = self._store.put(f"videos/{version_id}.mp4", data, "video/mp4")
        return VideoArtifact(uri=uri, duration_ms=sum(clip.duration_ms for clip in clips))

    def _materialize(self, clip: GeneratedClip, path: Path) -> Path:
        media_type = self._store.content_type(clip.uri)
        if not media_type.startswith("video/"):
            raise GenerationError(
                f"장면 '{clip.scene_key}' 의 클립이 영상이 아닙니다 ({media_type}). "
                "FRAME_GENERATOR 가 fake 면 FRAME_ASSEMBLER 도 fake 로 두세요",
                retryable=False,
            )
        path.write_bytes(self._store.get(clip.uri))
        return path

    def _matches(self, probe: VideoProbe) -> bool:
        return (
            probe.codec == "h264"
            and (probe.width, probe.height) == (self.width, self.height)
            and probe.fps == float(self.fps)
            and probe.frames is not None
        )

    def _target_frames(self, duration_ms: int) -> int:
        return max(1, round(duration_ms * self.fps / 1000))

    def _concat_copy(
        self, folder: Path, paths: list[Path], out: Path, expected: int
    ) -> bytes | None:
        listing = folder / "clips.ffconcat"
        lines = ["ffconcat version 1.0"] + [f"file '{path.name}'" for path in paths]
        listing.write_text("\n".join(lines) + "\n", encoding="utf-8")
        cmd = [self.ffmpeg, "-hide_banner", "-nostdin", "-y", "-f", "concat", "-i", str(listing)]
        cmd += ["-map", "0:v:0", "-c", "copy", "-an", "-map_metadata", "-1"]
        cmd += ["-movflags", "+faststart", str(out)]
        code, _ = _run(cmd, self.timeout_s)
        if code != 0 or not out.is_file():
            return None
        if probe_video(self.ffmpeg, out, self.timeout_s).frames != expected:
            return None  # 스트림 복사가 어긋나면 다시 인코딩한다
        if not self._decodes(out):
            return None
        return out.read_bytes()

    def _concat_reencode(
        self, paths: list[Path], clips: Sequence[GeneratedClip], out: Path, expected: int
    ) -> bytes:
        w, h, fps = self.width, self.height, self.fps
        chains = []
        for i, clip in enumerate(clips):
            chains.append(
                f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,"
                f"fps={fps},tpad=stop_mode=clone:stop_duration={clip.duration_ms / 1000 + 1:g},"
                f"trim=end_frame={self._target_frames(clip.duration_ms)},setpts=PTS-STARTPTS[v{i}]"
            )
        joined = "".join(f"[v{i}]" for i in range(len(clips)))
        graph = ";".join(chains) + f";{joined}concat=n={len(clips)}:v=1:a=0[out]"
        cmd = [self.ffmpeg, "-hide_banner", "-nostdin", "-y"]
        for path in paths:
            cmd += ["-i", str(path)]
        cmd += ["-filter_complex", graph, "-map", "[out]", "-an", "-map_metadata", "-1"]
        cmd += ["-c:v", "libx264", "-preset", self.preset, "-crf", str(self.crf)]
        # concat 필터 출력은 시간 기준이 섞여 평균 fps 가 어긋나 보이므로 고정 프레임레이트로 쓴다
        cmd += ["-r", str(fps), "-fps_mode", "cfr"]
        cmd += ["-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]
        code, log = _run(cmd, self.timeout_s)
        if code != 0 or not out.is_file():
            raise GenerationError(f"ffmpeg 조립에 실패했습니다: {tail(log)}", retryable=False)
        frames = probe_video(self.ffmpeg, out, self.timeout_s).frames
        if frames != expected:
            raise GenerationError(
                f"ffmpeg 조립 결과가 {frames} 프레임입니다 (목표 {expected})", retryable=False
            )
        return out.read_bytes()

    def _decodes(self, path: Path) -> bool:
        cmd = [self.ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-i", str(path)]
        code, log = _run(cmd + ["-f", "null", "-"], self.timeout_s)
        return code == 0 and not log.strip()


def _run(cmd: list[str], timeout_s: float) -> tuple[int, str]:
    """공용 실행기로 돌린다. 실행 자체가 안 되면 다시 해도 소용없는 생성 오류로 올린다."""
    try:
        return run_ffmpeg(cmd, timeout_s)
    except FfmpegNotRun as exc:
        raise GenerationError(str(exc), retryable=False) from exc


def _duration_ms(log: str) -> int | None:
    match = _DURATION.search(log)
    if match is None:
        return None
    hours, minutes, seconds = int(match.group(1)), int(match.group(2)), float(match.group(3))
    return round((hours * 3600 + minutes * 60 + seconds) * 1000)
