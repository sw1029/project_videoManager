"""ffmpeg 찾기와 실행: 실행 파일을 찾고, 명령을 돌려 종료 코드와 출력을 돌려주고, 그 빌드에 든 필터를 알려 준다.

찾는 순서 (resolve_ffmpeg): FRAME_VIDEO_FFMPEG -> PATH 의 ffmpeg -> imageio-ffmpeg 패키지(video extra).
ffprobe 없이 ffmpeg 하나로 검사한다 (imageio-ffmpeg 는 ffprobe 를 담고 있지 않다).

같은 판의 ffmpeg 라도 빌드마다 든 필터가 다르다 (선택 라이브러리 없이 빌드하면 그 필터가 빠진다).
꼭 있어야 하는 필터는 available_filters() 로 조립할 때 확인한다 - 쓰는 순간에 실패하지 않게.

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
from functools import lru_cache
from pathlib import Path

from frame.application.errors import AdapterError, ConfigurationError

__all__ = [
    "FfmpegNotRun",
    "VideoToolError",
    "available_filters",
    "filter_names",
    "require_ffmpeg",
    "resolve_ffmpeg",
    "run_ffmpeg",
    "run_reading",
    "tail",
]


FILTERS_TIMEOUT_S = 30.0  # 필터 목록은 파일을 읽지 않는다. 금방 끝난다
# `ffmpeg -filters` 의 한 줄: ' T.C drawtext          V->V       설명'. 머리의 범례 줄은 두 칸 들여 쓴다
_FILTER_LINE = re.compile(r"^ [A-Z.]{3} +(\S+) +\S+->\S+", re.MULTILINE)
# 오류 문구에 싣는 출력의 끝: 줄 수와 글자 수 (tail)
ERROR_TAIL_LINES = 5
ERROR_TAIL_CHARS = 600


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
    proc = _run(cmd, timeout_s, cwd)
    return proc.returncode, proc.stderr.decode("utf-8", errors="replace")


def _run(
    cmd: Sequence[str], timeout_s: float, cwd: str | Path | None = None
) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
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


def run_reading(cmd: Sequence[str], timeout_s: float) -> tuple[int, str]:
    """영상을 읽는 도구의 실행. run_ffmpeg 와 같되, 실행 자체가 안 되면 VideoToolError 로 올린다."""
    try:
        return run_ffmpeg(cmd, timeout_s)
    except FfmpegNotRun as exc:
        raise VideoToolError(str(exc)) from exc


@lru_cache(maxsize=8)
def available_filters(ffmpeg: str, timeout_s: float = FILTERS_TIMEOUT_S) -> frozenset[str]:
    """이 ffmpeg 빌드에 든 필터의 이름 (`ffmpeg -filters`).

    실행하지 못하면 FfmpegNotRun, 목록을 읽지 못하면 VideoToolError 다.
    같은 판이라도 빌드마다 다르다. 예: 6.1 부터 drawtext 는 libharfbuzz 와 함께 빌드해야 들어간다 -
    video extra(imageio-ffmpeg 0.6.0)가 Linux 에 가져오는 정적 빌드에는 없고, Windows · macOS 의 것에는 있다
    (감사 A-43).
    """
    proc = _run([ffmpeg, "-hide_banner", "-filters"], timeout_s)
    if proc.returncode != 0:
        log = proc.stderr.decode("utf-8", errors="replace")
        raise VideoToolError(f"ffmpeg 의 필터 목록을 읽지 못했습니다: {tail(log)}")
    return filter_names(proc.stdout.decode("utf-8", errors="replace"))


def filter_names(listing: str) -> frozenset[str]:
    """`ffmpeg -filters` 의 출력에서 필터의 이름을 고른다."""
    return frozenset(_FILTER_LINE.findall(listing.replace("\r", "")))


def tail(log: str, lines: int = ERROR_TAIL_LINES) -> str:
    """오류 문구에 실을 출력의 끝: 빈 줄을 뺀 마지막 lines 줄을 ' | ' 로 잇는다 (600자까지).

    ffmpeg 는 까닭을 먼저 찍고 ("No such filter: …" · "Could not load font …"),
    그 뒤에 어디서 멈췄는지를 서너 줄로 잇는다 ("Error initializing a simple filtergraph" ·
    "Error opening output file …" · "Error opening output files: …").
    끝의 세 줄만 실으면 까닭이 빠진다. 다섯 줄을 싣는다 (감사 A-43).

    ffmpeg 는 읽지 못한 파일의 경로를 출력한다. 그 파일은 이 서버의 임시 폴더에 있으므로 폴더를 <tmp> 로 바꾼다.
    """
    useful = [line.strip() for line in log.replace("\r", "\n").splitlines() if line.strip()]
    shown = _without_temporary_folder(" | ".join(useful[-lines:]))
    return shown[-ERROR_TAIL_CHARS:] or "(출력 없음)"


def _without_temporary_folder(text: str) -> str:
    """시스템 임시 폴더 아래의 작업 폴더(<임시 폴더>/<이름>)를 <tmp> 로 바꾼다. 구분자는 두 가지 다 본다."""
    parts = [re.escape(part) for part in re.split(r"[\\/]+", tempfile.gettempdir()) if part]
    if not parts:
        return text
    folder = r"[\\/]+".join(parts)
    lead = r"[\\/]+" if tempfile.gettempdir().startswith(("/", "\\")) else ""
    pattern = re.compile(lead + folder + r"[\\/]+[^\\/\s'\":|]+", re.IGNORECASE)
    return pattern.sub("<tmp>", text)
