"""시리즈·음성 어댑터가 쓰는 미디어 도구 (adapters/shared/media/media_tools) 의 실행과 오류 문구.

실행은 공용 실행기(shared/media/ffmpeg)가 하고, 이 모듈은 그 오류를 MediaToolError 로 바꿔 올린다 —
이 모듈을 쓰는 어댑터는 MediaToolError 하나만 잡아 자기 포트의 오류로 바꾼다.

실행기는 어떤 명령이든 돌리므로 ffmpeg 없이 파이썬 인터프리터로 확인한다.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

from frame.adapters.shared.media import media_tools
from frame.adapters.shared.media.media_tools import MediaToolError, run_ffmpeg


def _python(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def test_the_exit_code_and_what_was_written_to_stderr_come_back():
    code, log = run_ffmpeg(
        _python("import sys; sys.stderr.write('boom'); sys.exit(3)"), timeout_s=30
    )

    assert (code, log) == (3, "boom")


def test_a_command_that_does_not_finish_in_time_is_a_media_tool_error():
    with pytest.raises(MediaToolError, match="0.2초") as raised:
        run_ffmpeg(_python("import time; time.sleep(30)"), timeout_s=0.2)

    assert raised.value.code == "MEDIA_TOOL_ERROR"


def test_a_missing_executable_is_a_media_tool_error_that_does_not_show_its_path(tmp_path):
    with pytest.raises(MediaToolError, match="실행하지 못했습니다") as raised:
        run_ffmpeg([str(tmp_path / "no-such-ffmpeg"), "-version"], timeout_s=5)

    assert raised.value.code == "MEDIA_TOOL_ERROR"
    # 실행 파일의 경로(서버의 폴더 구조)는 문구에 싣지 않는다
    assert str(tmp_path) not in str(raised.value) and "no-such-ffmpeg" not in str(raised.value)


# ---- 밖으로 나가는 글 --------------------------------------------------------------------------
# 이 모듈의 오류 문구는 어댑터의 포트 오류(AudioError · CompositionError · VideoAnalysisError)에 실려 밖으로 나간다.
# ffmpeg 는 읽지 못한 파일의 경로를 출력에 찍고, 그 파일은 이 서버의 임시 폴더에 있다.


@pytest.mark.parametrize(
    "read",
    [
        lambda path: media_tools.detect_cuts("ffmpeg", path),
        lambda path: media_tools.detect_silences("ffmpeg", path),
        lambda path: media_tools.probe_media("ffmpeg", path),
    ],
    ids=["cuts", "silences", "probe"],
)
def test_error_text_hides_the_servers_temporary_folder(read, monkeypatch):
    path = Path(tempfile.gettempdir()) / "frame-probe-a1b2c3" / "source"
    output = (
        f"[in#0 @ 000001] Error opening input: Invalid data\nError opening input file {path}.\n"
    )
    monkeypatch.setattr(media_tools, "run_ffmpeg", lambda cmd, timeout_s, cwd=None: (1, output))

    with pytest.raises(MediaToolError) as raised:
        read(path)

    shown = str(raised.value)
    assert "frame-probe-a1b2c3" not in shown and tempfile.gettempdir() not in shown
    assert "<tmp>" in shown and "Invalid data" in shown
