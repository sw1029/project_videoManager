"""OpenCV 로 프레임마다 객체를 따라가는 추적기 (FRAME_TRACKER=opencv, ADR-0036).

흐름 (장면 클립 하나)
1. 클립을 처음부터 한 프레임씩 읽는다 (cv2.VideoCapture — opencv 휠에 든 FFmpeg).
2. 앞 프레임과 색 분포가 뚝 달라지면(상관 < CUT_SIMILARITY) 컷으로 본다: 따라가던 구간을 모두 끝내고 그 프레임에서
   바로 다시 찾는다. 생성 모델은 한 장면 클립 안에도 컷을 넣고, 컷 뒤의 추적기는 비슷한 색의 엉뚱한 것에 붙는다.
3. 찾는 시각(redetect_ms 간격, 첫 프레임 포함)마다 장면의 객체를 모두 VLM 에게 찾게 한다. 박스 확인을 켰으면 짚은
   박스를 잘라 그 객체가 맞는지 한 번 더 묻고, 아니라는(no) 박스는 버린다.
4. 아직 따라가지 않는 객체는 그 박스로 추적기(CSRT · KCF · MIL)를 시작해 다음 프레임부터 따라간다.
   이미 따라가는 객체는 VLM 박스와 견주어, 맞다고 확인(yes)된 박스와 겹침(IoU)이 REANCHOR_IOU 보다 작으면 그 박스로
   다시 맞춘다 (확대·축소·미끄러짐). 비슷하면 그대로 둔다 — 호출마다 흔들리는 VLM 박스로 잘 따라가던 것을 바꾸지 않게.
   VLM 이 보이지 않는다고 분명히 답하면 그 구간을 끝낸다 — 화면을 떠난 객체의 박스가 다른 것에 붙어 남지 않게.
5. 추적기가 놓치거나 박스가 너무 작아져도 그 구간을 끝내고 다음 찾는 시각에 다시 찾는다.
6. 박스는 sample_ms 간격으로 남긴다. 시각은 영상 타임라인 기준(장면 시작 + 클립 안 시각)이라, 재생 중인 영상의
   시각으로 바로 찾아 그릴 수 있다.

정한 것
- 새로 시작하는 박스는 분명히 틀린 것(no)만 거르고(작은 물건을 놓치지 않게), 따라가던 것을 옮기는 박스는 맞다(yes)여야
  한다. 2026-11-19 실측: 컵 클로즈업에서 카메라가 다가가자 CSRT 박스가 크기를 못 따라가 컵 아래 카운터로 미끄러졌다.
- 컷은 VLM 의 '보이지 않음' 답과 따로 찾는다. 같은 실측에서 고양이 장면이 강아지 클로즈업으로 넘어가는 컷 뒤에 고양이
  박스가 강아지 위에 남았는데, VLM 은 고양이가 보이지 않는다고 답하지 않았다 (컷 뒤 화면 귀퉁이에 고양이 발이 보인다).
  컷 찾기를 넣은 뒤에는 그 컷(7.17초)에서 구간이 끝나고, 컷 프레임에서 짚은 발 박스는 확인에서 걸러졌다.
- 배경(BACKGROUND)은 따라가지 않는다: VLM 에 묻지 않고 장면 전체에 화면 전체 박스 하나로 둔다 (_records.py).
- 영상이 아닌 클립(가짜 생성기의 JSON)이나 열 수 없는 클립은 객체마다 FAILED 와 사유를 남긴다 (파이프라인은 계속).
- 찾기 호출의 실패(키 없음·응답 해석 실패 등 LLMError)는 그 시각에 못 찾은 것으로 보고 사유를 남긴다.
  확인 호출만 실패하면 확인하지 못한 박스(unchecked)로 새로 시작은 하되, 따라가던 것을 옮기지는 않는다.
- 추적은 긴 변 TRACK_MAX_SIDE 로 줄인 프레임에서 한다 (박스는 비율이라 결과는 같다).
  VLM 에는 원본에 가까운 프레임을 준다.
"""

from __future__ import annotations

import importlib
import logging
import math
import os
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from frame.adapters.shared.media.frames import Box
from frame.application.errors import AdapterError, ConfigurationError, LLMError
from frame.application.ports.artifact_store import ArtifactStore
from frame.application.ports.llm import ImageInput
from frame.application.ports.tracker import ObjectTracker, TrackingRequest
from frame.domain.enums import EntityKind
from frame.domain.ir import EntitySpec, SceneSpec
from frame.domain.tracking import BoundingBox, TrackRecord, TrackSegment

