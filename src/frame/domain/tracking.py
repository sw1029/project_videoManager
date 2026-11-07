"""객체 추적 결과 값 객체 (SCENE_ENTITY.track_uri / tracking_status 와 대응)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .enums import TrackingStatus


@dataclass(frozen=True)
class BoundingBox:
    t_ms: int
    x: float  # 0..1 정규화 좌표
    y: float
    w: float
    h: float


@dataclass(frozen=True)
class TrackSegment:
    start_ms: int
    end_ms: int
    boxes: tuple[BoundingBox, ...] = ()


@dataclass
class TrackRecord:
    version_id: str
    scene_key: str
    entity_key: str
    status: TrackingStatus = TrackingStatus.PENDING
    segments: tuple[TrackSegment, ...] = ()
    track_uri: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.version_id, self.scene_key, self.entity_key)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene_key": self.scene_key,
            "entity_key": self.entity_key,
            "status": str(self.status),
            "track_uri": self.track_uri,
            "segments": [
                {
                    "start_ms": s.start_ms,
                    "end_ms": s.end_ms,
                    "boxes": [
                        {"t_ms": b.t_ms, "x": b.x, "y": b.y, "w": b.w, "h": b.h} for b in s.boxes
                    ],
                }
                for s in self.segments
            ],
            "detail": dict(self.detail),
        }

    @classmethod
    def from_dict(cls, version_id: str, data: dict[str, Any]) -> TrackRecord:
        return cls(
            version_id=version_id,
            scene_key=data["scene_key"],
            entity_key=data["entity_key"],
            status=TrackingStatus(data.get("status", TrackingStatus.PENDING)),
            track_uri=data.get("track_uri"),
            segments=tuple(
                TrackSegment(
                    start_ms=int(s["start_ms"]),
                    end_ms=int(s["end_ms"]),
                    boxes=tuple(BoundingBox(**b) for b in s.get("boxes", [])),
                )
                for s in data.get("segments", [])
            ),
            detail=dict(data.get("detail", {})),
        )
