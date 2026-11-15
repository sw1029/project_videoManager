"""VLM 의 구조화 응답에서 항목을 '물어본 것' 에 맞추는 공용 규칙.

실제 모델은 응답의 키에 물어본 키 대신 질의의 한 줄을 통째로 돌려줄 때가 있다 (2026-11-12 실호출:
`cup` 대신 `cup (prop): 빨간 머그컵; color: teal`). 키가 정확히 같기를 기대하면 맞는 답을 읽지 못한다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

__all__ = ["match_answers", "requested_key"]


def match_answers(
    items: Sequence[Any], wanted: Sequence[str], field: str = "entity_key"
) -> dict[str, Mapping[str, Any]]:
    """응답 항목을 물어본 키에 맞춘다 (키 -> 항목).

    항목의 field 값이 물어본 키로 시작하면 그 키의 답으로 읽는다. 그래도 맞는 것이 없는데 하나만 묻고
    하나만 받았으면 그 답으로 본다. 그 밖에는 누구의 답인지 알 수 없으므로 쓰지 않는다.
    """
    answers: dict[str, Mapping[str, Any]] = {}
    unmatched: list[Mapping[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        key = requested_key(str(item.get(field) or ""), wanted)
        if key is not None and key not in answers:
            answers[key] = item
        else:
            unmatched.append(item)
    if not answers and len(wanted) == 1 and len(unmatched) == 1:
        answers[wanted[0]] = unmatched[0]
    return answers


def requested_key(raw: str, wanted: Sequence[str]) -> str | None:
    """응답의 키가 가리키는 물어본 키. 키 그대로이거나, 키로 시작하고 바로 뒤가 키의 일부가 아닌 글자일 때."""
    text = raw.strip().lstrip("-").strip()
    # 'cup2' 를 'cup' 보다, 'R10' 을 'R1' 보다 먼저 본다
    for key in sorted(wanted, key=len, reverse=True):
        if not text.startswith(key):
            continue
        rest = text[len(key) :]
        if not rest or not (rest[0].isalnum() or rest[0] == "_"):
            return key
    return None
