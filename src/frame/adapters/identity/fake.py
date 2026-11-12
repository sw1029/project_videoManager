"""테스트·로컬 개발용 정체성 기준 추출기: 실제 영상을 읽지 않고 1x1 PNG 를 기준 이미지로 저장한다.

- 결정적: 같은 클립·같은 객체 -> 같은 URI.
- 실패 주입: missing_entity_keys(클립에서 못 찾음), error(실행 불가).
- 스파이: requests 로 '어느 클립에서 어떤 객체의 기준을 뽑았는지' 를 단언할 수 있다.
"""

from __future__ import annotations

import base64
from collections.abc import Iterable

from frame.application.ports.artifact_store import ArtifactStore
from frame.application.ports.identity import (
    IdentityExtractionRequest,
    IdentityImage,
    IdentityReferenceExtractor,
)

from .keys import identity_key

# 투명한 1x1 PNG (참조 이미지로 보내도 형식 검사를 통과한다)
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


class FakeIdentityExtractor(IdentityReferenceExtractor):
    def __init__(
        self,
        store: ArtifactStore,
        missing_entity_keys: Iterable[str] = (),
        error: Exception | None = None,
        describe: bool = False,
    ) -> None:
        self._store = store
        self._missing = set(missing_entity_keys)
        self._error = error
        self._describe = describe
        self.requests: list[IdentityExtractionRequest] = []

    def extract(self, request: IdentityExtractionRequest) -> tuple[IdentityImage, ...]:
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        images = []
        for entity in request.entities:
            if entity.entity_key in self._missing:
                continue
            uri = self._store.put(
                identity_key(request.clip.uri, entity.entity_key, "png"), PNG_1X1, "image/png"
            )
            images.append(
                IdentityImage(
                    entity_key=entity.entity_key,
                    image_uri=uri,
                    description=f"{entity.entity_key} 의 모습" if self._describe else "",
                    detail={"extractor": "fake", "scene_key": request.scene.scene_key},
                )
            )
        return tuple(images)
