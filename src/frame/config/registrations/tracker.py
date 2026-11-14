"""객체 추적 (ObjectTracker) 의 등록 — US-03."""

from __future__ import annotations

from frame.adapters.tracker import FakeObjectTracker
from frame.application.ports import ObjectTracker

from ..registry import AdapterRegistry
from ._dependencies import skeleton


def register(registry: AdapterRegistry) -> None:
    registry.register(ObjectTracker, "fake", lambda ctx: FakeObjectTracker())
    # OpenCV 추적기(OpenCvObjectTracker)는 뼈대뿐이다. 구현하면 여기서 그 클래스를 만든다
    registry.register(ObjectTracker, "opencv", skeleton("FRAME_TRACKER", "opencv", working="fake"))
