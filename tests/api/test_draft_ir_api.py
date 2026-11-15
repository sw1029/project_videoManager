"""#11 PUT /api/versions/{version_id}/ir — 화면이 구조화 결과(블록)를 고친 뒤 생성을 접수한다 (리스크 2)."""

import copy

import pytest
from fastapi.testclient import TestClient

from frame.presentation.app import create_app
from tests.support.factories import CAFE_PROMPT, dispatcher_of, make_container


@pytest.fixture
def api():
    container = make_container()
    return TestClient(create_app(container)), container


def _draft(client, container) -> dict:
    created = client.post("/api/projects", json={"title": "카페", "prompt": CAFE_PROMPT})
    assert created.status_code == 202, created.text
    dispatcher_of(container).run_all()
    return client.get(f"/api/versions/{created.json()['version_id']}").json()


def _with_teal_cup(ir: dict) -> dict:
    """GET 으로 받은 ir 의 intro 컵 색을 청록으로 바꾸고 closeup 을 200 ms 밀어 둔다 (화면에서 고친 결과)."""
    for requirement in ir["scenes"][0]["requirements"]:
        if requirement["requirement_key"] == "intro:cup:color":
            requirement["expected_value"] = "teal"
    ir["scenes"][1]["planned_start_ms"] += 200
    ir["scenes"][1]["planned_end_ms"] += 200
    return ir


def _requirement(version: dict, key: str) -> dict:
    return next(
        r for s in version["ir"]["scenes"] for r in s["requirements"] if r["requirement_key"] == key
    )


@pytest.mark.ac("AC-13")
def test_put_ir_saves_the_repaired_ir_and_returns_repair_notes(api):
    client, container = api
    draft = _draft(client, container)

    response = client.put(
        f"/api/versions/{draft['version_id']}/ir", json=_with_teal_cup(draft["ir"])
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["version_id"], body["state"]) == (draft["version_id"], "DRAFT")
    assert _requirement(body, "intro:cup:color")["expected_value"] == "teal"
    assert body["ir"]["scenes"][1]["planned_start_ms"] == 4000  # 보정이 붙였다
    assert len(body["repair_notes"]) == 1 and "closeup" in body["repair_notes"][0]
    stored = client.get(f"/api/versions/{draft['version_id']}").json()
    assert stored["ir"] == body["ir"]
    assert stored["source_prompt"] == CAFE_PROMPT


def test_the_ir_from_get_can_be_put_back_unchanged(api):
    """GET 의 ir (entity_id·requirement_key·clip_url 같은 읽기 전용 필드 포함)을 그대로 보내도 된다."""
    client, container = api
    draft = _draft(client, container)

    response = client.put(f"/api/versions/{draft['version_id']}/ir", json=draft["ir"])

    assert response.status_code == 200, response.text
    assert response.json()["repair_notes"] == []
    assert response.json()["ir"] == draft["ir"]


def test_generation_can_be_accepted_after_editing(api):
    """완료 기준: 사용자가 구조화 결과를 고친 뒤 생성을 접수할 수 있다."""
    client, container = api
    draft = _draft(client, container)
    version_id = draft["version_id"]
    edited = client.put(f"/api/versions/{version_id}/ir", json=_with_teal_cup(draft["ir"])).json()

    accepted = client.post(f"/api/versions/{version_id}/generations")
    assert accepted.status_code == 202, accepted.text
    dispatcher_of(container).run_all()

    done = client.get(f"/api/versions/{version_id}").json()
    assert done["state"] == "COMPLETED"
    assert done["frozen_fingerprint"] is not None
    assert [s["requirements"] for s in done["ir"]["scenes"]] == [
        s["requirements"] for s in edited["ir"]["scenes"]
    ]
    checks = client.get(f"/api/versions/{version_id}/checks").json()
    assert any(r["requirement_key"] == "intro:cup:color" for r in checks["results"])