from ._frames import (
    Ratio,
    color_signature,
    crop,
    jpeg,
    normalized,
    overlap,
    pixel_rect,
    rounded,
    shrink,
    signature_similarity,
)
from ._records import (
    TraceLog,
    VlmUsage,
    background_record,
    closed_segment,
    failed_record,
    tracked_record,
)
from .locator import EntityLocator

log = logging.getLogger(__name__)

# 설정 값 -> 그 추적기를 만드는 OpenCV 함수 이름 (CSRT · KCF 는 contrib 판에만 있다)
ALGORITHMS: Mapping[str, str] = {
    "csrt": "TrackerCSRT_create",
    "kcf": "TrackerKCF_create",
    "mil": "TrackerMIL_create",
}
TRACK_MAX_SIDE = 640  # 따라갈 때 프레임의 긴 변(px). 줄일수록 빠르다
LOCATE_MAX_SIDE = 1024  # VLM 에 보이는 프레임의 긴 변(px)
CROP_PADDING = 0.1  # 확인용으로 자를 때 박스 둘레에 더하는 여백 (박스 크기에 대한 비율)
CROP_MIN_SIDE = 160  # 확인용 이미지의 짧은 변 하한(px)
MIN_TRACKED_SIDE = 0.01  # 따라가던 박스의 가로·세로가 화면의 1% 밑으로 줄면 놓친 것으로 본다
REANCHOR_IOU = 0.6  # 따라가던 박스와 확인된 VLM 박스의 겹침이 이보다 작으면 VLM 박스로 다시 맞춘다
# 연속 프레임의 색 분포 상관이 이보다 낮으면 컷(화면이 통째로 바뀜)으로 본다. 같은 장면의 움직임·확대는 0.8 위에 있다
CUT_SIMILARITY = 0.5
FALLBACK_FPS = 30.0  # 클립이 fps 를 알려 주지 않을 때 (정규화한 클립은 30fps, FRAME_VIDEO_FPS)
MAX_FPS = 240.0
JPEG_QUALITY = 90
UNVERIFIED = "unverified"  # 박스 확인을 끈 설정의 판정 (확인하지 않고 믿는다)
TRUSTED_TO_MOVE = frozenset({"yes", UNVERIFIED})  # 따라가던 것을 옮겨도 되는 판정


def load_cv2() -> Any:
    """cv2 (tracking extra 의 opencv-contrib-python-headless). 없으면 기동 시점의 ConfigurationError."""
    try:
        return importlib.import_module("cv2")
    except ImportError as exc:
        raise ConfigurationError(
            'FRAME_TRACKER=opencv 에는 OpenCV 가 필요합니다: pip install -e ".[tracking]" '
            "(opencv-contrib-python-headless — CSRT · KCF 추적기는 contrib 판에만 있습니다)"
        ) from exc


def tracker_factory(cv2: Any, algorithm: str) -> Any:
    """그 판의 OpenCV 에서 추적기를 만드는 함수. 없는 이름이거나 그 판에 없으면 ConfigurationError."""
    name = ALGORITHMS.get(algorithm)
    if name is None:
        raise ConfigurationError(
            f"FRAME_TRACKER_ALGORITHM 은 {', '.join(ALGORITHMS)} 가운데 하나입니다: {algorithm!r}"
        )
    create = getattr(cv2, name, None) or getattr(getattr(cv2, "legacy", None), name, None)
    if create is None:
        raise ConfigurationError(
            f"설치된 OpenCV 에 {algorithm.upper()} 추적기가 없습니다. "
            "opencv-contrib-python-headless 를 설치하세요 (tracking extra)"
        )
    return create


@dataclass
class _Live:
    """따라가는 중인 객체 하나 (지금 구간)."""

    tracker: Any
    start_ms: int
    last_ms: int
    next_sample_ms: int
    current: Ratio  # 마지막 프레임의 박스
    boxes: list[BoundingBox]

    def closed(self, end_ms: int, frame_ms: float) -> TrackSegment:
        return closed_segment(self.start_ms, self.last_ms, end_ms, frame_ms, self.boxes)


