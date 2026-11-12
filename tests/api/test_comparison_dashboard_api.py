"""US-04 전후 비교·대시보드 API (ADR-0014). 화면이 한 번의 호출로 그릴 수 있는지를 응답 형태로 확인한다."""

import pytest
from fastapi.testclient import TestClient

from frame.presentation.app import create_app
from frame.tools.export_openapi import build_spec, contract_signature
from tests.support.factories import CAFE_PROMPT, dispatcher_of, make_container


@pytest.fixture
def api():
    container = make_container()
    return TestClient(create_app(container)), dispatcher_of(container)


def _create(client, dispatcher, prompt=CAFE_PROMPT, title="카페 신제품 숏폼") -> dict:
    response = client.post("/api/projects", json={"title": title, "prompt": prompt})
    assert response.status_code == 202, response.text
    dispatcher.run_all()
    return response.json()


def _generate(client, dispatcher, version_id: str) -> None:
    assert client.post(f"/api/versions/{version_id}/generations").status_code == 202
    dispatcher.run_all()


def _revise(client, version_id: str, entity="cup", predicate="color", value="teal") -> str:
    response = client.post(
        f"/api/versions/{version_id}/revisions",
        json={"entity_key": entity, "predicate": predicate, "new_value": value},
    )
    assert response.status_code == 201, response.text
    return response.json()["version_id"]


def _scene(body: dict, scene_key: str) -> dict:
    return next(s for s in body["scenes"] if s["scene_key"] == scene_key)


def _assert_error(response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    assert response.json()["error_code"] == code
    assert response.json()["error_message"]


# ---- GET /api/versions/{id}/comparison ----------------------------------------------


@pytest.mark.ac("AC-43")
def test_comparison_returns_everything_the_before_after_view_needs(api):
    client, dispatcher = api
    original = _create(client, dispatcher)["version_id"]
    _generate(client, dispatcher, original)
    revision = _revise(client, original)
    _generate(client, dispatcher, revision)

    response = client.get(f"/api/versions/{revision}/comparison")

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["version_id"], body["against_version_id"]) == (revision, original)
    assert body["against_is_parent"] is True
    before, after = body["before"], body["after"]
    assert (before["state"], after["state"]) == ("COMPLETED", "COMPLETED")
    assert before["video_url"] == f"/api/versions/{original}/video"
    assert after["video_url"] == f"/api/versions/{revision}/video"
    assert after["video_media_type"] == "application/json"  # Fake 매니페스트
    assert before["checks"]["preservation_rate"] is None
    assert after["checks"]["preservation_rate"] >= 0.9
    assert body["checks_delta"] == {
        "requirement_reflection_rate": 0.0,
        "consistency_rate": 0.0,
        "preservation_rate": None,
    }
    assert body["summary"] == {
        "scene_count": 3,
        "status_counts": {
            "REUSED": 1,
            "REGENERATED": 2,
            "ADDED": 0,
            "REMOVED": 0,
            "UNAVAILABLE": 0,
        },
        "changed_scene_keys": ["intro", "closeup"],
        "requirement_change_count": 2,
    }
    assert [(s["scene_key"], s["status"], s["conditions_changed"]) for s in body["scenes"]] == [
        ("intro", "REGENERATED", True),
        ("closeup", "REGENERATED", True),
        ("outro", "REUSED", False),
    ]

    intro = _scene(body, "intro")
    assert (intro["before"]["planned_start_ms"], intro["before"]["planned_end_ms"]) == (0, 4000)
    color = next(r for r in intro["requirements"] if r["predicate"] == "color")
    assert color == {
        "change": "CHANGED",
        "entity_key": "cup",
        "predicate": "color",
        "value_kind": "COLOR",
        "relational": False,
        "before_requirement_key": "intro:cup:color",
        "after_requirement_key": "intro:cup:color",
        "before_value": "red",
        "after_value": "teal",
        "before_verdict": "PASS",
        "after_verdict": "PASS",
    }
    assert {r["change"] for r in intro["requirements"] if r is not color} == {"UNCHANGED"}

    outro = _scene(body, "outro")
    assert outro["before"]["clip_url"] == f"/api/versions/{original}/scenes/outro/clip"
    assert outro["after"]["clip_url"] == f"/api/versions/{revision}/scenes/outro/clip"
    assert outro["after"]["clip_media_type"] == "application/json"
    reused_before = client.get(outro["before"]["clip_url"])
    reused_after = client.get(outro["after"]["clip_url"])
    assert reused_before.status_code == reused_after.status_code == 200
    assert reused_before.content == reused_after.content  # 재사용한 장면은 같은 바이트


def test_comparison_accepts_an_explicit_against_version(api):
    client, dispatcher = api
    original = _create(client, dispatcher)["version_id"]
    _generate(client, dispatcher, original)
    revision = _revise(client, original)
    _generate(client, dispatcher, revision)

    body = client.get(f"/api/versions/{original}/comparison?against={revision}").json()

    assert (body["version_id"], body["against_version_id"]) == (original, revision)
    assert body["against_is_parent"] is False
    assert [s["status"] for s in body["scenes"]] == ["REGENERATED", "REGENERATED", "REUSED"]
    color = next(r for r in _scene(body, "closeup")["requirements"] if r["predicate"] == "color")
    assert (color["before_value"], color["after_value"]) == ("teal", "red")


