"""ffmpeg 로 장면 클립을 이어 최종 영상을 만든다 (FRAME_ASSEMBLER=ffmpeg).

생성기가 정규화한 클립(adapters/generator/normalizer.py)은 모두 같은 규격이라 스트림 복사로 잇는다 (재인코딩 없음).
규격이 다른 클립이 섞이면 목표 규격으로 다시 인코딩한다.
ffmpeg 찾기·실행과 클립 검사(probe_video)는 frame.adapters.shared.media 에 있다.
"""

from __future__ import annotations

import tempfile
from collections.abc import Sequence
from pathlib import Path

from frame.adapters.shared.media.ffmpeg import FfmpegNotRun, VideoToolError, run_ffmpeg, tail
from frame.adapters.shared.media.probe import VideoProbe, probe_video
from frame.application.errors import GenerationError
from frame.application.ports.artifact_store import ArtifactStore
from frame.application.ports.assembler import VideoAssembler
from frame.domain.artifacts import GeneratedClip, VideoArtifact

__all__ = ["FfmpegVideoAssembler"]


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
            probes = [_probe(self.ffmpeg, path, self.timeout_s) for path in paths]
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
        if _probe(self.ffmpeg, out, self.timeout_s).frames != expected:
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
        frames = _probe(self.ffmpeg, out, self.timeout_s).frames
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


def _probe(ffmpeg: str, path: Path, timeout_s: float) -> VideoProbe:
    """클립 검사. 읽지 못하면 다시 해도 소용없는 생성 오류로 올린다."""
    try:
        return probe_video(ffmpeg, path, timeout_s)
    except VideoToolError as exc:
        raise GenerationError(str(exc), retryable=False) from exc
