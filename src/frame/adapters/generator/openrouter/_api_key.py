"""OpenRouter API 키: 찾기, 표시, 헤더에 실을 수 있는지.

키를 찾는 순서는 명시 인자 -> OPENROUTER_API_KEY -> 키 파일이다. 키 파일을 읽는 규칙과 표시 규칙은 공용 모듈
(frame.adapters.shared.api_keys)의 것이다. 이 제공자의 키는 'sk-or-' 로 시작한다.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path

from frame.adapters.shared import api_keys
from frame.application.errors import (
    ConfigurationError,
)

KEY_ENV = "OPENROUTER_API_KEY"
KEY_TOKEN = re.compile(r"sk-or-[A-Za-z0-9_\-]+")


def resolve_openrouter_key(
    explicit: str | None = None,
    env: Mapping[str, str] | None = None,
    key_file: str | Path | None = None,
) -> str:
    """명시 인자 -> OPENROUTER_API_KEY -> 키 파일 순서. 못 찾으면 ConfigurationError (메시지에 키 내용 없음).

    키 파일에서는 'sk-or-' 로 시작하는 값만 받는다. 다른 회사의 키 파일(OpenAI 키가 든 파일 등)을 잘못 지정해도
    그 키를 OpenRouter 로 보내지 않는다.
    """
    if explicit and explicit.strip():
        return explicit.strip()
    source = os.environ if env is None else env
    from_env = (source.get(KEY_ENV) or "").strip()
    if from_env:
        return from_env
    if key_file:
        path = Path(key_file)
        if not path.is_file():
            raise ConfigurationError(f"OpenRouter 키 파일이 없습니다: {path}")
        text = path.read_text(encoding="utf-8", errors="ignore")
        found = api_keys.find_key(text, name=KEY_ENV, token=KEY_TOKEN)
        if found is not None and found.startswith("sk-or-"):
            return found
        raise ConfigurationError(f"키 파일에서 OpenRouter 키(sk-or-...)를 찾지 못했습니다: {path}")
    raise ConfigurationError(
        f"OpenRouter API 키가 없습니다. {KEY_ENV} 환경 변수 또는 FRAME_VIDEO_KEY_FILE 을 설정하세요"
    )


def mask_key(key: str) -> str:
    """진단용 표시. 끝 4자리만 (ADR-0010). 짧은 키는 아무것도 보이지 않는다."""
    return api_keys.mask_key(key)


def fits_in_a_header(key: str) -> bool:
    """HTTP 헤더에 실을 수 있는 키인가: 공백·제어 문자가 없는 ASCII."""
    return key.isascii() and key.isprintable() and not any(char.isspace() for char in key)
