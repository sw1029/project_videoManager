"""시연 실행기의 실제 영상 모드 (run --video, ADR-0037): OpenRouter 로 실제 영상을 만들고 ffmpeg 로 잇는다.

- 데이터는 .demo/video 에 따로 둔다. 서버는 기동할 때 끝나지 않은 버전을 다시 실행하므로(재시작 복구, ADR-0013)
  가짜 생성기로 쓰던 DB 를 그대로 쓰면 거기 남아 있던 작업이 유료 생성기로 돈다.
- 작업은 백그라운드 스레드에서 돈다 (FRAME_JOBS=thread). 장면 하나에 몇 분이 걸려 요청 안에서 끝낼 수 없고,
  화면은 진행 상태를 물어 가며 보여 준다.
- 셸에서 정한 해상도 · 검수기 · 시간 제한은 그대로 쓴다. 정하지 않았으면 시연에 맞춘 값(VIDEO_DEFAULTS)이다.
- 실행기는 의존성을 설치하기 전에도 돌아야 해서 표준 라이브러리만 쓴다.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from frame.tools import demo_keys

VIDEO_DIR = "video"  # .demo/video: 실제 영상 모드의 DB · 산출물
VIDEO_EXTRA = "video"  # pyproject 의 선택 묶음 (imageio-ffmpeg: ffmpeg 실행 파일이 들어 있다)
# 셸에서 정하지 않았을 때 쓰는 값
VIDEO_DEFAULTS: dict[str, str] = {
    # 720p 의 절반 요금이다 (Wan 3.0: 480p $0.05/초, 720p $0.10/초 — settings.video_resolution 주석)
    "FRAME_VIDEO_RESOLUTION": "480p",
    # 실제 영상에는 실제 검수기: 요구조건 · 일관성은 VLM(OpenAI 키), 수정 버전의 보존율은 SSIM(ffmpeg, 요금 없음)
    "FRAME_CHECKERS": "vlm_requirement,vlm_consistency,ssim_preservation",
    # 생성기 전체의 시간 제한: 영상 하나를 기다리는 시간(FRAME_VIDEO_TIMEOUT_S, 기본 600초)보다 길게 (ADR-0012).
    # 셸에서 FRAME_VIDEO_TIMEOUT_S 를 바꿨으면 그 값 + TIMEOUT_MARGIN_S 를 쓴다 (generator_timeout)
    "FRAME_GENERATOR_TIMEOUT_S": "900",
}
TIMEOUT_MARGIN_S = 300  # 생성기 전체의 제한 = 영상 하나를 기다리는 시간 + 이만큼 (ADR-0012 의 권장)
# 이 모드에서는 셸의 값과 상관없이 정한다
FORCED: dict[str, str] = {
    "FRAME_GENERATOR": "external_api",
    "FRAME_ASSEMBLER": "ffmpeg",
    "FRAME_JOBS": "thread",
}

Ask = Callable[[Sequence[str]], "subprocess.CompletedProcess[str] | None"]


def state_dir(demo_dir: Path) -> Path:
    return demo_dir / VIDEO_DIR


def apply(env: dict[str, str], *, key_file: Path | None) -> None:
    """시연 서버 환경에 실제 영상 모드를 얹는다.

    key_file: 저장한 OpenRouter 키 파일 (그 키가 들어 있을 때만 넘긴다 — 없으면 셸의 FRAME_VIDEO_KEY_FILE 이나
    OPENROUTER_API_KEY 를 서버가 그대로 쓴다).
    """
    for name, value in VIDEO_DEFAULTS.items():
        if not env.get(name, "").strip():
            env[name] = generator_timeout(env) if name == "FRAME_GENERATOR_TIMEOUT_S" else value
    env.update(FORCED)
    if key_file is not None:
        env["FRAME_VIDEO_KEY_FILE"] = str(key_file)


def generator_timeout(env: Mapping[str, str]) -> str:
    """생성기 전체의 시간 제한 기본값: 셸의 FRAME_VIDEO_TIMEOUT_S + 여유, 없거나 읽을 수 없으면 VIDEO_DEFAULTS."""
    try:
        video_timeout = float(env.get("FRAME_VIDEO_TIMEOUT_S", "").strip())
    except ValueError:
        return VIDEO_DEFAULTS["FRAME_GENERATOR_TIMEOUT_S"]
    return (
        f"{video_timeout + TIMEOUT_MARGIN_S:g}"
        if video_timeout > 0
        else VIDEO_DEFAULTS["FRAME_GENERATOR_TIMEOUT_S"]
    )


def configured_ffmpeg(env: Mapping[str, str], root: Path) -> Path | None:
    """FRAME_VIDEO_FFMPEG 로 지정한 실행 파일 (서버처럼 파일이어야 한다. 상대 경로는 서버가 뜨는 root 기준)."""
    configured = env.get("FRAME_VIDEO_FFMPEG", "")  # 서버(Settings)처럼 다듬지 않는다
    if not configured:
        return None
    path = Path(configured)
    return path if path.is_absolute() else root / path


def ffmpeg_source(env: Mapping[str, str], root: Path, runtime_python: Path, ask: Ask) -> str | None:
    """서버가 찾을 ffmpeg (서버의 resolve_ffmpeg 와 같은 순서: FRAME_VIDEO_FFMPEG -> PATH -> imageio-ffmpeg).

    FRAME_VIDEO_FFMPEG 를 정했으면 서버는 그 파일만 본다 — 없으면 PATH 로 넘어가지 않고 None 이다.
    """
    configured = configured_ffmpeg(env, root)
    if configured is not None:
        return str(configured) if configured.is_file() else None
    on_path = shutil.which("ffmpeg")
    if on_path:
        return on_path
    probe = "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"
    # -X utf8: 경로에 코드 페이지 밖 글자(예: cp949 의 é)가 있어도 출력이 깨지지 않게 (-I 는 PYTHONUTF8 을 무시한다)
    done = ask([str(runtime_python), "-I", "-X", "utf8", "-c", probe])
    found = (
        done.stdout.strip().splitlines()[-1].strip()
        if done is not None and done.returncode == 0
        else ""
    )
    return found or None


def problems(
    root: Path,
    env: Mapping[str, str],
    *,
    bundle: bool,
    runtime_python: Path,
    ask: Ask,
    keys_hint: str,
    setup_hint: str,
) -> list[str]:
    """실제 영상 모드를 띄우기 전에 알 수 있는 문제 (서버도 기동 때 거절하지만, 무엇을 할지 먼저 알린다)."""
    found: list[str] = []
    router = demo_keys.OPENROUTER
    key = demo_keys.status(root, router, env)
    if key.active is None:
        seen = "" if key.describe() == "없음" else f" ({key.describe()})"
        found.append(
            f"OpenRouter 키가 없습니다{seen}. {keys_hint} 로 넣으세요 "
            f"(또는 {setup_hint} --video --openrouter-key-file 경로, 환경 변수 {router.env} · {router.file_env})"
        )
    if ffmpeg_source(env, root, runtime_python, ask) is not None:
        return found
    configured = configured_ffmpeg(env, root)
    if configured is not None:
        found.append(
            f"FRAME_VIDEO_FFMPEG 에 지정한 실행 파일이 없습니다: {configured} — 경로를 고치거나 그 변수를 지우세요"
        )
    elif bundle:
        found.append(
            "ffmpeg 가 없습니다. 이 번들에는 ffmpeg 가 들어 있지 않으니 ffmpeg 를 설치해 PATH 에 두세요"
        )
    else:
        found.append(
            f"ffmpeg 가 없습니다. {setup_hint} --video 로 영상 묶음(ffmpeg 포함)을 설치하세요"
        )
    return found


def prepare_hint(*, bundle: bool, setup_hint: str) -> str:
    """실제 영상 모드를 처음 쓰기 전에 할 일 (안내문용)."""
    if bundle:
        return "ffmpeg 가 PATH 에 있어야 합니다 (이 번들에는 없음)"
    return f"처음 한 번 {setup_hint} --video 로 ffmpeg 를 설치"


def notices(env: Mapping[str, str], root: Path) -> list[str]:
    """실행 중 안내: 무엇에 요금이 들고 어디에 저장되는지."""
    resolution = env.get("FRAME_VIDEO_RESOLUTION", "")
    model = env.get("FRAME_VIDEO_MODEL", "").strip() or "기본 모델"
    key = demo_keys.status(root, demo_keys.OPENROUTER, env)
    return [
        f"실제 영상 생성: OpenRouter {model} · {resolution} — 장면마다 요금이 듭니다 (생성을 누를 때만)",
        f"OpenRouter 키: {key.describe()}",
        (
            f"이 모드의 프로젝트 · 영상은 {demo_keys.DEMO_DIR}/{VIDEO_DIR} 에 따로 저장됩니다 "
            "(가짜 생성기로 만든 것과 섞이지 않습니다)"
        ),
        (
            "생성 중에 서버를 끄면 다음 실행 때 이어서 진행합니다 "
            "(이미 제출한 장면은 다시 사지 않고 이어 받습니다, ADR-0013)"
        ),
    ]