@dataclass
class _Scene:
    """장면 하나를 따라가는 동안의 상태."""

    end_ms: int
    frame_ms: float
    live: dict[str, _Live]
    segments: dict[str, list[TrackSegment]]
    logs: dict[str, TraceLog]
    usage: VlmUsage
    cuts_ms: list[int]  # 클립 안의 컷 시각 (영상 타임라인)

    def end(self, key: str, t: int) -> None:
        """그 객체의 지금 구간을 끝낸다 (놓쳤거나 보이지 않거나 컷)."""
        state = self.live.pop(key, None)
        if state is not None:
            self.logs[key].lost_ms.append(t)
            self.segments[key].append(state.closed(self.end_ms, self.frame_ms))


class _ClipUnreadable(Exception):
    """영상으로 읽을 수 없는 클립 (객체마다 FAILED 로 남긴다)."""


class OpenCvObjectTracker(ObjectTracker):
    def __init__(
        self,
        store: ArtifactStore,
        locator: EntityLocator,
        *,
        algorithm: str,
        sample_ms: int,
        redetect_ms: int,
        verify_boxes: bool,
    ) -> None:
        self._cv2 = load_cv2()
        self.algorithm = algorithm.strip().lower()
        # 판에 그 추적기가 있는지 기동 때 본다
        self._create = tracker_factory(self._cv2, self.algorithm)
        if sample_ms <= 0:
            raise ConfigurationError(f"FRAME_TRACKER_SAMPLE_MS 는 0 보다 커야 합니다: {sample_ms}")
        if redetect_ms < 0:
            raise ConfigurationError(
                f"FRAME_TRACKER_REDETECT_MS 는 0 이상이어야 합니다: {redetect_ms}"
            )
        self._store = store
        self._locator = locator
        self.sample_ms = sample_ms
        self.redetect_ms = redetect_ms
        self.verify_boxes = verify_boxes

    def track(self, request: TrackingRequest) -> tuple[TrackRecord, ...]:
        specs = {e.entity_key: e for e in request.entities}
        entities = tuple(specs.get(key) or EntitySpec(key) for key in request.entity_keys)
        movers = [e for e in entities if e.kind is not EntityKind.BACKGROUND]
        records = {e.entity_key: background_record(request, e.entity_key) for e in entities}
        if movers:
            records |= {r.entity_key: r for r in self._track_clip(request, movers)}
        return tuple(records[key] for key in request.entity_keys)

    def _track_clip(
        self, request: TrackingRequest, movers: Sequence[EntitySpec]
    ) -> tuple[TrackRecord, ...]:
        media_type = self._store.content_type(request.clip.uri)
        if not media_type.startswith("video/"):
            reason = f"클립이 영상이 아니라 따라갈 수 없습니다 ({media_type})"
            return tuple(failed_record(request, e.entity_key, reason) for e in movers)
        try:
            with self._clip_file(request.clip.uri) as path:
                return self._track_file(path, request, movers)
        except _ClipUnreadable as exc:
            return tuple(failed_record(request, e.entity_key, str(exc)) for e in movers)

    # ---- 클립 읽기 ----
    @contextmanager
    def _clip_file(self, uri: str) -> Iterator[Path]:
        """OpenCV 가 열 파일 경로. 파일 저장소면 그 파일을, 아니면 임시 파일에 써서 준다."""
        local = self._store.local_path(uri)
        if local is not None and local.is_file():
            yield local
            return
        try:
            data = self._store.get(uri)
        except AdapterError as exc:
            raise _ClipUnreadable(f"클립을 읽지 못했습니다: {exc}") from exc
        handle, name = tempfile.mkstemp(suffix=".mp4", prefix="frame-track-")
        try:
            with os.fdopen(handle, "wb") as out:
                out.write(data)
            yield Path(name)
        finally:
            Path(name).unlink(missing_ok=True)

    def _track_file(
        self, path: Path, request: TrackingRequest, entities: Sequence[EntitySpec]
    ) -> tuple[TrackRecord, ...]:
        cv2 = self._cv2
        capture = cv2.VideoCapture(str(path))
        if not capture.isOpened():
            raise _ClipUnreadable("클립을 영상으로 열지 못했습니다")
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        if not math.isfinite(fps) or not 0 < fps <= MAX_FPS:
            fps = FALLBACK_FPS
        scene = request.scene
        start = scene.planned_start_ms
        state = _Scene(
            scene.planned_end_ms,
            1000.0 / fps,
            {},
            {e.entity_key: [] for e in entities},
            {e.entity_key: TraceLog() for e in entities},
            VlmUsage(),
            [],
        )
        next_find = 0.0
        previous = None  # 앞 프레임의 색 분포 (컷 찾기)
        index = 0
        try:
            while True:
                ok, frame = capture.read()
                clip_ms = int(round(index * state.frame_ms))
                if not ok or clip_ms >= scene.duration_ms:
                    break
                t = start + clip_ms
                small = shrink(cv2, frame, TRACK_MAX_SIDE)
                signature = color_signature(cv2, small)
                cut = (
                    previous is not None
                    and signature_similarity(cv2, previous, signature) < CUT_SIMILARITY
                )
                previous = signature
                # 화면이 통째로 바뀌었다: 추적기는 엉뚱한 것에 붙으므로 끝내고 바로 다시 찾는다
                if cut:
                    state.cuts_ms.append(t)
                    for key in list(state.live):
                        state.end(key, t)
                for key, live in list(state.live.items()):
                    if not self._follow(live, small, t):  # 놓쳤다: 다음 찾는 시각에 다시
                        state.end(key, t)
                due = clip_ms >= next_find  # 찾는 시각의 격자는 컷과 상관없이 그대로 간다
                if cut or due:
                    self._keyframe(state, frame, small, entities, scene, t)
                if due:
                    next_find = clip_ms + self.redetect_ms if self.redetect_ms else math.inf
                index += 1
        finally:
            capture.release()
        for key, live in state.live.items():
            state.segments[key].append(live.closed(state.end_ms, state.frame_ms))
        log.info(
            "장면 %s 추적 (%s): %s, VLM %d회",
            scene.scene_key,
            self.algorithm,
            {key: len(found) for key, found in state.segments.items()},
            state.usage.calls,
        )
        clip_detail = {  # 장면 클립 하나에 대한 값 (그 장면의 객체 기록마다 같다)
            "algorithm": self.algorithm,
            "sample_ms": self.sample_ms,
            "redetect_ms": self.redetect_ms,
            "verify_boxes": self.verify_boxes,
            "cuts_ms": state.cuts_ms,  # 클립 안의 컷 (영상 타임라인)
        }
        return tuple(
            tracked_record(
                request,
                e.entity_key,
                state.segments[e.entity_key],
                state.logs[e.entity_key],
                state.usage,
                clip_detail,
            )
            for e in entities
        )

    # ---- 찾는 시각 ----
    def _keyframe(
        self,
        state: _Scene,
        frame: Any,
        small: Any,
        entities: Sequence[EntitySpec],
        scene: SceneSpec,
        t: int,
    ) -> None:
        """VLM 의 답을 적용한다: 새로 시작 · 어긋난 것 다시 맞추기 · 보이지 않으면 끝내기 · 그대로 두기."""
        found, absent, entries = self._find(frame, entities, scene, t, state.usage)
        for key, entry in entries.items():
            if key in absent:
                entry["absent"] = True
                state.end(key, t)
            elif (hit := found.get(key)) is not None:
                self._apply(state.live, key, hit, entry, small, t)
            state.logs[key].keyframes.append(entry)

    def _apply(
        self,
        live: dict[str, _Live],
        key: str,
        hit: tuple[Box, str],
        entry: dict[str, Any],
        small: Any,
        t: int,
    ) -> None:
        box, verdict = hit
        current = live.get(key)
        if current is None:
            started = self._begin(small, box, t)
            if started is not None:
                live[key] = started
            return
        entry["iou"] = round(overlap(current.current, box), 2)
        entry["reanchored"] = bool(
            verdict in TRUSTED_TO_MOVE
            and entry["iou"] < REANCHOR_IOU
            and self._reanchor(current, small, box, t)
        )

    # ---- 따라가기 ----
    def _begin(self, small: Any, box: Box, t: int) -> _Live | None:
        tracker = self._started_tracker(small, box)
        if tracker is None:
            return None
        current = rounded(box)
        return _Live(tracker, t, t, t + self.sample_ms, current, [BoundingBox(t, *current)])

    def _reanchor(self, state: _Live, small: Any, box: Box, t: int) -> bool:
        """같은 구간 안에서 추적기를 VLM 박스로 다시 시작한다 (그 시각의 박스는 새 박스로)."""
        tracker = self._started_tracker(small, box)
        if tracker is None:
            return False
        state.tracker, state.current, state.last_ms = tracker, rounded(box), t
        anchored = BoundingBox(t, *state.current)
        if state.boxes and state.boxes[-1].t_ms == t:
            state.boxes[-1] = anchored
        else:
            state.boxes.append(anchored)
        state.next_sample_ms = t + self.sample_ms
        return True

    def _started_tracker(self, small: Any, box: Box) -> Any | None:
        tracker = self._create()
        try:
            tracker.init(small, pixel_rect(small, box))
        except self._cv2.error as exc:  # 화면 끝에 걸친 박스 등 추적기가 받지 못하는 박스
            log.warning("추적기를 시작하지 못했습니다 (%s): %s", box, exc)
            return None
        return tracker

    def _follow(self, state: _Live, small: Any, t: int) -> bool:
        """한 프레임 따라간다. 놓쳤으면 False."""
        ok, rect = state.tracker.update(small)
        height, width = small.shape[:2]
        followed = normalized(rect, width, height, MIN_TRACKED_SIDE) if ok else None
        if followed is None:
            return False
        state.current, state.last_ms = followed, t
        if t >= state.next_sample_ms:
            state.boxes.append(BoundingBox(t, *followed))
            state.next_sample_ms = t + self.sample_ms
        return True

    # ---- 찾기 (VLM) ----
    def _find(
        self, frame: Any, entities: Sequence[EntitySpec], scene: SceneSpec, t: int, usage: VlmUsage
    ) -> tuple[dict[str, tuple[Box, str]], frozenset[str], dict[str, dict[str, Any]]]:
        """장면의 객체를 찾고 확인한다 -> (거절되지 않은 박스와 판정, 보이지 않는 객체, 객체마다 그 시각의 근거)."""
        cv2 = self._cv2
        entries: dict[str, dict[str, Any]] = {
            e.entity_key: {"t_ms": t, "found": False} for e in entities
        }
        frame_image = _image(cv2, shrink(cv2, frame, LOCATE_MAX_SIDE))
        try:
            located = self._locator.locate(frame_image, entities, scene)
        except LLMError as exc:
            reason = f"위치를 묻지 못했습니다: {exc}"[:240]
            for entry in entries.values():
                entry["reason"] = reason
            return {}, frozenset(), entries
        usage.add(located.model, located.usage)
        boxes = {key: located.boxes[key] for key in entries if key in located.boxes}
        verdicts, seen = self._verify(frame, entities, boxes, scene, usage)
        found: dict[str, tuple[Box, str]] = {}
        for key, box in boxes.items():
            entry = entries[key]
            entry["found"] = True
            entry["box"] = {name: round(value, 4) for name, value in box.as_dict().items()}
            verdict = verdicts.get(key, "unclear") if self.verify_boxes else UNVERIFIED
            if self.verify_boxes:
                entry["verified"] = verdict
                if seen.get(key):
                    entry["seen"] = seen[key]
            if verdict == "no":
                entry["rejected"] = True  # 엉뚱한 곳을 짚은 박스: 쓰지 않는다
            else:
                found[key] = (box, verdict)
        absent = frozenset(key for key in located.absent if key in entries and key not in boxes)
        return found, absent, entries

    def _verify(
        self,
        frame: Any,
        entities: Sequence[EntitySpec],
        boxes: Mapping[str, Box],
        scene: SceneSpec,
        usage: VlmUsage,
    ) -> tuple[dict[str, str], dict[str, str]]:
        if not self.verify_boxes or not boxes:
            return {}, {}
        cv2 = self._cv2
        crops = [
            (
                e,
                _image(
                    cv2,
                    crop(
                        cv2,
                        frame,
                        boxes[e.entity_key],
                        padding=CROP_PADDING,
                        min_side=CROP_MIN_SIDE,
                    ),
                ),
            )
            for e in entities
            if e.entity_key in boxes
        ]
        try:
            result = self._locator.verify(crops, scene)
        except LLMError as exc:
            log.warning("추적 박스를 확인하지 못해 확인 없이 씁니다 (새로 시작만): %s", exc)
            return dict.fromkeys(boxes, "unchecked"), {}
        usage.add(result.model, result.usage)
        return dict(result.verdicts), dict(result.seen)


def _image(cv2: Any, picture: Any) -> ImageInput:
    data = jpeg(cv2, picture, JPEG_QUALITY)
    if data is None:
        raise _ClipUnreadable("프레임을 JPEG 로 바꾸지 못했습니다")
    return ImageInput(data, "image/jpeg")
