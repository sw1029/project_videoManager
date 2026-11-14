"""ffmpeg 실행기 (adapters/shared/media/ffmpeg).

명령을 돌려 종료 코드와 출력을 돌려주고, 오류 문구에 실을 출력의 끝을 만든다.

실행기는 어떤 명령이든 돌리므로 ffmpeg 없이 파이썬 인터프리터로 확인한다.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

from frame.adapters.shared.media.ffmpeg import (
    FfmpegNotRun,
    available_filters,
    filter_names,
    run_ffmpeg,
    tail,
)
from tests.support.video import find_ffmpeg

FFMPEG = find_ffmpeg()


def _python(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def test_the_exit_code_and_what_was_written_to_stderr_come_back():
    code, log = run_ffmpeg(
        _python("import sys; sys.stderr.write('frame=1\\nboom'); sys.exit(3)"), timeout_s=30
    )

    assert code == 3 and log.replace("\r\n", "\n") == "frame=1\nboom"


def test_the_command_runs_in_the_given_folder(tmp_path):
    code, log = run_ffmpeg(
        _python("import os, sys; sys.stderr.write(os.getcwd())"), timeout_s=30, cwd=tmp_path
    )

    assert code == 0 and log.strip().lower() == str(tmp_path).lower()


def test_a_command_that_does_not_finish_in_time_is_not_run():
    with pytest.raises(FfmpegNotRun, match="0.2초"):
        run_ffmpeg(_python("import time; time.sleep(30)"), timeout_s=0.2)


def test_a_missing_executable_is_not_run(tmp_path):
    with pytest.raises(FfmpegNotRun, match="실행하지 못했습니다") as raised:
        run_ffmpeg([str(tmp_path / "no-such-ffmpeg"), "-version"], timeout_s=5)

    assert raised.value.code == "VIDEO_TOOL_ERROR"
    # 실행 파일의 경로(서버의 폴더 구조)는 문구에 싣지 않는다
    assert str(tmp_path) not in str(raised.value) and "no-such-ffmpeg" not in str(raised.value)


def test_the_tail_is_the_last_lines_that_say_something():
    log = "ffmpeg version 7\r\n\r\n  Input #0\n\n[mp4] moov atom not found\nError opening input\n"

    assert tail(log, lines=3) == "Input #0 | [mp4] moov atom not found | Error opening input"
    assert tail(log, lines=1) == "Error opening input"


def test_the_tail_keeps_the_line_that_says_why():
    """ffmpeg 7 은 까닭을 먼저 찍고, 그 뒤에 어디서 멈췄는지를 서너 줄로 잇는다. 끝의 세 줄만 실으면 까닭이 빠진다.

    (CI 의 Linux 잡에서 화면 문구 합성이 실패했을 때, 오류 문구에 까닭이 없었다 - 감사 A-43.)
    """
    log = (
        "Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'source.mp4':\n"
        "  Stream #0:0[0x1](und): Video: h264 (High), yuv420p, 240x426, 10 fps\n"
        "[AVFilterGraph @ 0x55d0c8a3b6c0] No such filter: 'drawtext'\n"
        "[vost#0:0/libx264 @ 0x55d0c8a3a2c0] Error initializing a simple filtergraph\n"
        "Error opening output file composed.mp4.\n"
        "Error opening output files: Filter not found\n"
    )

    shown = tail(log)

    assert "No such filter: 'drawtext'" in shown
    assert shown.endswith("Error opening output files: Filter not found")


def test_the_tail_of_nothing_says_so_and_a_long_tail_is_cut():
    assert tail("") == "(출력 없음)" and tail(" \n\r\n") == "(출력 없음)"
    assert len(tail("x" * 5000)) == 600


# ---- ffmpeg 가 가진 필터 (감사 A-43) -----------------------------------------------------
# `ffmpeg -filters` 가 찍는 모양 (7.1 의 출력에서 몇 줄)
LISTING = """Filters:
  T.. = Timeline support
  .S. = Slice threading
  A = Audio input/output
  | = Source or sink filter
 TSC aap               AA->A      Apply Affine Projection algorithm to first audio stream.
 ... anullsrc          |->A       Null audio source, return empty audio frames.
 T.C drawbox           V->V       Draw a colored box on the input video.
 T.C drawtext          V->V       Draw text on top of video frames using libfreetype library.
 ... subtitles         V->V       Render text subtitles onto input video using the libass library.
"""


def test_filter_names_are_read_from_the_listing():
    names = filter_names(LISTING)

    assert names == {
        "aap",
        "anullsrc",
        "drawbox",
        "drawtext",
        "subtitles",
    }  # 머리의 범례는 필터가 아니다


def test_a_build_without_a_filter_does_not_list_it():
    """ffmpeg 6.1 부터 drawtext 는 libharfbuzz 가 있어야 빌드된다. 없이 빌드한 ffmpeg 의 목록에는 그 줄이 없다."""
    without = "\n".join(line for line in LISTING.splitlines() if " drawtext " not in line)

    assert "drawtext" not in filter_names(without) and "drawbox" in filter_names(without)


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg 없음 (video extra 미설치)")
def test_a_real_ffmpeg_lists_its_filters():
    assert {"drawbox", "scale", "fps"} <= available_filters(FFMPEG)


def test_an_ffmpeg_that_cannot_be_run_lists_nothing(tmp_path):
    with pytest.raises(FfmpegNotRun, match="실행하지 못했습니다"):
        available_filters(str(tmp_path / "no-such-ffmpeg"))


# ---- 밖으로 나가는 글 (감사 A-29, A-38) ---------------------------------------------------
# tail() 의 결과는 검수 결과의 detail.reason 과 버전의 error_message 로 API 응답에 나간다.


@pytest.mark.parametrize("separator", ["native", "/"], ids=["native-separators", "forward-slashes"])
def test_the_tail_hides_the_servers_temporary_folder(separator):
    folder = str(Path(tempfile.gettempdir()) / "frame-frames-a1b2c3")
    path = str(Path(folder) / "clip.mp4")
    if separator == "/":
        folder, path = folder.replace("\\", "/"), path.replace("\\", "/")
    log = f"[in#0 @ 000001] Error opening input: Invalid data\nError opening input file {path}.\n"

    shown = tail(log)

    assert folder not in shown and "frame-frames-a1b2c3" not in shown
    assert tempfile.gettempdir() not in shown
    assert "<tmp>" in shown and "clip.mp4" in shown and "Invalid data" in shown


def test_the_tail_leaves_other_text_alone():
    log = "Stream #0:0: Video: h264, 480x854, 30 fps\nConversion failed!\n"

    assert tail(log) == "Stream #0:0: Video: h264, 480x854, 30 fps | Conversion failed!"
