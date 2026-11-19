"""OpenCV 추적기 (FRAME_TRACKER=opencv, ADR-0036): 합성 클립의 움직이는 사각형을 프레임마다 따라가는가."""

from __future__ import annotations

import sys
from dataclasses import replace

import pytest

pytest.importorskip("cv2")

from frame.adapters.storage import InMemoryArtifactStore, LocalFileArtifactStore  # noqa: E402
from frame.adapters.tracker import OpenCvObjectTracker  # noqa: E402
from frame.application.errors import ConfigurationError, LLMBadResponse, LLMUnavailable  # noqa: E402
from frame.application.ports.tracker import TrackingRequest  # noqa: E402
from frame.domain.artifacts import GeneratedClip  # noqa: E402
from frame.domain.enums import EntityKind, TrackingStatus  # noqa: E402
from frame.domain.ir import EntitySpec, SceneSpec  # noqa: E402
from tests.support.tracking_video import ScriptedLocator, Square, iou, write_squares  # noqa: E402

DURATION = 2000
RED = Square(color=(0, 0, 255), y=100)


def _settings(**overrides):
    return {
        "algorithm": "csrt",
        "sample_ms": 100,
        "redetect_ms": 1000,
        "verify_boxes": True,
    } | overrides


def _clip(tmp_path, store, squares, *, start_ms=0, duration_ms=DURATION, cut_at_ms=None):
    """클립을 저장소에 넣고 (요청, 정답) 을 돌려준다. 정답의 시각은 영상 타임라인으로 옮긴다."""
    path = tmp_path / "clip.mp4"
    truth = write_squares(path, squares, duration_ms, cut_at_ms=cut_at_ms)
    uri = store.put("clips/ver_1/intro.mp4", path.read_bytes(), "video/mp4")
    keys = tuple(squares)
    scene = SceneSpec("intro", start_ms, start_ms + duration_ms, "사각형이 움직인다", keys)
    request = TrackingRequest(
        "ver_1",
        scene,
        GeneratedClip("intro", uri, duration_ms, "fp"),
        keys,
        entities=tuple(EntitySpec(key, EntityKind.PROP, "색 사각형") for key in keys),
    )
    timeline = {
        key: {start_ms + t: box for t, box in boxes.items()} for key, boxes in truth.items()
    }
    return request, timeline


def test_follows_a_moving_square_frame_by_frame(tmp_path):
    store = InMemoryArtifactStore()
    request, truth = _clip(tmp_path, store, {"red": RED})
    locator = ScriptedLocator(answers=[{"red": truth["red"][0]}])

    (record,) = OpenCvObjectTracker(store, locator, **_settings()).track(request)

    assert record.status is TrackingStatus.COMPLETED
    (segment,) = record.segments
    boxes = segment.boxes
    assert len(boxes) >= 15  # 2초를 0.1초 간격으로
    assert all(b.t_ms - a.t_ms >= 100 for a, b in zip(boxes, boxes[1:], strict=False))
    assert min(iou(b, truth["red"][b.t_ms]) for b in boxes) > 0.5  # 끝까지 사각형 위에 있다
    assert boxes[-1].x > boxes[0].x + 0.3  # 오른쪽으로 따라갔다
    # 찾는 시각(0초·1초)마다 모든 객체를 묻는다. 1초에는 VLM 이 못 찾았지만 따라가던 것은 그대로 이어 간다
    assert (locator.located, locator.verified) == ([["red"], ["red"]], [["red"]])
    assert record.detail["algorithm"] == "csrt"
    assert record.detail["keyframes"][0]["verified"] == "yes"
    assert record.detail["keyframes"][1] == {"t_ms": 1000, "found": False}
    assert record.detail["scene_vlm"]["calls"] == 3  # 찾기 2 + 확인 1


