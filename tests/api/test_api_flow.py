"""Endpoint Catalog 의 7개 API 를 TestClient 로 검증한다 (Controller 는 변환만 하므로 흐름 중심)."""

import pytest
from fastapi.testclient import TestClient

from frame.presentation.app import create_app
from tests.support.factories import CAFE_PROMPT, dispatcher_of, make_container


@pytest.fixture
def api():
    container = make_container()
    client = TestClient(create_app(container))
    return client, dispatcher_of(container)


def _create(client, dispatcher, prompt=CAFE_PROMPT):
    response = client.post("/api/projects", json={"title": "카페 신제품 숏폼", "prompt": prompt})
    assert response.status_code == 202, response.text
    dispatcher.run_all()
    return response.json()


def test_health_reports_selected_adapters(api):
    client, _ = api
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["adapters"]["generator"] == "fake"


@pytest.mark.ac("AC-11")
def test_create_project_then_structuring_yields_draft_ir(api):
    client, dispatcher = api
    response = client.post("/api/projects", json={"title": "카페", "prompt": CAFE_PROMPT})
    assert response.status_code == 202
    assert response.json()["state"] == "STRUCTURING"
    version_id = response.json()["version_id"]

    dispatcher.run_all()
    body = client.get(f"/api/versions/{version_id}").json()
    assert body["state"] == "DRAFT"
    assert [s["scene_key"] for s in body["ir"]["scenes"]] == ["intro", "closeup", "outro"]
    assert body["ir"]["entities"][0]["entity_id"] == f"{body['project_id']}:barista"


def test_project_detail_lists_versions(api):
    client, dispatcher = api
    created = _create(client, dispatcher)
    body = client.get(f"/api/projects/{created['project_id']}").json()
    assert body["title"] == "카페 신제품 숏폼"
    assert [v["version_id"] for v in body["versions"]] == [created["version_id"]]


def test_generation_is_accepted_then_completed(api):
    client, dispatcher = api
    version_id = _create(client, dispatcher)["version_id"]

    accepted = client.post(f"/api/versions/{version_id}/generations")
    assert accepted.status_code == 202 and accepted.json()["state"] == "QUEUED"
    assert client.get(f"/api/versions/{version_id}").json()["state"] == "QUEUED"

    dispatcher.run_all()
    body = client.get(f"/api/versions/{version_id}").json()
    assert body["state"] == "COMPLETED" and body["video_uri"]
    assert all(s["clip_uri"] for s in body["ir"]["scenes"])


@pytest.mark.ac("AC-12")
def test_checks_endpoint_reports_reflection_and_consistency(api):
    client, dispatcher = api
    version_id = _create(client, dispatcher)["version_id"]
    client.post(f"/api/versions/{version_id}/generations")
    dispatcher.run_all()

    body = client.get(f"/api/versions/{version_id}/checks").json()
    assert body["summary"]["requirement_reflection_rate"] == 1.0
    assert body["summary"]["consistency_rate"] == 1.0
    assert {r["check_type"] for r in body["results"]} == {"REQUIREMENT", "CONSISTENCY"}


@pytest.mark.ac("AC-31")
def test_tracks_endpoint_accepts_entity_key_or_entity_id(api):
    client, dispatcher = api
    created = _create(client, dispatcher)
    version_id = created["version_id"]
    client.post(f"/api/versions/{version_id}/generations")
    dispatcher.run_all()

    by_key = client.get(f"/api/versions/{version_id}/entities/cup/tracks").json()
    by_id = client.get(
        f"/api/versions/{version_id}/entities/{created['project_id']}:cup/tracks"
    ).json()
    assert by_key == by_id
    assert [(r["scene_key"], r["status"]) for r in by_key["records"]] == [
        ("intro", "COMPLETED"),
        ("closeup", "COMPLETED"),
    ]
    assert by_key["records"][0]["segments"][0]["boxes"]


