"""ObjectTracker 계약 테스트: 가짜와 OpenCV 추적기(ADR-0036)가 같은 스위트를 통과한다.

OpenCV 는 tracking extra 가 있을 때만 돈다 (합성 클립의 움직이는 사각형 + 정해진 시작 박스).
"""

import pytest

from frame.adapters.generator import FakeVideoGenerator
from frame.adapters.storage import InMemoryArtifactStore
from frame.adapters.tracker import FakeObjectTracker
from frame.application.errors import TrackingError
from frame.application.ports.generator import SceneGenerationRequest
from frame.application.ports.tracker import TrackingRequest
from frame.domain.artifacts import GeneratedClip
from frame.domain.enums import TrackingStatus

COLORS = [(0, 0, 255), (0, 200, 0), (255, 80, 0)]


def _fake_case(ir, tmp_path):
    store = InMemoryArtifactStore()
    scene = ir.scene("intro")
    clip = FakeVideoGenerator(store).generate_scene(
        SceneGenerationRequest("ver_1", scene, tuple(ir.entity(k) for k in scene.entity_keys))
    )
    return FakeObjectTracker(), TrackingRequest("ver_1", scene, clip, scene.entity_keys)


def _opencv_case(ir, tmp_path):
    pytest.importorskip("cv2")
    from frame.adapters.tracker import OpenCvObjectTracker  # noqa: PLC0415
    from tests.support.tracking_video import ScriptedLocator, Square, write_squares  # noqa: PLC0415

    scene = ir.scene("intro")
    squares = {
        key: Square(color=COLORS[i % len(COLORS)], y=20 + 70 * i)
        for i, key in enumerate(scene.entity_keys)
    }
    path = tmp_path / "intro.mp4"
    truth = write_squares(path, squares, scene.duration_ms)
    store = InMemoryArtifactStore()
    uri = store.put("clips/ver_1/intro.mp4", path.read_bytes(), "video/mp4")
    request = TrackingRequest(
        "ver_1",
        scene,
        GeneratedClip("intro", uri, scene.duration_ms, "fp"),
        scene.entity_keys,
        entities=tuple(ir.entity(k) for k in scene.entity_keys),
    )
    locator = ScriptedLocator(answers=[{key: truth[key][0] for key in squares}])
    tracker = OpenCvObjectTracker(
        store, locator, algorithm="csrt", sample_ms=200, redetect_ms=1000, verify_boxes=True
    )
    return tracker, request


CASES = [pytest.param(_fake_case, id="fake"), pytest.param(_opencv_case, id="opencv")]


@pytest.fixture(params=CASES)
def case(request, ir, tmp_path):
    return request.param(ir, tmp_path)


@pytest.fixture
def tracking_request(ir, tmp_path):
    return _fake_case(ir, tmp_path)[1]


def test_returns_one_record_per_requested_entity(case):
    tracker, tracking_request = case
    records = tracker.track(tracking_request)
    assert [r.entity_key for r in records] == list(tracking_request.entity_keys)
    assert all(r.scene_key == "intro" and r.version_id == "ver_1" for r in records)


def test_segments_stay_within_scene_bounds(case):
    tracker, tracking_request = case
    scene = tracking_request.scene
    for record in tracker.track(tracking_request):
        assert record.status is TrackingStatus.COMPLETED
        for segment in record.segments:
            assert (
                scene.planned_start_ms <= segment.start_ms < segment.end_ms <= scene.planned_end_ms
            )
            assert all(
                scene.planned_start_ms <= b.t_ms < scene.planned_end_ms for b in segment.boxes
            )
            assert all(0 <= b.x <= 1 and 0 <= b.y <= 1 for b in segment.boxes)


def test_soft_failure_is_reported_as_failed_status(tracking_request):
    records = FakeObjectTracker(fail_entity_keys={"cup"}).track(tracking_request)
    by_key = {r.entity_key: r for r in records}
    assert by_key["cup"].status is TrackingStatus.FAILED and by_key["cup"].segments == ()
    assert by_key["barista"].status is TrackingStatus.COMPLETED


def test_hard_failure_raises_tracking_error(tracking_request):
    with pytest.raises(TrackingError):
        FakeObjectTracker(error=TrackingError("모델 로드 실패")).track(tracking_request)