def test_a_drifted_track_is_moved_to_the_checked_box_of_the_next_find(tmp_path):
    """따라가던 박스가 객체에서 멀어졌으면(확대·축소·미끄러짐) 찾는 시각에 확인(yes)된 VLM 박스로 다시 맞춘다."""
    store = InMemoryArtifactStore()
    # 1초에 순간 이동하는 사각형: 추적기 혼자서는 따라가지 못한다
    jumper = Square(color=(0, 0, 255), y=40, travel=0, visible_until_ms=1000)
    landed = Square(color=(0, 0, 255), y=160, start_x=220, travel=0, visible_from_ms=1000)
    request, truth = _clip(tmp_path, store, {"red": jumper, "landed": landed})
    request = replace(request, entity_keys=("red",), entities=request.entities[:1])
    after = truth["landed"][1000]
    locator = ScriptedLocator(answers=[{"red": truth["red"][0]}, {"red": after}])

    (record,) = OpenCvObjectTracker(store, locator, **_settings()).track(request)

    boxes = [b for segment in record.segments for b in segment.boxes]
    late = [b for b in boxes if b.t_ms >= 1000]
    assert late and min(iou(b, after) for b in late) > 0.5
    reanchor = record.detail["keyframes"][1]
    assert reanchor["reanchored"] is True and reanchor["iou"] < 0.6


def test_a_matching_box_does_not_restart_the_track(tmp_path):
    """VLM 박스가 따라가던 박스와 거의 같으면(IoU 0.6 이상) 그대로 둔다 — 호출마다 흔들리는 박스로 바꾸지 않는다."""
    store = InMemoryArtifactStore()
    request, truth = _clip(tmp_path, store, {"red": RED})
    locator = ScriptedLocator(answers=[{"red": truth["red"][0]}, {"red": truth["red"][1000]}])

    (record,) = OpenCvObjectTracker(store, locator, **_settings()).track(request)

    assert record.detail["keyframes"][1]["reanchored"] is False


def test_only_a_box_checked_as_yes_can_move_a_running_track(tmp_path):
    """새로 시작하는 박스는 '분명히 아님(no)' 만 거르지만, 따라가던 것을 옮기는 박스는 '맞다(yes)' 여야 한다."""
    store = InMemoryArtifactStore()
    request, truth = _clip(tmp_path, store, {"red": RED})
    far = truth["red"][0]  # 1초에 0초 자리를 짚는다 — 사각형은 이미 오른쪽으로 갔다
    locator = ScriptedLocator(answers=[{"red": truth["red"][0]}, {"red": far}])
    locator.verdicts = {"red": "unclear"}

    (record,) = OpenCvObjectTracker(store, locator, **_settings()).track(request)

    assert record.detail["keyframes"][1]["reanchored"] is False
    late = [b for s in record.segments for b in s.boxes if b.t_ms > 1000]
    assert min(iou(b, truth["red"][b.t_ms]) for b in late) > 0.5  # 추적기가 따라가던 대로


def test_a_track_ends_when_the_vlm_says_the_object_is_gone(tmp_path):
    """객체가 화면을 떠나도 추적기는 다른 것에 붙어 계속 '찾았다' 고 할 수 있다 (실측: 클로즈업에서 고양이 박스가
    강아지 위에 남았다). 찾는 시각에 VLM 이 보이지 않는다고 답하면 그 구간을 끝낸다."""
    store = InMemoryArtifactStore()
    leaves = Square(color=(0, 0, 255), y=100, visible_until_ms=900)
    request, truth = _clip(tmp_path, store, {"red": leaves})
    locator = ScriptedLocator(answers=[{"red": truth["red"][0]}, {"red": None}])

    (record,) = OpenCvObjectTracker(store, locator, **_settings()).track(request)

    (segment,) = record.segments
    assert segment.end_ms <= 1000 + 34
    assert record.detail["keyframes"][1] == {"t_ms": 1000, "found": False, "absent": True}
    assert 1000 in record.detail["lost_ms"]


def test_a_shot_cut_ends_the_tracks_and_finds_again_right_away(tmp_path):
    """장면 클립 안의 컷(화면이 통째로 바뀜)에서는 추적기가 엉뚱한 것에 붙는다 (실측: 강아지 클로즈업으로 끊긴 뒤
    고양이 박스가 강아지 머리에 남았다). 컷이면 따라가던 구간을 끝내고 그 프레임에서 바로 다시 찾는다."""
    store = InMemoryArtifactStore()
    before = Square(color=(0, 0, 255), y=40, travel=0, visible_until_ms=600)
    after = Square(color=(0, 0, 255), y=160, start_x=220, travel=0, visible_from_ms=600)
    request, truth = _clip(tmp_path, store, {"red": before, "after": after}, cut_at_ms=600)
    request = replace(request, entity_keys=("red",), entities=request.entities[:1])
    locator = ScriptedLocator(answers=[{"red": truth["red"][0]}, {"red": truth["after"][600]}])

    (record,) = OpenCvObjectTracker(store, locator, **_settings()).track(request)

    first, second = record.segments
    assert first.end_ms <= 634 and 600 <= second.start_ms < 700
    assert min(iou(b, truth["after"][600]) for b in second.boxes) > 0.5
    assert len(locator.located) == 3  # 0초 · 컷(0.6초) · 1초
    assert record.detail["cuts_ms"] == [second.start_ms]


