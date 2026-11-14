"""FfmpegVideoAssembler: 장면 클립을 순서대로 이어 한 mp4 로 만든다 (ffmpeg 없으면 건너뜀).

- 정규화된(같은 규격) 클립은 스트림 복사로 잇는다 (재인코딩 없음).
- 규격이 다른 클립(설정을 바꾸기 전에 만든 부모 클립 재사용 등)은 목표 규격으로 다시 인코딩한다.
"""

from __future__ import annotations

import subprocess

import pytest

from frame.adapters.generator.ffmpeg import (
    FfmpegVideoAssembler,
    FfmpegVideoNormalizer,
    probe_video,
)
from frame.adapters.shared.media.ffmpeg import resolve_ffmpeg
from frame.adapters.storage import InMemoryArtifactStore
from frame.application.errors import ConfigurationError, GenerationError
from frame.application.ports.generator import GeneratedClip


@pytest.fixture(scope="module")
def ffmpeg():
    try:
        return resolve_ffmpeg()
    except ConfigurationError:
        pytest.skip("ffmpeg 가 없어 건너뜀")


@pytest.fixture
def store():
    return InMemoryArtifactStore()


def _color_clip(
    ffmpeg, folder, store, scene_key, color, *, duration_ms=1000, fps=30, size=(480, 854)
):
    """단색 원본을 정규화해 저장한 장면 클립 (생성기가 저장한 클립과 같은 규격)."""
    raw = folder / f"{scene_key}.raw.mp4"
    subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
         f"color=c={color}:size=320x568:rate=25:duration={duration_ms / 1000 + 0.2}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(raw)],
        check=True,
        capture_output=True,
    )  # fmt: skip
    normalized = FfmpegVideoNormalizer(ffmpeg).normalize(
        raw.read_bytes(), duration_ms=duration_ms, fps=fps, width=size[0], height=size[1]
    )
    uri = store.put(f"clips/ver_1/{scene_key}.mp4", normalized.data, "video/mp4")
    return GeneratedClip(scene_key, uri, duration_ms, f"fp-{scene_key}")


def _rgb_at(ffmpeg, path, frame_index):
    """frame_index 번째 프레임의 평균 색 (1x1 로 줄인 RGB)."""
    out = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(path), "-vf",
         f"select='eq(n\\,{frame_index})',scale=1:1", "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True,
        capture_output=True,
    ).stdout  # fmt: skip
    return tuple(out[:3])


def _dominant(rgb):
    return "rgb"[max(range(3), key=lambda i: rgb[i])]


def test_uniform_clips_are_joined_in_order_without_reencoding(ffmpeg, store, tmp_path):
    clips = [
        _color_clip(ffmpeg, tmp_path, store, "intro", "red"),
        _color_clip(ffmpeg, tmp_path, store, "closeup", "green"),
        _color_clip(ffmpeg, tmp_path, store, "outro", "blue"),
    ]
    assembler = FfmpegVideoAssembler(store, ffmpeg, fps=30, width=480, height=854)

    video = assembler.assemble("ver_1", clips)

    assert video.uri == "mem://videos/ver_1.mp4"
    assert video.duration_ms == 3000
    assert store.content_type(video.uri) == "video/mp4"
    assert assembler.last_method == "copy"
    path = tmp_path / "video.mp4"
    path.write_bytes(store.get(video.uri))
    probe = probe_video(ffmpeg, path)
    assert (probe.width, probe.height, probe.fps, probe.frames, probe.has_audio) == (
        480,
        854,
        30.0,
        90,
        False,
    )
    # 장면 순서: 빨강 -> 초록 -> 파랑
    assert [_dominant(_rgb_at(ffmpeg, path, n)) for n in (15, 45, 75)] == ["r", "g", "b"]


def test_mismatched_clips_are_reencoded_to_the_target_spec(ffmpeg, store, tmp_path):
    clips = [
        _color_clip(ffmpeg, tmp_path, store, "old", "red", fps=24, size=(720, 1280)),
        _color_clip(ffmpeg, tmp_path, store, "new", "blue", duration_ms=1500),
    ]
    assembler = FfmpegVideoAssembler(store, ffmpeg, fps=30, width=480, height=854)

    video = assembler.assemble("ver_2", clips)

    assert assembler.last_method == "reencode"
    assert video.duration_ms == 2500
    path = tmp_path / "video.mp4"
    path.write_bytes(store.get(video.uri))
    probe = probe_video(ffmpeg, path)
    assert (probe.width, probe.height, probe.fps, probe.frames) == (480, 854, 30.0, 75)
    assert [_dominant(_rgb_at(ffmpeg, path, n)) for n in (10, 60)] == ["r", "b"]


def test_non_video_clips_are_rejected(ffmpeg, store):
    uri = store.put("clips/ver_1/intro.json", b"{}", "application/json")
    assembler = FfmpegVideoAssembler(store, ffmpeg)
    with pytest.raises(GenerationError, match="영상") as excinfo:
        assembler.assemble("ver_1", [GeneratedClip("intro", uri, 1000, "fp")])
    assert excinfo.value.retryable is False


def test_nothing_to_assemble_is_an_error(ffmpeg, store):
    with pytest.raises(GenerationError, match="클립"):
        FfmpegVideoAssembler(store, ffmpeg).assemble("ver_1", [])
