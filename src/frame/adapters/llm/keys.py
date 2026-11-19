"""OpenAI(호환) API 키: 키 파일 읽기와 표시. 규칙은 adapters/shared/api_keys 에 있다.

키 내용은 반환값 외에는 어디에도 남기지 않는다.
"""

from __future__ import annotations

from pathlib import Path

from frame.adapters.shared import api_keys

KEY_VARIABLE = "OPENAI_API_KEY"
OPENROUTER_PREFIX = "sk-or-"  # 다른 회사(OpenRouter)의 키. OpenAI 주소로는 보내지 않는다
# 키 모양은 공용 모듈의 것이다: 시연 실행기의 키 도우미도 같은 규칙으로 읽는다 (ADR-0037)
_KEY_TOKEN = api_keys.OPENAI_KEY_TOKEN


def read_key_file(path: str | Path) -> str | None:
    """키 파일에서 키를 찾는다. 파일이 없거나 키가 없으면 None.

    지원 형식: 키만 있는 줄(sk-...), 또는 OPENAI_API_KEY=... 줄. 다른 변수의 값은 키로 쓰지 않는다.
    """
    file = Path(path)
    if not file.is_file():
        return None
    text = file.read_text(encoding="utf-8", errors="ignore")
    return api_keys.find_key(text, name=KEY_VARIABLE, token=_KEY_TOKEN)


def mask_key(key: str) -> str:
    """화면·로그에 보일 표시: sk-...끝 네 글자. 짧은 키는 아무것도 보이지 않는다."""
    return api_keys.mask_key(key, prefix="sk-")