def test_a_background_spans_its_scene_without_being_tracked(tmp_path):
    """배경(BACKGROUND)은 따라갈 물체가 아니다: VLM 에 묻지 않고 장면 전체에 화면 전체 박스 하나로 둔다."""
    store = InMemoryArtifactStore()
    request, truth = _clip(tmp_path, store, {"red": RED, "street": RED})
    background = EntitySpec("street", EntityKind.BACKGROUND, "비 오는 골목")
    request = replace(request, entities=(request.entities[0], background))
    locator = ScriptedLocator(answers=[{"red": truth["red"][0]}])

    red, street = OpenCvObjectTracker(store, locator, **_settings()).track(request)

    assert red.status is TrackingStatus.COMPLETED
    assert all("street" not in asked for asked in locator.located)
    assert street.status is TrackingStatus.COMPLETED
    (segment,) = street.segments
    assert (segment.start_ms, segment.end_ms) == (0, DURATION)
    assert [(b.x, b.y, b.w, b.h) for b in segment.boxes] == [(0.0, 0.0, 1.0, 1.0)]
    assert street.detail["background"] is True


def test_box_times_are_on_the_video_timeline(tmp_path):
    """박스 시각은 장면 시작부터의 영상 시각이다 — 재생 중인 영상의 시각으로 바로 찾아 그린다."""
    store = InMemoryArtifactStore()
    request, truth = _clip(tmp_path, store, {"red": RED}, start_ms=4000)
    locator = ScriptedLocator(answers=[{"red": truth["red"][4000]}])

    (record,) = OpenCvObjectTracker(store, locator, **_settings()).track(request)

    (segment,) = record.segments
    assert segment.start_ms == 4000 and segment.end_ms <= 6000
    assert all(4000 <= b.t_ms < 6000 for b in segment.boxes)


def test_reads_the_file_of_a_local_store_directly(tmp_path):
    store = LocalFileArtifactStore(tmp_path / "artifacts")
    request, truth = _clip(tmp_path, store, {"red": RED})

    (record,) = OpenCvObjectTracker(
        store, ScriptedLocator(answers=[{"red": truth["red"][0]}]), **_settings()
    ).track(request)

    assert record.status is TrackingStatus.COMPLETED


def test_a_box_the_check_rejects_is_not_followed(tmp_path):
    """VLM 이 엉뚱한 곳을 짚었다고 확인되면(no) 따라가지 않고, 다음 찾는 시각에 다시 찾는다."""
    store = InMemoryArtifactStore()
    request, truth = _clip(tmp_path, store, {"red": RED})
    locator = ScriptedLocator(answers=[{"red": truth["red"][0]}] * 2, verdicts={"red": "no"})

    (record,) = OpenCvObjectTracker(store, locator, **_settings()).track(request)

    assert record.status is TrackingStatus.FAILED and record.segments == ()
    assert len(locator.located) == 2  # 0초와 1초
    assert all(k["rejected"] for k in record.detail["keyframes"])
    assert "아니라는 확인" in record.detail["reason"]


def test_an_object_found_later_starts_its_segment_there(tmp_path):
    store = InMemoryArtifactStore()
    late = Square(color=(0, 200, 0), y=60, visible_from_ms=900)
    request, truth = _clip(tmp_path, store, {"green": late})
    locator = ScriptedLocator(answers=[{}, {"green": truth["green"][1000]}])

    (record,) = OpenCvObjectTracker(store, locator, **_settings()).track(request)

    (segment,) = record.segments
    assert 1000 <= segment.start_ms < 1100
    assert record.detail["keyframes"][0] == {"t_ms": 0, "found": False}


