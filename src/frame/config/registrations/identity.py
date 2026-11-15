"""정체성 기준 이미지 추출 (IdentityReferenceExtractor) 의 등록 — US-03, ADR-0015."""

from __future__ import annotations

from frame.adapters.identity import (
    FakeIdentityExtractor,
    FrameIdentityExtractor,
    VlmCropIdentityExtractor,
)
from frame.application.ports import IdentityReferenceExtractor

from ..registry import AdapterRegistry
from ._dependencies import artifact_store, ffmpeg_path, vlm_client


def register(registry: AdapterRegistry) -> None:
    # off 면 추출기가 없다 (기준 이미지를 쓰지 않는다)
    registry.register(IdentityReferenceExtractor, "off", lambda ctx: None)
    registry.register(
        IdentityReferenceExtractor, "fake", lambda ctx: FakeIdentityExtractor(artifact_store(ctx))
    )
    registry.register(
        IdentityReferenceExtractor,
        "frame",
        lambda ctx: FrameIdentityExtractor(artifact_store(ctx), ffmpeg_path(ctx)),
    )
    registry.register(
        IdentityReferenceExtractor,
        "vlm_crop",
        lambda ctx: VlmCropIdentityExtractor(
            artifact_store(ctx),
            ffmpeg_path(ctx),
            vlm_client(registry, ctx),
            verify_attributes=ctx.settings.identity_verify_attributes,
        ),
    )
