"""모델에 보내는 질의 글의 작은 조각: 술어를 낱말로, 설명을 붙임말로.

질의 글 자체(지시문 · 응답 형식)는 묻는 어댑터마다 다르고 문장 하나로 판정이 바뀐다 (ADR-0018). 여기에는
여러 어댑터가 글자까지 같이 쓰던 조각만 둔다.
"""

from __future__ import annotations

__all__ = ["described", "predicate_words"]


def predicate_words(predicate: str) -> str:
    """술어를 읽는 낱말로: 'shirt_color' -> 'shirt color'."""
    return predicate.replace("_", " ")


def described(text: str) -> str:
    """이름 뒤에 붙이는 설명: ': 빨간 머그컵'. 설명이 비어 있으면 아무것도 붙이지 않는다."""
    return f": {text.strip()}" if text.strip() else ""
