"""ffmpeg 찾기와 실행: 실행 파일을 찾고, 명령을 돌려 종료 코드와 출력을 돌려준다.

찾는 순서 (resolve_ffmpeg): FRAME_VIDEO_FFMPEG -> PATH 의 ffmpeg -> imageio-ffmpeg 패키지(video extra).
ffprobe 없이 ffmpeg 하나로 검사한다 (imageio-ffmpeg 는 ffprobe 를 담고 있지 않다).

ffmpeg 는 진행 상황과 오류를 모두 stderr 에 쓴다. 부르는 쪽이 종료 코드와 그 출력으로 성공 여부를 판단하고,
실패하면 tail() 로 출력의 끝 몇 줄을 오류 문구에 싣는다. 실행 자체가 안 되면 FfmpegNotRun 이다:
부르는 어댑터가 자기 오류(생성 오류)로 바꿔 올린다. 영상을 읽는 도구(프레임 · 크기 · 유사도)는
run_reading 을 쓴다 - 실행하지 못한 것도 읽지 못한 것도 VideoToolError 다.

이 모듈이 만드는 글은 밖으로 나간다 (검수 결과의 사유, 버전의 오류 문구 → API 응답). 그래서 서버의 폴더 구조를
싣지 않는다: 출력에 든 임시 폴더는 <tmp> 로 바꾸고, 실행 실패 문구에는 실행 파일의 경로를 넣지 않는다.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path

from frame.application.errors import AdapterError, ConfigurationError

__all__ = [
    "FfmpegNotRun",
    "VideoToolError",
    "require_ffmpeg",
    "resolve_ffmpeg",
    "run_ffmpeg",
    "run_reading",
    "tail",
]


class FfmpegNotRun(AdapterError):
    """ffmpeg 를 실행하지 못했거나 시간 안에 끝나지 않았다 (종료 코드가 없다)."""

    code = "VIDEO_TOOL_ERROR"


class VideoToolError(AdapterError):
    """ffmpeg 로 영상을 읽거나 비교하지 못했다."""

    code = "VIDEO_TOOL_ERROR"


def resolve_ffmpeg(explicit: str | None = None) -> str:
    """ffmpeg 실행 파일 경로. 못 찾으면 ConfigurationError (서버 기동 시점에 실패하도록 조립 단계에서 부른다)."""
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


def require_ffmpeg(ffmpeg: str | None) -> str:
    """조립 단계가 넘긴 경로가 비어 있으면 ConfigurationError (ffmpeg 가 꼭 필요한 어댑터의 생성자가 부른다)."""
    if not ffmpeg:
        raise ConfigurationError(
            "ffmpeg 실행 파일이 필요합니다. video extra(imageio-ffmpeg)를 설치하거나 "
            "FRAME_VIDEO_FFMPEG 로 경로를 지정하세요"
        )
    return ffmpeg


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


def run_reading(cmd: Sequence[str], timeout_s: float) -> tuple[int, str]:
    """영상을 읽는 도구의 실행. run_ffmpeg 와 같되, 실행 자체가 안 되면 VideoToolError 로 올린다."""
    try:
        return run_ffmpeg(cmd, timeout_s)
    except FfmpegNotRun as exc:
        raise VideoToolError(str(exc)) from exc


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
