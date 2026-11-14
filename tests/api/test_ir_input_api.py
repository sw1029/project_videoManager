"""#10 POST /api/projects {ir} — 화면이 자연어 없이 블록(IR)만으로 DRAFT 버전을 만든다 (아이디어 3, T4)."""

import pytest
from fastapi.testclient import TestClient

from frame.adapters.structurer import FakeStructurer
from frame.presentation.app import create_app
from tests.support.factories import (
    CAFE_DISPLAY_NAMES,
    cafe_block_document,
    dispatcher_of,
    make_container,
)


@pytest.fixture
def api():
    structurer = FakeStructurer()
    container = make_container(structurer=structurer)
    return TestClient(create_app(container)), container, structurer


def test_blocks_alone_make_a_draft_version(api):
    """완료 기준: 화면이 자연어 없이 블록만으로 DRAFT 버전을 만들 수 있다."""
    client, container, structurer = api

    response = client.post(
        "/api/projects", json={"title": "카페 블록", "ir": cafe_block_document()}
    )

    assert response.status_code == 201, (
        response.text
    )  # 바로 만들어졌다 (자연어는 202 + STRUCTURING)
    created = response.json()
    assert created["state"] == "DRAFT" and created["repair_notes"] == []
    assert structurer.calls == [] and dispatcher_of(container).pending == []
    version = client.get(f"/api/versions/{created['version_id']}").json()
    assert version["state"] == "DRAFT" and version["source_prompt"] is None
    assert [s["scene_key"] for s in version["ir"]["scenes"]] == ["intro", "closeup", "outro"]
    names = {e["entity_key"]: e["display_name"] for e in version["ir"]["entities"]}
    assert names == CAFE_DISPLAY_NAMES
    holds = next(r for r in version["ir"]["scenes"][0]["requirements"] if r["predicate"] == "holds")
    assert holds["expected_value"] == "cup"  # 관계 값은 보정이 상대 객체 키로 채운다


def test_a_block_draft_goes_through_generation(api):
    client, container, _ = api
    created = client.post(
        "/api/projects", json={"title": "카페 블록", "ir": cafe_block_document()}
    ).json()

    accepted = client.post(f"/api/versions/{created['version_id']}/generations")
    assert accepted.status_code == 202, accepted.text
    dispatcher_of(container).run_all()

    assert client.get(f"/api/versions/{created['version_id']}").json()["state"] == "COMPLETED"


def test_prompt_sent_with_ir_is_kept_as_the_source_prompt(api):
    client, container, structurer = api
    prompt = "바리스타가 빨간 컵을 들어 보인다"

    response = client.post(
        "/api/projects", json={"title": "카페", "prompt": prompt, "ir": cafe_block_document()}
    )

    assert response.status_code == 201, response.text
    version = client.get(f"/api/versions/{response.json()['version_id']}").json()
    assert version["source_prompt"] == prompt
    assert version["state"] == "DRAFT" and structurer.calls == []


def test_repair_notes_are_returned_when_the_server_fixes_the_blocks(api):
    client, _, _ = api
    document = cafe_block_document()
    document["scenes"][2]["entity_keys"] = [
        "cafe"
    ]  # outro 의 요구조건이 가리키는 barista 가 빠졌다

    response = client.post("/api/projects", json={"title": "카페", "ir": document})

    assert response.status_code == 201, response.text
    notes = response.json()["repair_notes"]
    assert len(notes) == 1 and "barista" in notes[0] and "outro" in notes[0]


def test_invalid_ir_returns_422_validation_error(api):
    client, container, _ = api
    document = cafe_block_document()
    document["scenes"][1]["scene_key"] = "intro"  # 장면 키 중복은 고칠 수 없다

    response = client.post("/api/projects", json={"title": "카페", "ir": document})

    assert response.status_code == 422
    assert response.json()["error_code"] == "VALIDATION_ERROR"
    assert "scene_key" in response.json()["error_message"]
    assert client.get("/api/projects/prj_0001").status_code == 404  # 아무것도 저장하지 않았다


@pytest.mark.parametrize(
    "body",
    [
        {"title": "카페"},  # prompt 도 ir 도 없다
        {"title": "카페", "ir": {"entities": []}},  # 장면 목록이 없다
        {"title": "카페", "ir": {"scenes": [{"scene_key": "a", "planned_start_ms": 0}]}},
    ],
    ids=["neither", "no-scenes", "missing-field"],
)
def test_request_schema_errors_return_422_with_details(api, body):
    client, _, _ = api
    response = client.post("/api/projects", json=body)
    assert response.status_code == 422
    assert response.json()["error_code"] == "VALIDATION_ERROR"
    assert response.json()["details"]


def test_ir_longer_than_the_limit_is_rejected(api):
    client, _, _ = api
    document = cafe_block_document()
    document["scenes"][1]["planned_end_ms"] = 9_000  # 4초 + 5초 + 292초 = 301초 (상한 5분)
    document["scenes"][2]["planned_end_ms"] = 300_000

    response = client.post("/api/projects", json={"title": "카페", "ir": document})

    assert response.status_code == 422
    assert "상한" in response.json()["error_message"]


def test_prompt_only_requests_still_start_structuring(api):
    client, container, structurer = api
    response = client.post("/api/projects", json={"title": "카페", "prompt": "scene a 0-4000"})
    assert response.status_code == 202
    assert response.json() == {
        "project_id": response.json()["project_id"],
        "version_id": response.json()["version_id"],
        "state": "STRUCTURING",
        "repair_notes": [],
    }
    assert len(dispatcher_of(container).pending) == 1


@pytest.mark.parametrize(
    ("where", "key"),
    [
        ("scene", "../ver_victim/intro"),  # 저장 경로를 벗어나는 장면 키 (감사 A-01 재현값)
        ("scene", "a/b"),
        ("entity", "a:b"),  # 요구조건 키의 경계를 흐린다
        ("entity", "-"),  # 장면 수준 조건의 자리 표시와 겹친다
        ("entity", "컵"),
    ],
)
def test_keys_that_are_not_ascii_identifiers_are_rejected_per_field(api, where, key):
    client, container, _ = api
    document = cafe_block_document()
    if where == "scene":
        document["scenes"][0]["scene_key"] = key
    else:
        document["entities"][0]["entity_key"] = key

    response = client.post("/api/projects", json={"title": "카페", "ir": document})

    assert response.status_code == 422
    body = response.json()
    assert body["error_code"] == "VALIDATION_ERROR"
    field = "scene_key" if where == "scene" else "entity_key"
    assert any(d["loc"].endswith(field) for d in body["details"])
    assert client.get("/api/projects/prj_0001").status_code == 404