def test_frozen_ir_is_not_changed(api):
    """완료 기준: 고정된 IR 은 바뀌지 않는다 (생성 접수 뒤에는 409 VERSION_NOT_EDITABLE)."""
    client, container = api
    draft = _draft(client, container)
    version_id = draft["version_id"]
    client.post(f"/api/versions/{version_id}/generations")
    queued = client.get(f"/api/versions/{version_id}").json()

    response = client.put(f"/api/versions/{version_id}/ir", json=_with_teal_cup(draft["ir"]))

    assert response.status_code == 409, response.text
    assert response.json()["error_code"] == "VERSION_NOT_EDITABLE"
    after = client.get(f"/api/versions/{version_id}").json()
    assert (after["ir"], after["frozen_fingerprint"]) == (
        queued["ir"],
        queued["frozen_fingerprint"],
    )


def test_ir_that_cannot_be_repaired_returns_422_and_keeps_the_draft(api):
    client, container = api
    draft = _draft(client, container)
    ir = copy.deepcopy(draft["ir"])
    ir["scenes"][1]["scene_key"] = "intro"  # 장면 키 중복

    response = client.put(f"/api/versions/{draft['version_id']}/ir", json=ir)

    assert response.status_code == 422
    assert response.json()["error_code"] == "VALIDATION_ERROR"
    assert "scene_key" in response.json()["error_message"]
    assert client.get(f"/api/versions/{draft['version_id']}").json()["ir"] == draft["ir"]


@pytest.mark.parametrize(
    "body",
    [
        {"entities": []},  # 장면 목록이 없다
        {"entities": [], "scenes": []},  # 장면이 하나도 없다
        {"entities": [{"kind": "PROP"}], "scenes": [{"scene_key": "a"}]},  # 필수 항목 누락
    ],
)
def test_malformed_ir_documents_are_rejected_by_the_schema(api, body):
    client, container = api
    draft = _draft(client, container)

    response = client.put(f"/api/versions/{draft['version_id']}/ir", json=body)

    assert response.status_code == 422
    assert response.json()["error_code"] == "VALIDATION_ERROR"
    assert response.json()["details"]


def test_unknown_version_returns_404(api):
    client, container = api
    draft = _draft(client, container)
    response = client.put("/api/versions/ver_missing/ir", json=draft["ir"])
    assert response.status_code == 404
    assert response.json()["error_code"] == "NOT_FOUND"


def test_display_names_can_be_given_and_are_returned(api):
    """#10 display_name: 화면 라벨("컵 01")을 키와 따로 둔다. 구조화 결과에는 없으므로 null 이다."""
    client, container = api
    draft = _draft(client, container)
    assert {e["display_name"] for e in draft["ir"]["entities"]} == {None}
    ir = copy.deepcopy(draft["ir"])
    for entity in ir["entities"]:
        if entity["entity_key"] == "cup":
            entity["display_name"] = "컵 01"

    response = client.put(f"/api/versions/{draft['version_id']}/ir", json=ir)

    assert response.status_code == 200, response.text
    names = {e["entity_key"]: e["display_name"] for e in response.json()["ir"]["entities"]}
    assert names == {"barista": None, "cup": "컵 01", "cafe": None}
    assert response.json()["repair_notes"] == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda ir: ir["scenes"][0].update(scene_key="../ver_victim/intro"),
        lambda ir: ir["entities"][0].update(entity_key="a:b"),
        lambda ir: ir["scenes"][1].update(entity_keys=["cup", "../x"]),
        lambda ir: ir["scenes"][0]["requirements"][0].update(related_entity_key="컵"),
    ],
    ids=["scene-key", "entity-key", "scene-entity-keys", "requirement-key"],
)
def test_ir_keys_must_be_ascii_identifiers(api, mutate):
    """감사 A-01: 키는 저장 경로·요구조건 키에 들어가므로 영문·숫자·밑줄만 받는다 (422, 저장하지 않음)."""
    client, container = api
    draft = _draft(client, container)
    ir = copy.deepcopy(draft["ir"])
    mutate(ir)

    response = client.put(f"/api/versions/{draft['version_id']}/ir", json=ir)

    assert response.status_code == 422
    assert response.json()["error_code"] == "VALIDATION_ERROR"
    assert response.json()["details"]
    assert client.get(f"/api/versions/{draft['version_id']}").json()["ir"] == draft["ir"]
