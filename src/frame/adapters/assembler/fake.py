"""가짜 조립기 (FRAME_ASSEMBLER=fake): 영상을 만들지 않고, 이은 순서를 적은 결과 정보(JSON)를 저장한다."""

from __future__ import annotations

import json
from collections.abc import Sequence

from frame.application.ports.artifact_store import ArtifactStore
from frame.application.ports.assembler import VideoAssembler
from frame.domain.artifacts import GeneratedClip, VideoArtifact

__all__ = ["FakeVideoAssembler"]


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
