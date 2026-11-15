"""테스트 실행 스위치와 환경 격리 규칙 (ADR-0030). 환경 변수 이름과 읽는 방법을 한 곳에 둔다.

- FRAME_LIVE_TESTS=1: 실제 호출(유료) 테스트를 돌린다.
  서버 설정에 쓰는 변수(FRAME_GENERATOR, FRAME_STRUCTURER)만으로는 켜지지 않는다.
- FRAME_REQUIRE_FFMPEG=1: ffmpeg 를 찾지 못하면 건너뛰지 않고 실패한다 (CI 가 쓴다).
- FRAME_REQUIRE_KOREAN_FONT=1: 한글 글꼴을 찾지 못하면 건너뛰지 않고 실패한다 (글꼴을 깐 CI 잡이 쓴다).
- FRAME_REQUIRE_DRAWTEXT=1: ffmpeg 에 글자를 그리는 필터(drawtext)가 없으면 건너뛰지 않고 실패한다 (같은 잡이 쓴다).
- 그 밖의 FRAME_* 와 API 키 변수는 실제 호출이 아닌 테스트에서 지운다: 개발자 셸의 설정·키가 테스트에 새지 않게 한다.
"""

from __future__ import annotations

from collections.abc import Mapping

LIVE_TESTS_VARIABLE = "FRAME_LIVE_TESTS"
REQUIRE_FFMPEG_VARIABLE = "FRAME_REQUIRE_FFMPEG"
REQUIRE_KOREAN_FONT_VARIABLE = "FRAME_REQUIRE_KOREAN_FONT"
REQUIRE_DRAWTEXT_VARIABLE = "FRAME_REQUIRE_DRAWTEXT"
SETTINGS_PREFIX = "FRAME_"
API_KEY_VARIABLES = ("OPENAI_API_KEY", "OPENROUTER_API_KEY")
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def live_tests_enabled(env: Mapping[str, str]) -> bool:
    """실제 호출(유료) 테스트를 돌려도 되는가."""
    return env.get(LIVE_TESTS_VARIABLE, "").strip() == "1"


def ffmpeg_required(env: Mapping[str, str]) -> bool:
    """ffmpeg 가 없을 때 건너뛰지 않고 실패해야 하는가."""
    return env.get(REQUIRE_FFMPEG_VARIABLE, "").strip() == "1"


def missing_ffmpeg_message(env: Mapping[str, str], ffmpeg: str | None) -> str | None:
    """ffmpeg 가 필요하다고 했는데 없으면 그 사유, 아니면 None."""
    if not ffmpeg_required(env) or ffmpeg is not None:
        return None
    return (
        f"{REQUIRE_FFMPEG_VARIABLE}=1 인데 ffmpeg 를 찾지 못했습니다. "
        "pip install -e '.[video]' 로 설치하거나 PATH 에 ffmpeg 를 두세요"
    )


def korean_font_required(env: Mapping[str, str]) -> bool:
    """한글 글꼴이 없을 때 화면 문구 합성 테스트를 건너뛰지 않고 실패해야 하는가."""
    return env.get(REQUIRE_KOREAN_FONT_VARIABLE, "").strip() == "1"


def missing_korean_font_message(env: Mapping[str, str], font: str | None) -> str | None:
    """한글 글꼴이 필요하다고 했는데 없으면 그 사유, 아니면 None."""
    if not korean_font_required(env) or font is not None:
        return None
    return (
        f"{REQUIRE_KOREAN_FONT_VARIABLE}=1 인데 한글 글꼴을 찾지 못했습니다. "
        "글꼴을 설치하거나 (Linux: fonts-noto-cjk) 글꼴 폴더에 한글 글꼴 파일을 두세요"
    )


def drawtext_required(env: Mapping[str, str]) -> bool:
    """ffmpeg 가 글자를 그리지 못할 때 화면 문구 합성 테스트를 건너뛰지 않고 실패해야 하는가."""
    return env.get(REQUIRE_DRAWTEXT_VARIABLE, "").strip() == "1"


def missing_drawtext_message(env: Mapping[str, str], *, problem: str | None) -> str | None:
    """글자를 그리는 ffmpeg 가 필요하다고 했는데 그렇지 않으면 그 사유, 아니면 None.

    problem 은 그릴 수 없는 까닭이다 (tests/support/video.drawtext_problem: 어느 ffmpeg 인지, 필터가 없는지
    목록을 읽지 못했는지). 그릴 수 있으면 None 이다.
    """
    if not drawtext_required(env) or problem is None:
        return None
    return (
        f"{REQUIRE_DRAWTEXT_VARIABLE}=1 인데 글자를 그릴 수 없습니다: {problem}. "
        "video extra 가 Linux 에 가져오는 ffmpeg 에는 drawtext 필터가 없습니다. "
        "drawtext 가 든 ffmpeg 를 PATH 에 두세요 (Debian · Ubuntu: apt install ffmpeg)"
    )


def shell_leak_names(env: Mapping[str, str]) -> list[str]:
    """테스트가 물려받으면 안 되는 환경 변수의 이름: 서버 설정(FRAME_*)과 API 키."""
    return sorted(
        name for name in env if name.startswith(SETTINGS_PREFIX) or name in API_KEY_VARIABLES
    )


def is_loopback(host: object) -> bool:
    return isinstance(host, str) and host in LOOPBACK_HOSTS
