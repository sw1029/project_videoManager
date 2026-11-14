"""객체 추적 (ObjectTracker) 의 등록 — US-03."""

from __future__ import annotations

from frame.adapters.tracker import FakeObjectTracker, OpenCvObjectTracker
from frame.application.ports import ObjectTracker

from ..registry import AdapterRegistry


def register(registry: AdapterRegistry) -> None:
    registry.register(ObjectTracker, "fake", lambda ctx: FakeObjectTracker())
    registry.register(ObjectTracker, "opencv", lambda ctx: OpenCvObjectTracker())
