"""테스트용 추적 어댑터: 장면 구간 전체에 걸친 결정적 박스 시퀀스를 만든다."""

from __future__ import annotations

from collections.abc import Iterable

from frame.application.errors import TrackingError
from frame.application.ports.tracker import ObjectTracker, TrackingRequest
from frame.domain.enums import TrackingStatus
from frame.domain.tracking import BoundingBox, TrackRecord, TrackSegment


class FakeObjectTracker(ObjectTracker):
    def __init__(
        self,
        fail_entity_keys: Iterable[str] = (),
        error: Exception | None = None,
        sample_every_ms: int = 1000,
    ) -> None:
        self._fail_entity_keys = set(fail_entity_keys)
        self._error = error
        self._sample_every_ms = sample_every_ms
        self.requests: list[TrackingRequest] = []

    def track(self, request: TrackingRequest) -> tuple[TrackRecord, ...]:
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        if isinstance(self._error, TrackingError):  # pragma: no cover - 방어적
            raise self._error
        records = []
        for index, entity_key in enumerate(request.entity_keys):
            if entity_key in self._fail_entity_keys:
                records.append(
                    TrackRecord(
                        request.version_id,
                        request.scene.scene_key,
                        entity_key,
                        status=TrackingStatus.FAILED,
                        detail={"reason": "주입된 추적 실패"},
                    )
                )
                continue
            start, end = request.scene.planned_start_ms, request.scene.planned_end_ms
            boxes = tuple(
                BoundingBox(t_ms=t, x=0.1 * (index + 1), y=0.2, w=0.2, h=0.3)
                for t in range(start, end, self._sample_every_ms)
            )
            records.append(
                TrackRecord(
                    request.version_id,
                    request.scene.scene_key,
                    entity_key,
                    status=TrackingStatus.COMPLETED,
                    segments=(TrackSegment(start, end, boxes),),
                    track_uri=f"{request.clip.uri}#track={entity_key}",
                )
            )
        return tuple(records)
