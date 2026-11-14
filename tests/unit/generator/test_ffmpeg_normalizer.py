"""FfmpegVideoNormalizer: 실제 ffmpeg 로 합성 영상을 만들어 길이·fps·해상도·오디오 제거를 확인한다.

ffmpeg 가 없으면 건너뛴다 (`pip install -e ".[video]"` 또는 PATH 의 ffmpeg, FRAME_VIDEO_FFMPEG).
"""

from __future__ import annotations

import subprocess
import tempfile

import pytest

from frame.adapters.generator.ffmpeg import FfmpegVideoNormalizer, probe_video
from frame.adapters.shared.media.ffmpeg import resolve_ffmpeg
from frame.application.errors import ConfigurationError, GenerationError


@pytest.fixture(scope="module")
def ffmpeg():
    try:
        return resolve_ffmpeg()
    except ConfigurationError:
        pytest.skip("ffmpeg 가 없어 건너뜀")


def _synthetic(ffmpeg, folder, *, seconds, fps, size, audio=True) -> bytes:
    """모델 출력처럼 해상도·fps·길이가 어긋난 원본을 만든다."""
    out = folder / f"src_{size}_{fps}_{seconds}.mp4"
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    cmd += ["-f", "lavfi", "-i", f"testsrc2=size={size}:rate={fps}:duration={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    cmd += ["-c:a", "aac", "-shortest"] if audio else []
    cmd.append(str(out))
    subprocess.run(cmd, check=True, capture_output=True)
    return out.read_bytes()


def test_longer_source_is_cut_to_exact_frames_without_audio(ffmpeg, tmp_path):
    source = _synthetic(ffmpeg, tmp_path, seconds=4.0625, fps=16, size="480x832")

    result = FfmpegVideoNormalizer(ffmpeg).normalize(
        source, duration_ms=4000, fps=24, width=480, height=854
    )

    src = result.source
    assert (src.width, src.height, src.fps, src.has_audio) == (480, 832, 16.0, True)
    out = result.probe
    assert (out.width, out.height, out.fps, out.frames, out.has_audio) == (
        480,
        854,
        24.0,
        96,
        False,
    )
    assert out.duration_ms == 4000
    assert out.codec == "h264"
    written = tmp_path / "out.mp4"
    written.write_bytes(result.data)
    assert probe_video(ffmpeg, written).frames == 96  # 저장된 파일을 다시 열어도 같다


def test_shorter_source_is_padded_with_its_last_frame(ffmpeg, tmp_path):
    source = _synthetic(ffmpeg, tmp_path, seconds=3, fps=24, size="720x1280", audio=False)

    result = FfmpegVideoNormalizer(ffmpeg).normalize(
        source, duration_ms=3500, fps=24, width=480, height=854
    )

    assert (result.probe.frames, result.probe.duration_ms) == (84, 3500)
    assert (result.probe.width, result.probe.height) == (480, 854)


def test_wan30_like_output_keeps_every_frame_at_the_default_fps(ffmpeg, tmp_path):
    """2026-11-11 실호출의 Wan 3.0 9:16 480p 4초 원본은 480x854·30fps·오디오 포함이었다.

    기본 fps(30)로 맞추면 프레임을 버리지 않고 오디오만 뺀다.
    """
    source = _synthetic(ffmpeg, tmp_path, seconds=4.04, fps=30, size="480x854")

    result = FfmpegVideoNormalizer(ffmpeg).normalize(
        source, duration_ms=4000, fps=30, width=480, height=854
    )

    assert (result.source.fps, result.source.has_audio) == (30.0, True)
    out = result.probe
    assert (out.width, out.height, out.fps, out.frames) == (480, 854, 30.0, 120)
    assert (out.duration_ms, out.has_audio) == (4000, False)


def test_garbage_input_raises_generation_error(ffmpeg):
    with pytest.raises(GenerationError, match="ffmpeg"):
        FfmpegVideoNormalizer(ffmpeg).normalize(
            b"not a video", duration_ms=1000, fps=24, width=480, height=854
        )


def test_error_messages_do_not_show_the_servers_temporary_folder(ffmpeg):
    """정규화 실패의 문구는 버전의 error_message 로 API 에 나간다 (감사 A-38)."""
    with pytest.raises(GenerationError) as raised:
        FfmpegVideoNormalizer(ffmpeg).normalize(
            b"not a video", duration_ms=1000, fps=24, width=480, height=854
        )

    message = str(raised.value)
    assert tempfile.gettempdir() not in message and "frame-ffmpeg-" not in message


def test_resolve_ffmpeg_uses_an_explicit_path_and_fails_clearly(tmp_path):
    exe = tmp_path / "ffmpeg.exe"
    exe.write_bytes(b"")
    assert resolve_ffmpeg(str(exe)) == str(exe)
    with pytest.raises(ConfigurationError, match="ffmpeg"):
        resolve_ffmpeg(str(tmp_path / "missing.exe"))