def test_without_redetection_it_looks_only_at_the_first_frame(tmp_path):
    store = InMemoryArtifactStore()
    request, _ = _clip(tmp_path, store, {"red": RED})
    locator = ScriptedLocator(answers=[{}])

    (record,) = OpenCvObjectTracker(store, locator, **_settings(redetect_ms=0)).track(request)

    assert record.status is TrackingStatus.FAILED
    assert len(locator.located) == 1
    assert record.detail["reason"] == "화면에서 찾지 못했습니다"


def test_a_failed_locate_call_is_kept_as_the_reason(tmp_path):
    store = InMemoryArtifactStore()
    request, _ = _clip(tmp_path, store, {"red": RED})
    locator = ScriptedLocator(locate_error=LLMBadResponse("JSON 이 아님"))

    (record,) = OpenCvObjectTracker(store, locator, **_settings()).track(request)

    assert record.status is TrackingStatus.FAILED
    assert record.detail["reason"].startswith("위치를 묻지 못했습니다")


def test_when_the_check_itself_fails_the_box_is_followed_unchecked(tmp_path):
    store = InMemoryArtifactStore()
    request, truth = _clip(tmp_path, store, {"red": RED})
    locator = ScriptedLocator(
        answers=[{"red": truth["red"][0]}], verify_error=LLMUnavailable("키 없음")
    )

    (record,) = OpenCvObjectTracker(store, locator, **_settings()).track(request)

    assert record.status is TrackingStatus.COMPLETED
    assert record.detail["keyframes"][0]["verified"] == "unchecked"


def test_boxes_are_not_checked_when_the_check_is_off(tmp_path):
    store = InMemoryArtifactStore()
    request, truth = _clip(tmp_path, store, {"red": RED})
    locator = ScriptedLocator(answers=[{"red": truth["red"][0]}], verdicts={"red": "no"})

    (record,) = OpenCvObjectTracker(store, locator, **_settings(verify_boxes=False)).track(request)

    assert record.status is TrackingStatus.COMPLETED
    assert locator.verified == [] and "verified" not in record.detail["keyframes"][0]


@pytest.mark.parametrize("algorithm", ["kcf", "mil"])
def test_the_other_algorithms_follow_too(tmp_path, algorithm):
    store = InMemoryArtifactStore()
    request, truth = _clip(tmp_path, store, {"red": RED})
    locator = ScriptedLocator(answers=[{"red": truth["red"][0]}])

    (record,) = OpenCvObjectTracker(store, locator, **_settings(algorithm=algorithm)).track(request)

    assert record.status is TrackingStatus.COMPLETED
    boxes = [b for segment in record.segments for b in segment.boxes]
    assert sum(iou(b, truth["red"][b.t_ms]) > 0.3 for b in boxes) >= len(boxes) * 0.8


def test_a_clip_that_is_not_a_video_fails_softly():
    """가짜 생성기의 JSON 클립은 따라갈 수 없다: 객체마다 FAILED 와 사유 (파이프라인은 계속)."""
    store = InMemoryArtifactStore()
    uri = store.put("clips/ver_1/intro.json", b"{}", "application/json")
    scene = SceneSpec("intro", 0, 1000, "", ("cup",))
    request = TrackingRequest("ver_1", scene, GeneratedClip("intro", uri, 1000, "fp"), ("cup",))

    (record,) = OpenCvObjectTracker(store, ScriptedLocator(), **_settings()).track(request)

    assert record.status is TrackingStatus.FAILED
    assert "영상이 아니라" in record.detail["reason"]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"algorithm": "magic"}, "FRAME_TRACKER_ALGORITHM"),
        ({"sample_ms": 0}, "FRAME_TRACKER_SAMPLE_MS"),
        ({"redetect_ms": -1}, "FRAME_TRACKER_REDETECT_MS"),
    ],
)
def test_bad_settings_are_refused_at_startup(overrides, message):
    with pytest.raises(ConfigurationError, match=message):
        OpenCvObjectTracker(InMemoryArtifactStore(), ScriptedLocator(), **_settings(**overrides))


def test_without_opencv_the_server_does_not_start(monkeypatch):
    monkeypatch.setitem(sys.modules, "cv2", None)  # import cv2 -> ImportError

    with pytest.raises(ConfigurationError, match=r"\.\[tracking\]"):
        OpenCvObjectTracker(InMemoryArtifactStore(), ScriptedLocator(), **_settings())