@pytest.mark.ac("AC-43")
def test_revision_flow_allows_before_after_comparison(api):
    client, dispatcher = api
    original_id = _create(client, dispatcher)["version_id"]
    client.post(f"/api/versions/{original_id}/generations")
    dispatcher.run_all()

    revised = client.post(
        f"/api/versions/{original_id}/revisions",
        json={"entity_key": "cup", "predicate": "color", "new_value": "teal"},
    )
    assert revised.status_code == 201, revised.text
    assert revised.json()["changed_scene_keys"] == ["intro", "closeup"]
    revision_id = revised.json()["version_id"]

    client.post(f"/api/versions/{revision_id}/generations")
    dispatcher.run_all()

    before = client.get(f"/api/versions/{original_id}").json()
    after = client.get(f"/api/versions/{revision_id}").json()
    assert before["state"] == after["state"] == "COMPLETED"
    assert after["parent_version_id"] == original_id
    assert before["video_uri"] != after["video_uri"]
    clips = {
        b["scene_key"]: (b["clip_uri"], a["clip_uri"])
        for b, a in zip(before["ir"]["scenes"], after["ir"]["scenes"], strict=True)
    }
    assert clips["outro"][0] == clips["outro"][1]  # 비대상 장면은 같은 클립
    assert clips["intro"][0] != clips["intro"][1]
    assert (
        client.get(f"/api/versions/{revision_id}/checks").json()["summary"]["preservation_rate"]
        >= 0.9
    )


def test_generation_on_non_draft_returns_409(api):
    client, dispatcher = api
    version_id = _create(client, dispatcher)["version_id"]
    client.post(f"/api/versions/{version_id}/generations")
    response = client.post(f"/api/versions/{version_id}/generations")
    assert response.status_code == 409
    assert response.json() == {
        "error_code": "VERSION_NOT_GENERATABLE",
        "error_message": response.json()["error_message"],
    }


def test_failed_version_exposes_error_code_and_message(api):
    client, dispatcher = api
    body = _create(client, dispatcher, prompt="자연어만 있는 요구사항")
    version = client.get(f"/api/versions/{body['version_id']}").json()
    assert version["state"] == "FAILED"
    assert version["error_code"] == "STRUCTURING_ERROR" and version["error_message"]


def test_unknown_version_returns_404(api):
    client, _ = api
    response = client.get("/api/versions/ver_missing")
    assert response.status_code == 404 and response.json()["error_code"] == "NOT_FOUND"


def test_revision_validation_errors(api):
    client, dispatcher = api
    version_id = _create(client, dispatcher)["version_id"]
    unknown = client.post(
        f"/api/versions/{version_id}/revisions",
        json={"entity_key": "ghost", "predicate": "color", "new_value": "teal"},
    )
    assert unknown.status_code == 404
    bad_scope = client.post(
        f"/api/versions/{version_id}/revisions",
        json={"entity_key": "cup", "predicate": "color", "new_value": "teal", "scope": "SCENES"},
    )
    assert bad_scope.status_code == 422 and bad_scope.json()["error_code"] == "VALIDATION_ERROR"
    schema = client.post(f"/api/versions/{version_id}/revisions", json={"entity_key": "cup"})
    assert schema.status_code == 422  # pydantic 스키마 검증


def test_empty_prompt_is_rejected_by_schema(api):
    client, _ = api
    assert client.post("/api/projects", json={"title": "카페", "prompt": ""}).status_code == 422


def test_vocabulary_endpoint_lists_predicates(api):
    client, _ = api
    body = client.get("/api/vocabulary/predicates").json()
    by_name = {p["name"]: p for p in body["predicates"]}
    assert by_name["color"]["value_kind"] == "COLOR" and by_name["color"]["relational"] is False
    assert by_name["holds"]["relational"] is True and "들다" in by_name["holds"]["aliases"]


def test_revision_can_change_a_relation_with_current_value(api):
    client, dispatcher = api
    version_id = _create(
        client, dispatcher, prompt=CAFE_PROMPT + "entity phone PROP\nin intro: phone\n"
    )["version_id"]
    response = client.post(
        f"/api/versions/{version_id}/revisions",
        json={
            "entity_key": "barista",
            "predicate": "holds",
            "new_value": "phone",
            "current_value": "cup",
            "scope": "SCENES",
            "scene_keys": ["intro"],
        },
    )
    assert response.status_code == 201, response.text
    revised = client.get(f"/api/versions/{response.json()['version_id']}").json()
    intro = next(s for s in revised["ir"]["scenes"] if s["scene_key"] == "intro")
    holds = next(r for r in intro["requirements"] if r["predicate"] == "holds")
    assert (holds["related_entity_key"], holds["requirement_key"], holds["value_kind"]) == (
        "phone",
        "intro:barista:holds:phone",
        "ENTITY",
    )