def test_comparison_of_a_draft_revision_shows_planned_changes_without_clips(api):
    client, dispatcher = api
    original = _create(client, dispatcher)["version_id"]
    _generate(client, dispatcher, original)
    revision = _revise(client, original)

    body = client.get(f"/api/versions/{revision}/comparison").json()

    assert body["after"]["state"] == "DRAFT"
    assert body["after"]["video_url"] is None and body["after"]["video_media_type"] is None
    assert body["after"]["checks"]["total"] == 0
    assert body["checks_delta"]["requirement_reflection_rate"] is None
    assert body["summary"]["status_counts"]["UNAVAILABLE"] == 3
    assert body["summary"]["changed_scene_keys"] == ["intro", "closeup"]
    intro = _scene(body, "intro")
    assert intro["after"]["clip_url"] is None and intro["before"]["clip_url"]
    color = next(r for r in intro["requirements"] if r["predicate"] == "color")
    assert (color["before_verdict"], color["after_verdict"]) == ("PASS", None)


def test_comparison_errors_use_the_common_error_shape(api):
    client, dispatcher = api
    original = _create(client, dispatcher)["version_id"]
    other = _create(client, dispatcher, title="다른 프로젝트")["version_id"]
    failed = _create(client, dispatcher, prompt="자연어만 있는 요구사항")["version_id"]  # IR 없음

    _assert_error(client.get("/api/versions/ver_missing/comparison"), 404, "NOT_FOUND")
    _assert_error(
        client.get(f"/api/versions/{original}/comparison?against=ver_missing"), 404, "NOT_FOUND"
    )
    no_parent = client.get(f"/api/versions/{original}/comparison")
    _assert_error(no_parent, 422, "VALIDATION_ERROR")
    assert set(no_parent.json()) == {"error_code", "error_message"}
    _assert_error(
        client.get(f"/api/versions/{original}/comparison?against={other}"),
        422,
        "VALIDATION_ERROR",
    )
    _assert_error(
        client.get(f"/api/versions/{failed}/comparison?against={original}"),
        409,
        "VERSION_NOT_COMPARABLE",
    )
    empty = client.get(f"/api/versions/{original}/comparison?against=")
    _assert_error(empty, 422, "VALIDATION_ERROR")
    assert empty.json()["details"][0]["loc"] == "query.against"


# ---- GET /api/projects/{id}/dashboard -----------------------------------------------


@pytest.mark.ac("AC-41")
def test_dashboard_returns_the_version_tree_and_summaries_in_one_call(api):
    client, dispatcher = api
    created = _create(client, dispatcher)
    project_id, root = created["project_id"], created["version_id"]
    _generate(client, dispatcher, root)
    teal = _revise(client, root)
    _generate(client, dispatcher, teal)
    black = _revise(client, root, "barista", "shirt_color", "black")  # DRAFT 로 둔다

    response = client.get(f"/api/projects/{project_id}/dashboard")

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["project_id"], body["title"]) == (project_id, "카페 신제품 숏폼")
    assert body["created_at"]
    assert body["summary"] == {
        "version_count": 3,
        "state_counts": {
            "STRUCTURING": 0,
            "DRAFT": 1,
            "QUEUED": 0,
            "PROCESSING": 0,
            "COMPLETED": 2,
            "FAILED": 0,
        },
        "latest_version_id": black,
        "latest_completed_version_id": teal,
    }
    assert [
        (
            v["version_id"],
            v["parent_version_id"],
            v["child_version_ids"],
            v["depth"],
            v["state"],
            v["changed_scene_count"],
        )
        for v in body["versions"]
    ] == [
        (root, None, [teal, black], 0, "COMPLETED", None),
        (teal, root, [], 1, "COMPLETED", 2),
        (black, root, [], 1, "DRAFT", 2),
    ]
    nodes = {v["version_id"]: v for v in body["versions"]}
    assert nodes[teal]["video_url"] == f"/api/versions/{teal}/video"
    assert nodes[teal]["video_media_type"] == "application/json"
    assert nodes[black]["video_url"] is None
    assert nodes[root]["checks"]["requirement_reflection_rate"] == 1.0
    assert nodes[teal]["checks"]["preservation_rate"] >= 0.9
    assert nodes[black]["checks"]["total"] == 0
    assert {v["scene_count"] for v in body["versions"]} == {3}
    assert all(v["created_at"] and v["error_code"] is None for v in body["versions"])


def test_dashboard_shows_failed_versions_with_their_error(api):
    client, dispatcher = api
    created = _create(client, dispatcher, prompt="자연어만 있는 요구사항")

    body = client.get(f"/api/projects/{created['project_id']}/dashboard").json()

    (node,) = body["versions"]
    assert (node["state"], node["error_code"]) == ("FAILED", "STRUCTURING_ERROR")
    assert node["error_message"]
    assert (node["scene_count"], node["changed_scene_count"]) == (None, None)
    assert body["summary"]["state_counts"]["FAILED"] == 1
    assert body["summary"]["latest_completed_version_id"] is None


def test_dashboard_of_unknown_project_returns_404(api):
    client, _ = api
    _assert_error(client.get("/api/projects/prj_missing/dashboard"), 404, "NOT_FOUND")


# ---- 계약 -------------------------------------------------------------------------


def test_openapi_signature_covers_the_new_fields_the_frontend_reads():
    signature = contract_signature(build_spec())
    comparison = "GET /api/versions/{version_id}/comparison"
    dashboard = "GET /api/projects/{project_id}/dashboard"
    for entry in (
        f"{comparison} param:against",
        f"{comparison} -> 200:scenes[].status",
        f"{comparison} -> 200:scenes[].after.clip_url",
        f"{comparison} -> 200:scenes[].requirements[].after_verdict",
        f"{comparison} -> 200:checks_delta.preservation_rate",
        f"{comparison} -> 200:after.checks.requirement_reflection_rate",
        f"{comparison} -> 409",
        f"{dashboard} -> 200:versions[].changed_scene_count",
        f"{dashboard} -> 200:versions[].checks.consistency_rate",
        f"{dashboard} -> 200:summary.state_counts",
        f"{dashboard} -> 404",
    ):
        assert entry in signature, entry
