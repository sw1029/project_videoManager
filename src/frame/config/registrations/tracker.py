"""객체 추적 (ObjectTracker) 의 등록 — US-03, ADR-0036."""

from __future__ import annotations

from frame.adapters.tracker import FakeObjectTracker, OpenCvObjectTracker, VlmEntityLocator
from frame.application.ports import ObjectTracker

from ..registry import AdapterRegistry, BuildContext
from ._dependencies import artifact_store, vlm_client_for


def register(registry: AdapterRegistry) -> None:
    registry.register(ObjectTracker, "fake", lambda ctx: FakeObjectTracker())
    registry.register(ObjectTracker, "opencv", lambda ctx: _opencv(registry, ctx))


def _opencv(registry: AdapterRegistry, ctx: BuildContext) -> ObjectTracker:
    """프레임마다 따라가는 추적기. OpenCV(tracking extra)나 VLM 키가 없으면 기동 시점에 ConfigurationError."""
    s = ctx.settings
    llm = vlm_client_for(
        registry, ctx, feature="객체 추적(FRAME_TRACKER=opencv)", off="FRAME_TRACKER=fake"
    )
    return OpenCvObjectTracker(
        artifact_store(ctx),
        VlmEntityLocator(llm),
        algorithm=s.tracker_algorithm,
        sample_ms=s.tracker_sample_ms,
        redetect_ms=s.tracker_redetect_ms,
        verify_boxes=s.tracker_verify_boxes,
    )
