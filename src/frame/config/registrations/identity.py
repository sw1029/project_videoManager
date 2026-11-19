"""정체성 기준 이미지 추출 (IdentityReferenceExtractor) 의 등록 — US-03, ADR-0015 · ADR-0035.

설정 값 auto 는 여기 오기 전에 풀린다 (container.identity_key: 실제 영상 생성기면 vlm_crop, 아니면 off).
"""

from __future__ import annotations

from frame.adapters.identity import (
    FakeIdentityExtractor,
    FrameIdentityExtractor,
    VlmCropIdentityExtractor,
)
from frame.application.ports import IdentityReferenceExtractor

from ..registry import AdapterRegistry
from ._dependencies import artifact_store, ffmpeg_path, vlm_client_for


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
    # 실제 영상 생성기에서는 기본(auto)으로 켜지는 기능이라, VLM 키가 없으면 끄는 방법까지 알린다 (ADR-0035)
    registry.register(
        IdentityReferenceExtractor,
        "vlm_crop",
        lambda ctx: VlmCropIdentityExtractor(
            artifact_store(ctx),
            ffmpeg_path(ctx),
            vlm_client_for(
                registry,
                ctx,
                feature=f"인물 일관성(FRAME_IDENTITY={ctx.settings.identity} → vlm_crop)",
                off="FRAME_IDENTITY=off",
            ),
            verify_attributes=ctx.settings.identity_verify_attributes,
        ),
    )
