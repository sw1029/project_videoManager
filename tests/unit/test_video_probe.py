"""영상 정보 읽기 (adapters/shared/media/probe · media_tools) 의 프레임 수.

프레임 수는 생성 결과의 확인에 쓰인다: 클립 정규화는 만든 클립의 프레임 수가 목표와 다르면 실패하고,
최종 영상 조립은 이은 영상의 프레임 수를 장면들의 합과 견준다. 그래서 세는 방법이 ffmpeg 의 판에 따라 달라지면 안 된다.

프레임 수를 진행 줄의 `frame=` 에서 읽던 때에는, Linux(imageio-ffmpeg 가 ffmpeg 7.0.2 를 가져온다)에서
프레임 수가 None 이 되어 정규화와 조립이 늘 실패했다 (CI 의 첫 실행). 스트림 복사의 진행 줄에 `frame=` 이 없는 판이
있다는 뜻이다 (Windows 의 7.1 은 찍는다). 여기서는 그 조건을 실제 출력에서 `frame=` 을 빼는 것으로 흉내 낸다.
"""

from __future__ import annotations

import re
import subprocess

import pytest

from frame.adapters.shared.media import media_tools, probe
from frame.adapters.shared.media.probe import count_packets, probe_video, read_video_packets
from tests.support.video import find_ffmpeg, make_clip

FFMPEG = find_ffmpeg()
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg 없음 (video extra 미설치)")

# ffmpeg 가 진행 줄에 찍는 프레임 수
_FRAME_FIELD = re.compile(r"frame=\s*\d+\s*")


def _without_frame_field(run):
    """진행 줄에 프레임 수를 찍지 않는 ffmpeg 처럼 보이게 한다: 실제로 돌린 출력에서 `frame=` 을 뺀다."""

    def runner(cmd, timeout_s, *args, **kwargs):
        code, log = run(cmd, timeout_s, *args, **kwargs)
        return code, _FRAME_FIELD.sub("", log)

    return runner


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    """4초 · 초당 10프레임 = 40프레임."""
    path = tmp_path_factory.mktemp("probe") / "clip.mp4"
    make_clip(FFMPEG, path, seconds=4.0, size="64x114", fps=10)
    return path


@needs_ffmpeg
def test_the_probe_reads_the_frames_and_the_shape_of_a_clip(clip):
    found = probe_video(FFMPEG, clip)

    assert found.frames == 40
    assert (found.width, found.height, found.fps, found.codec) == (64, 114, 10.0, "h264")
    assert found.duration_ms == 4000 and found.has_audio is False


@needs_ffmpeg
def test_the_frame_count_does_not_come_from_the_progress_line(clip, monkeypatch):
    """진행 줄에 `frame=` 이 없어도 프레임 수를 센다 (고치기 전에는 None 이었다)."""
    monkeypatch.setattr(probe, "run_reading", _without_frame_field(probe.run_reading))

    assert probe_video(FFMPEG, clip).frames == 40


@needs_ffmpeg
def test_the_media_tools_frame_count_does_not_come_from_the_progress_line(clip, monkeypatch):
    """시리즈·음성 어댑터가 쓰는 probe_media 도 같은 방법으로 센다. 길이는 프레임 수로 다시 잰 값이다."""
    monkeypatch.setattr(media_tools, "run_ffmpeg", _without_frame_field(media_tools.run_ffmpeg))

    found = media_tools.probe_media(FFMPEG, clip)

    assert found.frames == 40 and found.duration_ms == 4000
    assert (found.width, found.height, found.fps, found.video_codec) == (64, 114, 10.0, "h264")


@needs_ffmpeg
def test_only_the_packets_of_the_video_stream_are_counted(tmp_path):
    """소리가 있는 파일에서도 영상 스트림의 프레임만 센다 (소리의 패킷은 훨씬 많다)."""
    path = tmp_path / "with-sound.mp4"
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y"]
    cmd += ["-f", "lavfi", "-i", "testsrc2=size=64x114:rate=10:duration=2"]
    cmd += ["-f", "lavfi", "-i", "sine=frequency=440:duration=2"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)]
    subprocess.run(cmd, check=True, capture_output=True)

    found = probe_video(FFMPEG, path)

    assert found.frames == 20 and found.has_audio is True
    assert media_tools.probe_media(FFMPEG, path).frames == 20


@needs_ffmpeg
def test_a_file_that_is_not_a_video_is_an_error_not_a_count(tmp_path):
    path = tmp_path / "not-a-video.mp4"
    path.write_bytes(b"this is not a video" * 50)

    with pytest.raises(probe.VideoToolError):
        probe_video(FFMPEG, path)


# ---- 패킷 목록 읽기 (ffmpeg 없이) ----------------------------------------------------------


def test_packet_lines_are_counted_and_header_lines_are_not():
    listing = (
        "#extradata 0:       46, 0x45401033\n"
        "#software: Lavf61.7.100\n"
        "#tb 0: 1/10240\n"
        "#media_type 0: video\n"
        "#codec_id 0: h264\n"
        "#dimensions 0: 64x114\n"
        "#sar 0: 1/1\n"
        "0,      -2048,          0,     1024,     2528, 0xc82778eb\n"
        "0,      -1024,       2048,     1024,      853, 0x1cd69f3d, F=0x0\n"
        "0,          0,       1024,     1024,      214, 0x6b1a5f0c, F=0x0\n"
    )

    assert count_packets(listing) == 3
    assert count_packets(listing.replace("\n", "\r\n")) == 3  # 줄 끝이 달라도 같다


def test_a_listing_without_packets_counts_zero():
    assert count_packets("#software: Lavf61.7.100\n#tb 0: 1/10240\n") == 0
    assert count_packets("") == 0


def test_no_listing_means_no_count_rather_than_zero():
    """ffmpeg 가 성공했다고 하는데 목록이 없으면 0 이 아니라 None 이다 (세지 못했다는 뜻이다)."""
    code, log, packets = read_video_packets(
        "ffmpeg", "clip.mp4", 5, run=lambda cmd, timeout_s: (0, "출력")
    )

    assert (code, log, packets) == (0, "출력", None)


def test_a_failed_run_gives_no_count():
    code, _, packets = read_video_packets(
        "ffmpeg", "clip.mp4", 5, run=lambda cmd, timeout_s: (1, "오류")
    )

    assert (code, packets) == (1, None)


def test_the_first_video_stream_is_copied_not_decoded():
    """세는 데 드는 시간이 영상의 길이·크기에 거의 기대지 않게, 디코드하지 않고 패킷만 옮겨 적는다."""
    commands = []

    def run(cmd, timeout_s):
        commands.append(list(cmd))
        return 0, ""

    read_video_packets("ffmpeg", "clip.mp4", 5, run=run)

    (cmd,) = commands
    assert cmd[cmd.index("-map") + 1] == "0:v:0" and cmd[cmd.index("-c") + 1] == "copy"
