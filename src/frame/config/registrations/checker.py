"""검수 (Checker) 의 등록 — US-03 / US-06. FRAME_CHECKERS 에 적은 키들이 차례로 검수기가 된다.

실제 검수기(ADR-0015·0016·0018)는 ffmpeg 로 클립을 읽는다.
vlm_* 는 이미지를 읽는 모델(FRAME_VLM_MODEL)이 프레임을 판정한다.
"""

from __future__ import annotations

from frame.adapters.checker import (
    FakeConsistencyChecker,
    FakePreservationChecker,
    FakeRequirementChecker,
    SsimPreservationChecker,
    VlmConsistencyChecker,
    VlmPreservationChecker,
    VlmRequirementChecker,
)
from frame.application.ports import Checker

from ..registry import AdapterRegistry
from ._dependencies import artifact_store, ffmpeg_path, vlm_client


def register(registry: AdapterRegistry) -> None:
    registry.register(Checker, "fake_requirement", lambda ctx: FakeRequirementChecker())
    registry.register(Checker, "fake_consistency", lambda ctx: FakeConsistencyChecker())
    registry.register(Checker, "fake_preservation", lambda ctx: FakePreservationChecker())
    # 일관성 (ADR-0015): 객체가 보이는 프레임에서 고정 속성이 유지됐는지 VLM 이 판정한다
    registry.register(
        Checker,
        "vlm_consistency",
        lambda ctx: VlmConsistencyChecker(
            artifact_store(ctx),
            ffmpeg_path(ctx),
            vlm_client(registry, ctx),
            frames_per_scene=ctx.settings.consistency_frames,
            threshold=ctx.settings.consistency_threshold,
        ),
    )
    # 요구 반영 (ADR-0018): 장면마다 VLM 이 요구조건별로 프레임을 판정한다
    registry.register(
        Checker,
        "vlm_requirement",
        lambda ctx: VlmRequirementChecker(
            artifact_store(ctx),
            ffmpeg_path(ctx),
            vlm_client(registry, ctx),
            frames_per_scene=ctx.settings.requirement_frames,
            threshold=ctx.settings.requirement_threshold,
        ),
    )
    # 유지 요소 (ADR-0018): 다시 만든 장면에서 그대로 두라고 한 요소를 VLM 이 부모·자식 프레임으로 판정한다
    registry.register(
        Checker,
        "vlm_preservation",
        lambda ctx: VlmPreservationChecker(
            artifact_store(ctx),
            ffmpeg_path(ctx),
            vlm_client(registry, ctx),
            frames_per_scene=ctx.settings.preservation_frames,
        ),
    )
    # 보존 (ADR-0016): 수정 버전의 장면이 부모 클립을 얼마나 그대로 두었는지 SSIM 으로 잰다
    registry.register(
        Checker,
        "ssim_preservation",
        lambda ctx: SsimPreservationChecker(
            artifact_store(ctx), ffmpeg_path(ctx), threshold=ctx.settings.preservation_threshold
        ),
    )
