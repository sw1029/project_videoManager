"""ffmpeg 실행기: 명령을 돌려 종료 코드와 출력을 돌려준다.

ffmpeg 는 진행 상황과 오류를 모두 stderr 에 쓴다. 부르는 쪽이 종료 코드와 그 출력으로 성공 여부를 판단하고,
실패하면 tail() 로 출력의 끝 몇 줄을 오류 문구에 싣는다. 실행 자체가 안 되면 FfmpegNotRun 이다:
부르는 어댑터가 자기 오류(생성 오류, 영상 도구 오류)로 바꿔 올린다.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

from frame.application.errors import AdapterError

__all__ = ["FfmpegNotRun", "run_ffmpeg", "tail"]


class FfmpegNotRun(AdapterError):
    """ffmpeg 를 실행하지 못했거나 시간 안에 끝나지 않았다 (종료 코드가 없다)."""

    code = "VIDEO_TOOL_ERROR"


def run_ffmpeg(
    cmd: Sequence[str], timeout_s: float, cwd: str | Path | None = None
) -> tuple[int, str]:
    """(종료 코드, stderr 출력). 실행하지 못했거나 timeout_s 안에 끝나지 않으면 FfmpegNotRun."""
    try:
        proc = subprocess.run(
            list(cmd),
            capture_output=True,
            timeout=timeout_s,
            check=False,
            cwd=str(cwd) if cwd is not None else None,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),  # Windows 콘솔 창 안 띄움
        )
    except subprocess.TimeoutExpired as exc:
        raise FfmpegNotRun(f"ffmpeg 가 {timeout_s:g}초 안에 끝나지 않았습니다") from exc
    except OSError as exc:
        raise FfmpegNotRun(f"ffmpeg 를 실행하지 못했습니다: {exc}") from exc
    return proc.returncode, proc.stderr.decode("utf-8", errors="replace")


def tail(log: str, lines: int = 3) -> str:
    """오류 문구에 실을 출력의 끝: 빈 줄을 뺀 마지막 lines 줄을 ' | ' 로 잇는다 (400자까지)."""
    useful = [line.strip() for line in log.replace("\r", "\n").splitlines() if line.strip()]
    return " | ".join(useful[-lines:])[-400:] or "(출력 없음)"
