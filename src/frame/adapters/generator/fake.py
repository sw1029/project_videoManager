"""테스트·로컬 개발용 생성 어댑터. 실제 영상 대신 생성 조건을 담은 JSON 산출물을 만든다.

- 결정적: 같은 요청 -> 같은 내용.
- 실패 주입: fail_scene_keys / error 로 특정 장면 또는 전체 실패를 재현한다.
- 스파이: requests 에 호출 기록을 남겨 '어떤 장면만 재생성됐는지' 를 단언할 수 있다.
- 수정 지시(ADR-0017): 요청에 수정 지시가 오면 산출물에 기록한다 (directive).
- 부모 클립(ADR-0016): 요청에 부모 클립이 오면 기록만 하고(parent_clip_uri) 새로 만든다.
  edits_parent=True 면 부모 클립을 편집하는 생성기 흉내를 낸다: 결과 키에 부모 클립이 들어가고 edited_parent 가 참이다.
- 미리보기(ADR-0020): 편집 흉내를 내고 부모 클립이 있으면 EDIT, 아니면 GENERATE. 어림값은 없다. previews 에 기록한다.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence

from frame.application.errors import GenerationError
from frame.application.ports.artifact_store import ArtifactStore
from frame.application.ports.generator import (
    GeneratedClip,
    ProductionMethod,
    ProductionPreview,
    SceneGenerationRequest,
    VideoArtifact,
    VideoAssembler,
    VideoGenerator,
)
from frame.domain.hashing import canonical_hash


class FakeVideoGenerator(VideoGenerator):
    def __init__(
        self,
        store: ArtifactStore,
        fail_scene_keys: Iterable[str] = (),
        error: Exception | None = None,
        edits_parent: bool = False,
    ) -> None:
        self._store = store
        self._fail_scene_keys = set(fail_scene_keys)
        self._error = error
        self._edits_parent = edits_parent
        self.requests: list[SceneGenerationRequest] = []
        self.previews: list[SceneGenerationRequest] = []

    def result_key(self, request: SceneGenerationRequest) -> str:
        """편집 흉내를 낼 때는 결과가 부모 클립에도 달려 있다 (ADR-0016)."""
        if self._edits_parent and request.parent is not None:
            return canonical_hash(
                {"base": request.generation_fingerprint, "parent_clip": request.parent.clip.uri}
            )
        return request.generation_fingerprint

    def preview(self, request: SceneGenerationRequest) -> ProductionPreview:
        self.previews.append(request)
        edits = self._edits_parent and request.parent is not None
        return ProductionPreview(ProductionMethod.EDIT if edits else ProductionMethod.GENERATE)

    def generate_scene(self, request: SceneGenerationRequest) -> GeneratedClip:
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        if request.scene.scene_key in self._fail_scene_keys:
            raise GenerationError(f"장면 '{request.scene.scene_key}' 생성 실패 (주입된 오류)")
        payload: dict[str, object] = {
            "scene_key": request.scene.scene_key,
            "fingerprint": request.fingerprint,
            "seed": request.seed,
            "requirements": [
                {"key": r.key, "expected_value": r.expected_value}
                for r in request.scene.requirements
            ],
            "references": [r.image_uri for r in request.references],
        }
        metadata: dict[str, object] = {"generator": "fake"}
        if request.identity_references:
            payload["identity_references"] = [r.image_uri for r in request.identity_references]
        if request.directive is not None:
            payload["directive"] = {
                "kind": str(request.directive.kind),
                "enforce": [f"{r.key}={r.expected_value}" for r in request.directive.enforce],
                "keep": [item.to_dict() for item in request.directive.keep],
            }
        if request.parent is not None:
            payload["parent_clip_uri"] = request.parent.clip.uri
            metadata["parent_clip_uri"] = request.parent.clip.uri
            metadata["edited_parent"] = self._edits_parent
            metadata["edit"] = {"applied": self._edits_parent}  # 공통 기록 (포트 문서)
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
            metadata=metadata,
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
