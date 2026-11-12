"""GET /api/versions/{id} 의 progress 와 실패 버전의 부분 결과 (이슈 #3)."""

from fastapi.testclient import TestClient

from frame.adapters.generator import FakeVideoGenerator
from frame.adapters.storage import InMemoryArtifactStore
from frame.adapters.tracker import FakeObjectTracker
from frame.application.errors import TrackingError
from frame.presentation.app import create_app
from tests.support.factories import CAFE_PROMPT, dispatcher_of, make_container


def _api(**overrides):
    container = make_container(**overrides)
    return TestClient(create_app(container)), dispatcher_of(container)


def _draft(client, dispatcher) -> str:
    created = client.post("/api/projects", json={"title": "카페", "prompt": CAFE_PROMPT})
    assert created.status_code == 202, created.text
    dispatcher.run_all()
    return created.json()["version_id"]


def _generate(client, dispatcher, vid) -> dict:
    assert client.post(f"/api/versions/{vid}/generations").status_code == 202
    dispatcher.run_all()
    return client.get(f"/api/versions/{vid}").json()


def _without_elapsed(stages):
    return [{k: v for k, v in s.items() if k != "elapsed_ms"} for s in stages]


def test_progress_is_null_until_the_pipeline_runs_then_lists_every_stage():
    client, dispatcher = _api()
    vid = _draft(client, dispatcher)
    assert client.get(f"/api/versions/{vid}").json()["progress"] is None

    progress = _generate(client, dispatcher, vid)["progress"]

    assert progress["stage"] == "check"
    assert _without_elapsed(progress["stages"]) == [
        {"stage": "generate", "status": "COMPLETED", "done": 3, "total": 3, "current": None},
        {"stage": "track", "status": "COMPLETED", "done": 3, "total": 3, "current": None},
        {"stage": "check", "status": "COMPLETED", "done": 0, "total": 0, "current": None},
    ]
    assert all(isinstance(s["elapsed_ms"], int) for s in progress["stages"])
    assert progress["elapsed_ms"] == sum(s["elapsed_ms"] for s in progress["stages"])


def test_failed_version_serves_the_finished_clip_and_says_where_it_stopped():
    store = InMemoryArtifactStore()
    client, dispatcher = _api(
        artifact_store=store, generator=FakeVideoGenerator(store, fail_scene_keys={"closeup"})
    )
    vid = _draft(client, dispatcher)

    body = _generate(client, dispatcher, vid)

    assert (body["state"], body["error_code"]) == ("FAILED", "GENERATION_ERROR")
    assert "장면 closeup (2/3) 에서 실패" in body["error_message"]
    assert body["progress"]["stage"] == "generate"
    assert _without_elapsed(body["progress"]["stages"]) == [
        {"stage": "generate", "status": "FAILED", "done": 1, "total": 3, "current": "closeup"}
    ]
    scenes = {s["scene_key"]: s for s in body["ir"]["scenes"]}
    assert scenes["intro"]["clip_url"] == f"/api/versions/{vid}/scenes/intro/clip"
    assert scenes["intro"]["clip_uri"] and scenes["closeup"]["clip_uri"] is None
    # 과금된 장면 결과를 받을 수 있다
    assert client.get(scenes["intro"]["clip_url"]).status_code == 200
    missing = client.get(f"/api/versions/{vid}/scenes/closeup/clip")
    assert missing.status_code == 404 and missing.json()["error_code"] == "MEDIA_NOT_AVAILABLE"
    assert body["video_url"] is None


def test_tracking_failure_still_serves_clips_and_video_but_no_checks():
    client, dispatcher = _api(tracker=FakeObjectTracker(error=TrackingError("추적기 로드 실패")))
    vid = _draft(client, dispatcher)

    body = _generate(client, dispatcher, vid)

    assert (body["state"], body["error_code"]) == ("FAILED", "TRACKING_ERROR")
    assert [s["status"] for s in body["progress"]["stages"]] == ["COMPLETED", "FAILED"]
    assert all(s["clip_url"] for s in body["ir"]["scenes"])
    assert client.get(body["video_url"]).status_code == 200
    assert client.get(f"/api/versions/{vid}/checks").json()["results"] == []
