"""수정 의도 API (ADR-0017): 유지할 요소·요청 원문·REPAIR 를 받고, 버전·비교·대시보드 응답에 돌려준다.

컨셉 화면의 '컵 01 수정' 패널이 보내는 값이다.
"""

import pytest
from fastapi.testclient import TestClient

from frame.presentation.app import create_app
from tests.support.factories import CAFE_PROMPT, dispatcher_of, make_container

REQUEST = "컵 색상만 청록색으로 동일해줘. 인물과 배경은 유지해줘."
KEEP = [
    {"aspect": "ACTION", "entity_key": "barista"},
    {"aspect": "LIGHTING", "entity_key": None},
    {"aspect": "SHAPE", "entity_key": "cup"},
]


@pytest.fixture
def api():
    container = make_container()
    client = TestClient(create_app(container))
    dispatcher = dispatcher_of(container)
    created = client.post(
        "/api/projects", json={"title": "카페 신제품 숏폼", "prompt": CAFE_PROMPT}
    )
    dispatcher.run_all()
    return client, dispatcher, created.json()["version_id"]


def _revise(client, version_id, **body):
    payload = {"entity_key": "cup", "predicate": "color", "new_value": "teal", **body}
    return client.post(f"/api/versions/{version_id}/revisions", json=payload)


def test_change_revision_returns_what_was_asked(api):
    client, _, version_id = api

    created = _revise(client, version_id, keep=KEEP, request_text=REQUEST)

    assert created.status_code == 201, created.text
    assert created.json()["kind"] == "CHANGE"
    revision = client.get(f"/api/versions/{created.json()['version_id']}").json()["revision"]
    assert revision == {
        "kind": "CHANGE",
        "entity_key": "cup",
        "predicate": "color",
        "value": "teal",
        "previous_value": "red",
        "scope": "ALL_APPEARANCES",
        "target_scene_keys": ["intro", "closeup"],
        "keep": KEEP,
        "request_text": REQUEST,
    }
    assert client.get(f"/api/versions/{version_id}").json()["revision"] is None


@pytest.mark.ac("AC-01")
def test_repair_revision_regenerates_without_changing_requirements(api):
    client, dispatcher, version_id = api
    before = client.get(f"/api/versions/{version_id}").json()

    created = _revise(client, version_id, new_value="red", kind="REPAIR")

    assert created.status_code == 201, created.text
    body = created.json()
    assert body["kind"] == "REPAIR" and body["changed_scene_keys"] == ["intro", "closeup"]
    after = client.get(f"/api/versions/{body['version_id']}").json()
    assert after["ir"]["scenes"][0]["requirements"] == before["ir"]["scenes"][0]["requirements"]
    assert after["revision"]["kind"] == "REPAIR" and after["revision"]["value"] == "red"


def test_noop_change_and_wrong_repair_are_rejected_with_a_hint(api):
    client, _, version_id = api

    noop = _revise(client, version_id, new_value="red")
    wrong = _revise(client, version_id, kind="REPAIR")  # 요구는 red 인데 teal 로 맞추라고 함

    assert noop.status_code == 422 and "REPAIR" in noop.json()["error_message"]
    assert wrong.status_code == 422 and "CHANGE" in wrong.json()["error_message"]


def test_keep_items_are_validated(api):
    client, _, version_id = api

    unknown = _revise(client, version_id, keep=[{"aspect": "ACTION", "entity_key": "ghost"}])
    contradictory = _revise(
        client, version_id, keep=[{"aspect": "APPEARANCE", "entity_key": "cup"}]
    )
    malformed = _revise(client, version_id, keep=[{"aspect": "BACKGROUND", "entity_key": "cafe"}])
    not_an_aspect = _revise(client, version_id, keep=[{"aspect": "MOOD"}])

    assert unknown.status_code == 404
    assert contradictory.status_code == 422 and malformed.status_code == 422
    assert not_an_aspect.status_code == 422


def test_comparison_and_dashboard_show_the_revision_intent(api):
    client, dispatcher, version_id = api
    client.post(f"/api/versions/{version_id}/generations")
    dispatcher.run_all()
    revision_id = _revise(client, version_id, keep=KEEP[:1]).json()["version_id"]

    comparison = client.get(f"/api/versions/{revision_id}/comparison").json()
    project_id = client.get(f"/api/versions/{version_id}").json()["project_id"]
    dashboard = client.get(f"/api/projects/{project_id}/dashboard").json()

    assert comparison["after"]["revision"]["kind"] == "CHANGE"
    assert comparison["before"]["revision"] is None
    by_id = {v["version_id"]: v for v in dashboard["versions"]}
    assert by_id[revision_id]["revision"]["keep"] == KEEP[:1]
