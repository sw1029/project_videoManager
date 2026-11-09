"""API 키 파일 읽기와 마스킹 (키 내용은 반환값 외에는 어디에도 남기지 않는다)."""

from __future__ import annotations

import re
from pathlib import Path

_KEY_TOKEN = re.compile(r"sk-[A-Za-z0-9_\-]+")


def read_key_file(path: str | Path) -> str | None:
    """키 파일에서 키를 찾는다. 파일이 없거나 키가 없으면 None.

    지원 형식: 키 한 줄(sk-...), 또는 OPENAI_API_KEY=sk-... 같은 KEY=VALUE 줄.
    """
    file = Path(path)
    if not file.is_file():
        return None
    text = file.read_text(encoding="utf-8", errors="ignore")
    match = _KEY_TOKEN.search(text)
    if match:
        return match.group(0)
    for line in text.splitlines():
        if "=" in line:
            value = line.split("=", 1)[1].strip().strip("\"'")
            if value:
                return value
    return None


def mask_key(key: str) -> str:
    return f"{key[:3]}...{key[-4:]}" if len(key) > 8 else "***"
