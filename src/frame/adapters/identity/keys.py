"""정체성 기준 이미지의 저장 키 규칙 (추출기들이 함께 쓴다).

클립 URI 마다 폴더를 하나 둔다. 같은 클립·같은 객체면 늘 같은 키라서 다시 뽑아도 URI 가 바뀌지 않고
(생성 캐시·작업 원장 키가 흔들리지 않는다), 다른 클립의 기준 이미지를 덮어쓰지 않는다.
조건 fingerprint 는 같은 조건으로 따로 만든 클립끼리 같으므로 키로 쓰지 않는다.
"""

from __future__ import annotations

import hashlib

IDENTITY_PREFIX = "references/identity"


def identity_key(clip_uri: str, name: str, extension: str) -> str:
    folder = hashlib.sha256(clip_uri.encode("utf-8")).hexdigest()[:16]
    return f"{IDENTITY_PREFIX}/{folder}/{name}.{extension.lstrip('.')}"
