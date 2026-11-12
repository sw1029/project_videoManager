"""정체성 기준 이미지의 저장 키 규칙 (추출기들이 함께 쓴다).

클립 URI 마다 폴더를 하나 둔다. 다른 클립의 기준 이미지를 덮어쓰지 않는다
(조건 fingerprint 는 같은 조건으로 따로 만든 클립끼리 같으므로 키로 쓰지 않는다).
파일 이름에는 이미지 내용의 해시를 붙인다. 같은 클립·같은 객체에서 같은 이미지가 나오면 URI 가 같고,
다시 뽑았더니 다른 이미지가 나오면(VLM 이 다른 박스를 짚음) URI 도 달라진다. 생성 캐시·작업 원장 키는
기준 이미지의 URI 로 만들어지므로, URI 가 같은데 내용이 다른 일이 없어야 다른 참조로 만든 결과를 섞지 않는다.
"""

from __future__ import annotations

import hashlib

IDENTITY_PREFIX = "references/identity"


def identity_key(clip_uri: str, name: str, extension: str, content: bytes) -> str:
    folder = hashlib.sha256(clip_uri.encode("utf-8")).hexdigest()[:16]
    digest = hashlib.sha256(content).hexdigest()[:10]
    return f"{IDENTITY_PREFIX}/{folder}/{name}-{digest}.{extension.lstrip('.')}"
