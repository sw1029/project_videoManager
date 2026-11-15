"""커밋되는 기록 · 결과 파일에 남겨도 되는 글: 자유 문구의 이 컴퓨터 경로를 가리고, URL 의 계정 · 질의 · 조각을 뺀다.

지표 1 평가의 결과 파일과 구조화 골드셋의 기록은 저장소에 커밋한다. 예외 문구 · 검수 사유 같은 자유 문구에는
무엇이 들어올지 모른다 — 키 파일의 위치, 사용자 이름이 든 폴더, 접속 정보. 기록에 넣기 전에 여기를 거친다.
두 평가 도구가 같은 규칙을 쓴다. 전에는 저마다의 규칙이 있었고 서로 다른 것을 놓쳤다 (감사 P-20):
한쪽은 사용자 폴더 밖의 빈칸 든 경로와 따옴표 안의 경로를, 다른 쪽은 홈 · 임시 폴더가 아닌 절대 경로와 주소의 질의를.

가리는 것
- 사용자 폴더: 이름에 빈칸이 있어도 통째로.
- 절대 경로: 드라이브, UNC, 사용자 폴더를 줄인 ~, 유닉스의 뿌리에서 시작하는 것.
  따옴표 안의 경로는 닫는 따옴표까지, 따옴표 없는 경로는 빈칸 · 구두점까지다
  (빈칸 뒤의 낱말에 경로 구분자가 더 있으면 이어진 경로로 본다 — 빈칸이 든 폴더 이름).
- URL 은 남긴다: 어느 주소에서 실패했는지가 오류를 읽는 데 중요하다. 계정(user:pw@) · 질의 · 조각만 뺀다.
  저장소 키(local://…)와 상대 경로는 그대로다.

API 경로처럼 뿌리에서 시작하는 글도 경로로 보고 가린다. 덜 가리는 쪽보다 더 가리는 쪽이 낫다.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

PATH_MASK = "<경로>"

# 경로가 시작하는 곳
_PATH_START = (
    # 드라이브: 앞이 낱말 글자가 아닐 때 (주소 https:// 의 s:/ 는 아니다).
    # repr 로 찍힌 겹친 역슬래시도, file:/// 뒤의 것도 여기서 시작한다
    r"(?:(?<!\w)[A-Za-z]:[\\/]"
    # UNC: 역슬래시 둘 (repr 로 찍히면 넷) 뒤에 서버 이름
    r"|(?<![\w\\/])\\\\(?:\\\\)?(?=[\w$])"
    # 사용자 폴더를 줄인 것
    r"|(?<![\w/])~[\\/]"
    # 유닉스의 절대 경로. 주소의 '://' , 날짜 · 비율의 '/', 상대 경로(./ · a/b)의 '/' 는 아니다
    r"|(?<![\w/\\:.~-])/(?=[\w.]))"
)
# 따옴표 안의 경로는 닫는 따옴표까지 (repr 로 찍힌 OSError 의 파일 이름, 빈칸이 든 이름)
_QUOTED_PATH = re.compile(r"(?P<quote>['\"])" + _PATH_START + r"[^'\"\r\n]*(?P=quote)")
# 따옴표 없는 경로는 빈칸 · 구두점 · 콜론('경로: 메시지' 꼴)까지. 빈칸 뒤의 낱말에 구분자가 더 있으면 이어진 경로다
_PATH_END = r"\s'\",;:)\]}<>|"
_BARE_PATH = re.compile(
    _PATH_START + rf"(?:[^{_PATH_END}]| (?=[^{_PATH_END}]*[\\/]))*",
)
_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s'\"<>|]+")


def public_message(text: str, *, limit: int | None = None) -> str:
    """커밋되는 글에 남길 문구. limit 을 주면 그 길이로 자른다 (끝에 줄임표)."""
    for home in _home_folders():
        for variant in {home, home.replace("\\", "/")}:
            # 폴더 이름이 이어지면(사용자 폴더 Al 뒤의 ice) 사용자 폴더가 아니다. 그런 경로는 아래 규칙이 가린다
            boundary = re.escape(variant) + r"(?![\w-]|\.\w)"
            text = re.sub(boundary, "~", text, flags=re.IGNORECASE)
    text = _URL.sub(lambda found: public_url(found.group()), text)
    text = _QUOTED_PATH.sub(lambda found: f"{found['quote']}{PATH_MASK}{found['quote']}", text)
    text = _BARE_PATH.sub(PATH_MASK, text)
    if limit is not None and len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def public_url(value: Any) -> Any:
    """URL 이면 계정(user:pw@) · 질의 · 조각을 뺀 주소. 뺄 것이 없거나 URL 이 아니면 그대로."""
    if not isinstance(value, str):
        return value
    parts = urlsplit(value)
    if not (parts.scheme and parts.netloc):
        return value
    if not (parts.username or parts.password or parts.query or parts.fragment):
        return value  # 글자 그대로 (저장소 키의 대소문자를 바꾸지 않는다)
    host = parts.hostname or ""
    if ":" in host:  # IPv6 는 대괄호로 감싼다 ([::1]:8001)
        host = f"[{host}]"
    try:
        netloc = f"{host}:{parts.port}" if parts.port else host
    except ValueError:  # 포트가 숫자가 아니다
        netloc = host
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def public_value(value: Any) -> Any:
    """검수의 detail 처럼 중첩된 값: 그 안의 문자열을 public_message 로 가린다 (키 · 숫자 · None 은 그대로)."""
    if isinstance(value, str):
        return public_message(value)
    if isinstance(value, Mapping):
        return {key: public_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [public_value(item) for item in value]
    return value


def _home_folders() -> tuple[str, ...]:
    """가릴 사용자 폴더. 뿌리뿐인 짧은 경로('/')는 빼서 문구의 모든 구분자를 바꾸지 않는다."""
    try:
        home = Path.home()
    except RuntimeError:  # 사용자 폴더를 알 수 없는 환경
        return ()
    return (str(home),) if len(home.parts) > 1 else ()
