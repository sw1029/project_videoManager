"""객체 추적 결과 -> 응답 스키마 (US-03, ADR-0036). 그 밖의 변환은 mappers.py 에 있다.

박스의 t_ms 는 영상 타임라인 시각이고 좌표는 화면에 대한 비율이다 (ObjectTracker 포트 계약). 그대로 옮긴다.
"""

from __future__ import annotations

from dataclasses import asdict

from frame.application.dto import EntityTracksView, VersionTracksView
from frame.domain.tracking import TrackRecord

from . import schemas as s


def tracks_out(view: EntityTracksView) -> s.EntityTracksOut:
    return s.EntityTracksOut(
        version_id=view.version_id,
        entity_key=view.entity_key,
        records=[track_record_out(t) for t in view.records],
    )


def version_tracks_out(view: VersionTracksView) -> s.VersionTracksOut:
    return s.VersionTracksOut(
        version_id=view.version_id, records=[track_record_out(t) for t in view.records]
    )


def track_record_out(record: TrackRecord) -> s.TrackRecordOut:
    return s.TrackRecordOut(
        scene_key=record.scene_key,
        entity_key=record.entity_key,
        status=str(record.status),
        track_uri=record.track_uri,
        segments=[
            s.TrackSegmentOut(
                start_ms=seg.start_ms,
                end_ms=seg.end_ms,
                boxes=[s.BoxOut(**asdict(b)) for b in seg.boxes],
            )
            for seg in record.segments
        ],
    )
