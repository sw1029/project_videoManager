"""클립의 한 프레임을 통째로 정체성 기준 이미지로 쓰는 추출기 (FRAME_IDENTITY=frame, ADR-0015).

VLM 없이 ffmpeg 만으로 돈다. 화면 전체를 넘기므로 객체의 모습뿐 아니라 그 장면의 구도·배경까지 참조로 딸려 간다
(다음 장면이 앞 장면을 닮게 된다). 객체만 잘라 넘기려면 vlm_crop 을 쓴다.
"""

from __future__ import annotations

from frame.adapters.video_tools import extract_frame, require_ffmpeg
from frame.application.ports.artifact_store import ArtifactStore
from frame.application.ports.identity import (
    IdentityExtractionRequest,
    IdentityImage,
    IdentityReferenceExtractor,
)

from .keys import identity_key


class FrameIdentityExtractor(IdentityReferenceExtractor):
    def __init__(
        self,
        store: ArtifactStore,
        ffmpeg: str | None,
        *,
        position: float = 0.5,
        max_side: int = 1024,
        min_output_side: int = 480,
    ) -> None:
        self._store = store
        self._ffmpeg = require_ffmpeg(ffmpeg)
        self.position = min(1.0, max(0.0, position))  # 클립 길이 중 프레임을 뽑을 위치 (0..1)
        self.max_side = max_side
        self.min_output_side = min_output_side  # 영상 모델이 받는 참조 이미지의 최소 크기를 넘긴다

    def extract(self, request: IdentityExtractionRequest) -> tuple[IdentityImage, ...]:
        if not request.entities:
            return ()
        at_ms = int(request.clip.duration_ms * self.position)
        frame = extract_frame(
            self._ffmpeg,
            self._store.get(request.clip.uri),
            at_ms,
            max_side=self.max_side,
            min_side=self.min_output_side,
        )
        # 화면 전체 한 장을 그 장면의 모든 객체가 함께 쓴다 (생성기는 같은 이미지를 한 번만 보낸다)
        uri = self._store.put(
            identity_key(request.clip.uri, "frame", "jpg", frame.data),
            frame.data,
            frame.media_type,
        )
        return tuple(
            IdentityImage(
                entity_key=entity.entity_key,
                image_uri=uri,
                detail={"extractor": "frame", "frame_ms": at_ms},
            )
            for entity in request.entities
        )
