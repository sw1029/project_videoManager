"""API 키 다루기: 키 파일에서 키를 찾고, 화면·로그에 보일 표시를 만든다.

키 내용은 반환값 말고는 어디에도 남기지 않는다 (예외·로그·표시).

키 파일에서 찾는 규칙 (find_key) — 키 파일은 사람이 손으로 쓴 메모일 수도 .env 일 수도 있다.
1. `이름=값` 줄 가운데 이름이 찾는 변수(예: OPENAI_API_KEY)인 줄이 있으면 그 값이다
   (앞의 export, 값의 따옴표, 뒤의 주석을 허용한다).
2. 없으면 키 모양의 토큰(예: sk-...)을 찾는다. 다만 주석 줄과, 다른 환경 변수에 값을 주는 줄(대문자 이름=값)은
   보지 않는다. 그 줄의 값은 다른 서비스의 키이거나 다른 비밀이다.
3. 그래도 없으면 None 이다. 이름이 다른 줄의 값을 키로 쓰지 않는다 (.env 를 키 파일로 줘도 다른 비밀이 나가지 않는다).
"""

from __future__ import annotations

import re

__all__ = ["find_key", "mask_key"]

_ASSIGNMENT = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
_ENVIRONMENT_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
# 표시는 끝 네 글자다. 키가 이보다 짧으면 네 글자가 키의 큰 부분이라 아무것도 보이지 않는다
_SHOWN = 4
_SHORTEST_SHOWN_KEY = 16


def find_key(text: str, *, name: str, token: re.Pattern[str]) -> str | None:
    """키 파일의 내용에서 키를 찾는다. name 은 찾는 변수 이름, token 은 키 모양. 없으면 None."""
    loose: list[str] = []  # 이름 없이 키가 적혀 있을 수 있는 줄
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        assignment = _ASSIGNMENT.match(line)
        if assignment is not None:
            variable, value = assignment.groups()
            if variable == name:
                found = _value(value, token)
                if found:
                    return found
                continue
            if _ENVIRONMENT_NAME.match(variable):
                continue  # 다른 환경 변수의 값: 다른 서비스의 키이거나 다른 비밀이다
        loose.append(line)
    for line in loose:
        match = token.search(line)
        if match is not None:
            return match.group(0)
    return None


def _value(raw: str, token: re.Pattern[str]) -> str | None:
    """`이름=값` 의 값. 키 모양의 토큰이 있으면 그것, 없으면 따옴표·뒤의 주석을 뗀 글 (로컬 서버의 키)."""
    match = token.search(raw)
    if match is not None:
        return match.group(0)
    value = raw.split(" #", 1)[0].strip().strip("\"'").strip()
    return value or None


def mask_key(key: str, *, prefix: str = "") -> str:
    """화면·로그에 보일 표시: 끝 네 글자만 (ADR-0010). 짧은 키는 아무것도 보이지 않는다.

    prefix 는 키의 형식을 알리는 접두사("sk-")다. 키가 실제로 그것으로 시작할 때만 함께 보인다
    (접두사 자리에 키의 앞 글자를 그대로 보이면 비밀의 일부가 나간다).
    """
    if len(key) < _SHORTEST_SHOWN_KEY:
        return "***"
    shown = prefix if prefix and key.startswith(prefix) else ""
    return f"{shown}...{key[-_SHOWN:]}"
