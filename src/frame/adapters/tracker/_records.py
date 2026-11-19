"""OpenCV 추적기의 결과 기록: 객체마다 TrackRecord 와 그 근거(detail).

근거에는 찾는 시각마다 받은 박스·확인 결과·다시 맞췄는지, 놓친 시각, 장면의 VLM 호출 수·토큰이 들어간다.
API 는 detail 을 내보내지 않지만 저장되어, 박스가 왜 그 자리인지 나중에 확인할 수 있다.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from frame.application.ports.tracker import TrackingRequest
from frame.domain.enums import TrackingStatus
from frame.domain.tracking import BoundingBox, TrackRecord, TrackSegment

TRACKER_NAME = "opencv"
WHOLE_FRAME = (0.0, 0.0, 1.0, 1.0)


@dataclass
class TraceLog:
    """객체 하나의 근거: 찾는 시각마다 무엇을 받았는지, 언제 놓쳤는지."""

    keyframes: list[dict[str, Any]] = field(default_factory=list)
    lost_ms: list[int] = field(default_factory=list)


@dataclass
class VlmUsage:
    """장면 하나에서 쓴 VLM 호출."""

    calls: int = 0
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, model: str | None, usage: Mapping[str, int | None]) -> None:
        self.calls += 1
        self.model = model or self.model
        self.input_tokens += int(usage.get("input_tokens") or 0)
        self.output_tokens += int(usage.get("output_tokens") or 0)

    def as_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


def closed_segment(
    start_ms: int, last_ms: int, end_ms: int, frame_ms: float, boxes: Sequence[BoundingBox]
) -> TrackSegment:
    """구간을 닫는다: 마지막으로 따라간 프레임이 끝나는 시각까지 (장면 끝을 넘지 않게)."""
    stop = min(end_ms, last_ms + math.ceil(frame_ms))
    if stop <= start_ms:
        stop = min(end_ms, start_ms + 1)
    return TrackSegment(start_ms, stop, tuple(boxes))


def tracked_record(
    request: TrackingRequest,
    key: str,
    segments: Sequence[TrackSegment],
    trace: TraceLog,
    usage: VlmUsage,
    clip_detail: Mapping[str, Any],
) -> TrackRecord:
    """따라간 결과. 구간이 없으면 FAILED 와 사유. clip_detail: 장면 클립 하나에 대한 값 (설정·컷 시각)."""
    detail: dict[str, Any] = {
        "tracker": TRACKER_NAME,
        **clip_detail,
        "keyframes": trace.keyframes,
        "lost_ms": trace.lost_ms,
        "scene_vlm": usage.as_dict(),  # 장면 하나에서 쓴 VLM 호출 (그 장면의 객체 기록마다 같은 값)
    }
    scene_key = request.scene.scene_key
    if segments:
        return TrackRecord(
            request.version_id,
            scene_key,
            key,
            status=TrackingStatus.COMPLETED,
            segments=tuple(segments),
            track_uri=f"{request.clip.uri}#track={key}",
            detail=detail,
        )
    detail["reason"] = failure_reason(trace)
    return TrackRecord(
        request.version_id, scene_key, key, status=TrackingStatus.FAILED, detail=detail
    )


def background_record(request: TrackingRequest, key: str) -> TrackRecord:
    """배경은 따라갈 물체가 아니다: 장면 전체에 화면 전체 박스 하나 (화면은 종류로 걸러 그리지 않을 수 있다)."""
    scene = request.scene
    segment = TrackSegment(
        scene.planned_start_ms,
        scene.planned_end_ms,
        (BoundingBox(scene.planned_start_ms, *WHOLE_FRAME),),
    )
    return TrackRecord(
        request.version_id,
        scene.scene_key,
        key,
        status=TrackingStatus.COMPLETED,
        segments=(segment,),
        detail={"tracker": TRACKER_NAME, "background": True, "note": "배경은 장면 전체로 둔다"},
    )


def failed_record(request: TrackingRequest, key: str, reason: str) -> TrackRecord:
    return TrackRecord(
        request.version_id,
        request.scene.scene_key,
        key,
        status=TrackingStatus.FAILED,
        detail={"tracker": TRACKER_NAME, "reason": reason},
    )


def failure_reason(trace: TraceLog) -> str:
    if any(k.get("rejected") for k in trace.keyframes):
        return "VLM 이 짚은 박스가 그 객체가 아니라는 확인 결과라 따라가지 않았습니다"
    failures = [str(k["reason"]) for k in trace.keyframes if k.get("reason")]
    return failures[-1] if failures else "화면에서 찾지 못했습니다"
