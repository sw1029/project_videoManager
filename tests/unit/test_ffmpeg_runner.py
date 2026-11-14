"""ffmpeg 실행기 (adapters/shared/media/ffmpeg).

명령을 돌려 종료 코드와 출력을 돌려주고, 오류 문구에 실을 출력의 끝을 만든다.

실행기는 어떤 명령이든 돌리므로 ffmpeg 없이 파이썬 인터프리터로 확인한다.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

from frame.adapters.shared.media.ffmpeg import FfmpegNotRun, run_ffmpeg, tail


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

    assert tail(log) == "Input #0 | [mp4] moov atom not found | Error opening input"
    assert tail(log, lines=1) == "Error opening input"


def test_the_tail_of_nothing_says_so_and_a_long_tail_is_cut():
    assert tail("") == "(출력 없음)" and tail(" \n\r\n") == "(출력 없음)"
    assert len(tail("x" * 5000)) == 400


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
