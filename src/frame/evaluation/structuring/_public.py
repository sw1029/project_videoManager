"""커밋되는 기록에 남겨도 되는 값: 오류 문구의 절대 경로·사용자 폴더를 가리고, URL 의 계정·질의·조각을 뺀다.

기록(docs/evaluation/structuring/*.json)은 저장소에 커밋하는 파일이다. 그래서 키 파일 위치·사용자 폴더·접속 정보가
남으면 안 된다. 오류 문구는 예외의 자유 문구라 무엇이 들어올지 모르므로, 기록에 넣기 전에 여기를 거친다.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

# 경로의 시작: 드라이브(C:\ · C:/, 앞이 낱말 글자가 아닐 때 — https:// 의 s:/ 는 아니다), UNC(\\), ~\ · ~/, 유닉스 /
_ABSOLUTE_PATH = re.compile(
    r"(?:(?<!\w)[A-Za-z]:[\\/]|\\\\|(?<![\w/])~[\\/]|(?<![\w/:.~-])/(?=[^\s/]))[^\s'\"<>|]*"
)
_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s'\"<>|]+")
_MESSAGE_LIMIT = 300


def public_message(text: str) -> str:
    """커밋되는 기록에 남길 문구.

    - 사용자 폴더는 이름에 빈칸이 있어도 통째로 가린다 (~ 로 바꾼 뒤 아래 규칙이 가린다)
    - 그 밖의 절대 경로는 빈칸 앞까지 <경로> 로 가린다
    - URL 은 남기되 계정(user:pw@)·질의·조각을 뺀다. 어느 주소에서 실패했는지가 오류를 읽는 데 중요하다
    - 길면 자른다
    """
    for home in _home_folders():
        for variant in {home, home.replace("\\", "/")}:
            # 폴더 이름이 이어지면(C:\Users\Al 뒤의 ice) 사용자 폴더가 아니다. 그런 경로는 아래 규칙이 가린다
            boundary = re.escape(variant) + r"(?![\w-]|\.\w)"
            text = re.sub(boundary, "~", text, flags=re.IGNORECASE)
    text = _URL.sub(lambda found: public_url(found.group()), text)
    masked = _ABSOLUTE_PATH.sub("<경로>", text)
    return masked if len(masked) <= _MESSAGE_LIMIT else masked[: _MESSAGE_LIMIT - 1] + "…"


def public_url(value: Any) -> Any:
    """URL 이면 계정(user:pw@)·질의·조각을 뺀 주소, 아니면 그대로 (기록에 접속 정보가 새지 않게)."""
    if not isinstance(value, str):
        return value
    parts = urlsplit(value)
    if not (parts.scheme and parts.netloc):
        return value
    host = parts.hostname or ""
    if ":" in host:  # IPv6 는 대괄호로 감싼다 ([::1]:8001)
        host = f"[{host}]"
    try:
        netloc = f"{host}:{parts.port}" if parts.port else host
    except ValueError:  # 포트가 숫자가 아니다
        netloc = host
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def _home_folders() -> tuple[str, ...]:
    """가릴 사용자 폴더. 뿌리뿐인 짧은 경로('/')는 빼서 문구의 모든 구분자를 바꾸지 않는다."""
    try:
        home = Path.home()
    except RuntimeError:  # 사용자 폴더를 알 수 없는 환경
        return ()
    return (str(home),) if len(home.parts) >= 2 else ()
