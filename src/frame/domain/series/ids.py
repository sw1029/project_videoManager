"""테마 · 편 문서의 id 규칙. 도메인이 검사하는 것과 저장소가 받는 것이 같은 식이다.

id 는 저장 키의 한 마디가 된다 (themes/<id>/theme.json, episodes/<id>/episode.json). 그래서 받는 글자가 좁다:
점을 받으면 'index.json' 이 색인 파일과 부딪히고, 대문자를 받으면 대소문자를 가리지 않는 파일 시스템에서
'THM_A' 와 'thm_a' 가 같은 문서를 연다 (감사 BTS-14).

테마는 스스로 이 규칙을 검사하고 (Theme.validate), 저장소는 테마와 편의 id 를 이 규칙으로 거른다.
규칙을 두 곳에 따로 적으면 검사를 통과한 테마가 저장에서 거절될 수 있다 — 그래서 여기 한 곳에 둔다.
"""

from __future__ import annotations

import re

__all__ = ["DOCUMENT_ID_PATTERN"]

# 소문자 · 숫자로 시작, 소문자 · 숫자 · 밑줄 · 붙임표, 64자까지. fullmatch 로 쓴다 (match 는 줄 끝의 \n 앞에서도 맞는다)
DOCUMENT_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
