"""ffmpeg 실행기: 명령을 돌려 종료 코드와 출력을 돌려준다.

ffmpeg 는 진행 상황과 오류를 모두 stderr 에 쓴다. 부르는 쪽이 종료 코드와 그 출력으로 성공 여부를 판단하고,
실패하면 tail() 로 출력의 끝 몇 줄을 오류 문구에 싣는다. 실행 자체가 안 되면 FfmpegNotRun 이다:
부르는 어댑터가 자기 오류(생성 오류, 영상 도구 오류)로 바꿔 올린다.

이 모듈이 만드는 글은 밖으로 나간다 (검수 결과의 사유, 버전의 오류 문구 → API 응답). 그래서 서버의 폴더 구조를
싣지 않는다: 출력에 든 임시 폴더는 <tmp> 로 바꾸고, 실행 실패 문구에는 실행 파일의 경로를 넣지 않는다.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
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
        # 오류의 원문에는 실행 파일의 경로가 들어 있다. 종류와 사유만 싣는다
        reason = f"{type(exc).__name__}: {exc.strerror or '실행 오류'}"
        raise FfmpegNotRun(f"ffmpeg 를 실행하지 못했습니다 ({reason})") from exc
    return proc.returncode, proc.stderr.decode("utf-8", errors="replace")


def tail(log: str, lines: int = 3) -> str:
    """오류 문구에 실을 출력의 끝: 빈 줄을 뺀 마지막 lines 줄을 ' | ' 로 잇는다 (400자까지).

    ffmpeg 는 읽지 못한 파일의 경로를 출력한다. 그 파일은 이 서버의 임시 폴더에 있으므로 폴더를 <tmp> 로 바꾼다.
    """
    useful = [line.strip() for line in log.replace("\r", "\n").splitlines() if line.strip()]
    return _without_temporary_folder(" | ".join(useful[-lines:]))[-400:] or "(출력 없음)"


def _without_temporary_folder(text: str) -> str:
    """시스템 임시 폴더 아래의 작업 폴더(<임시 폴더>/<이름>)를 <tmp> 로 바꾼다. 구분자는 두 가지 다 본다."""
    parts = [re.escape(part) for part in re.split(r"[\\/]+", tempfile.gettempdir()) if part]
    if not parts:
        return text
    folder = r"[\\/]+".join(parts)
    lead = r"[\\/]+" if tempfile.gettempdir().startswith(("/", "\\")) else ""
    pattern = re.compile(lead + folder + r"[\\/]+[^\\/\s'\":|]+", re.IGNORECASE)
    return pattern.sub("<tmp>", text)
