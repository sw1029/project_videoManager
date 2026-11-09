"""ObjectTracker 계약 테스트. 실제 추적기(OpenCV/SAM2)는 slow 마커로 같은 스위트에 추가한다."""

import pytest

from frame.adapters.generator import FakeVideoGenerator
from frame.adapters.storage import InMemoryArtifactStore
from frame.adapters.tracker import FakeObjectTracker
from frame.application.errors import TrackingError
from frame.application.ports.generator import SceneGenerationRequest
from frame.application.ports.tracker import TrackingRequest
from frame.domain.enums import TrackingStatus

TRACKERS = [pytest.param(lambda: FakeObjectTracker(), id="fake")]


@pytest.fixture(params=TRACKERS)
def tracker(request):
    return request.param()


@pytest.fixture
def tracking_request(ir):
    store = InMemoryArtifactStore()
    scene = ir.scene("intro")
    clip = FakeVideoGenerator(store).generate_scene(
        SceneGenerationRequest("ver_1", scene, tuple(ir.entity(k) for k in scene.entity_keys))
    )
    return TrackingRequest("ver_1", scene, clip, scene.entity_keys)


def test_returns_one_record_per_requested_entity(tracker, tracking_request):
    records = tracker.track(tracking_request)
    assert [r.entity_key for r in records] == list(tracking_request.entity_keys)
    assert all(r.scene_key == "intro" and r.version_id == "ver_1" for r in records)


def test_segments_stay_within_scene_bounds(tracker, tracking_request):
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
