"""테스트·로컬 개발용 생성 어댑터. 실제 영상 대신 생성 조건을 담은 JSON 산출물을 만든다.

- 결정적: 같은 요청 -> 같은 내용.
- 실패 주입: fail_scene_keys / error 로 특정 장면 또는 전체 실패를 재현한다.
- 스파이: requests 에 호출 기록을 남겨 '어떤 장면만 재생성됐는지' 를 단언할 수 있다.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence

from frame.application.errors import GenerationError
from frame.application.ports.artifact_store import ArtifactStore
from frame.application.ports.generator import (
    GeneratedClip,
    SceneGenerationRequest,
    VideoArtifact,
    VideoAssembler,
    VideoGenerator,
)


class FakeVideoGenerator(VideoGenerator):
    def __init__(
        self,
        store: ArtifactStore,
        fail_scene_keys: Iterable[str] = (),
        error: Exception | None = None,
    ) -> None:
        self._store = store
        self._fail_scene_keys = set(fail_scene_keys)
        self._error = error
        self.requests: list[SceneGenerationRequest] = []

    def generate_scene(self, request: SceneGenerationRequest) -> GeneratedClip:
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        if request.scene.scene_key in self._fail_scene_keys:
            raise GenerationError(f"장면 '{request.scene.scene_key}' 생성 실패 (주입된 오류)")
        payload = {
            "scene_key": request.scene.scene_key,
            "fingerprint": request.fingerprint,
            "seed": request.seed,
            "requirements": [
                {"key": r.key, "expected_value": r.expected_value}
                for r in request.scene.requirements
            ],
            "references": [r.image_uri for r in request.references],
        }
        uri = self._store.put(
            f"clips/{request.version_id}/{request.scene.scene_key}.json",
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"),
            "application/json",
        )
        return GeneratedClip(
            scene_key=request.scene.scene_key,
            uri=uri,
            duration_ms=request.scene.duration_ms,
            fingerprint=request.fingerprint,
            metadata={"generator": "fake"},
        )


class FakeVideoAssembler(VideoAssembler):
    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    def assemble(self, version_id: str, clips: Sequence[GeneratedClip]) -> VideoArtifact:
        manifest = {"version_id": version_id, "clips": [c.uri for c in clips]}
        uri = self._store.put(
            f"videos/{version_id}.json",
            json.dumps(manifest, ensure_ascii=False).encode("utf-8"),
            "application/json",
        )
        return VideoArtifact(uri=uri, duration_ms=sum(c.duration_ms for c in clips))
