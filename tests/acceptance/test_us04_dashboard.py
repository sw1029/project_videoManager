"""US-04 대시보드 — AC-41(통합 검수 대시보드), AC-43(수정 전/후 비교 뷰어).

화면이 부르는 HTTP API 로 시나리오를 확인한다 (Fake 생성·추적·검수 어댑터).
"""

import pytest
from fastapi.testclient import TestClient

from frame.adapters.checker import (
    FakeConsistencyChecker,
    FakePreservationChecker,
    FakeRequirementChecker,
)
from frame.presentation.app import create_app
from tests.support.factories import CAFE_PROMPT, dispatcher_of, make_container


class Screen:
    """화면 대역: 백엔드 API 만 호출한다."""

    def __init__(self, **overrides) -> None:
        container = make_container(**overrides)
        self.client = TestClient(create_app(container))
        self._dispatcher = dispatcher_of(container)

    def create(self) -> dict:
        response = self.client.post(
            "/api/projects", json={"title": "카페 신제품 숏폼", "prompt": CAFE_PROMPT}
        )
        assert response.status_code == 202, response.text
        self._dispatcher.run_all()
        return response.json()

    def generate(self, version_id: str) -> None:
        assert self.client.post(f"/api/versions/{version_id}/generations").status_code == 202
        self._dispatcher.run_all()

    def revise(self, version_id: str, **change) -> dict:
        response = self.client.post(f"/api/versions/{version_id}/revisions", json=change)
        assert response.status_code == 201, response.text
        return response.json()

    def get(self, path: str) -> dict:
        response = self.client.get(path)
        assert response.status_code == 200, response.text
        return response.json()


def _requirements(comparison: dict) -> dict[str, dict]:
    return {
        r["after_requirement_key"]: r
        for scene in comparison["scenes"]
        for r in scene["requirements"]
        if r["after_requirement_key"] is not None
    }


@pytest.mark.ac("AC-41")
def test_ac41_dashboard_shows_requirements_reflection_and_before_after_on_one_screen():
    # Given: 영상 생성과 분석(추적·검수)이 끝난 원본과 수정 버전. 검수기는 한 요구조건을 미달로 판정한다
    screen = Screen(
        checkers=(
            FakeRequirementChecker(failing_keys={"closeup:cup:color"}),
            FakeConsistencyChecker(),
            FakePreservationChecker(),
        )
    )
    created = screen.create()
    original = created["version_id"]
    screen.generate(original)
    revision = screen.revise(original, entity_key="cup", predicate="color", new_value="teal")
    screen.generate(revision["version_id"])

    # When: 메인 검수 대시보드에 들어가 최신 버전을 연다 (대시보드 1회 + 전후 비교 1회)
    dashboard = screen.get(f"/api/projects/{created['project_id']}/dashboard")
    latest = dashboard["summary"]["latest_completed_version_id"]
    comparison = screen.get(f"/api/versions/{latest}/comparison")

    # Then: 버전 트리와 버전별 반영 상태
    assert latest == revision["version_id"]
    tree = {v["version_id"]: v for v in dashboard["versions"]}
    assert tree[original]["child_version_ids"] == [latest]
    assert tree[latest]["parent_version_id"] == original
    assert tree[latest]["changed_scene_count"] == len(revision["changed_scene_keys"])
    for node in tree.values():
        assert node["state"] == "COMPLETED" and node["video_url"]
        assert node["checks"]["requirement_reflection_rate"] == pytest.approx(7 / 8)
        assert node["checks"]["consistency_rate"] == 1.0

    # Then: 요구사항 목록 전체와 각 요구조건의 반영 상태
    version = screen.get(f"/api/versions/{latest}")
    listed = _requirements(comparison)
    assert set(listed) == {
        r["requirement_key"] for s in version["ir"]["scenes"] for r in s["requirements"]
    }
    assert listed["closeup:cup:color"]["after_verdict"] == "FAIL"
    assert {r["after_verdict"] for k, r in listed.items() if k != "closeup:cup:color"} == {"PASS"}

    # Then: 수정 전/후 결과 (영상·장면 클립·바뀐 조건)
    assert comparison["before"]["video_url"] and comparison["after"]["video_url"]
    assert comparison["summary"]["changed_scene_keys"] == revision["changed_scene_keys"]
    assert all(s["before"]["clip_url"] and s["after"]["clip_url"] for s in comparison["scenes"])
    assert {
        (k, r["before_value"], r["after_value"])
        for k, r in listed.items()
        if r["change"] != "UNCHANGED"
    } == {("intro:cup:color", "red", "teal"), ("closeup:cup:color", "red", "teal")}


@pytest.mark.ac("AC-43")
def test_ac43_viewer_shows_only_the_intended_region_changed_and_the_rest_preserved():
    # Given: 객체 속성(컵 색)을 바꾸고 선택적 재생성을 마쳤다
    screen = Screen()
    original = screen.create()["version_id"]
    screen.generate(original)
    revision = screen.revise(original, entity_key="cup", predicate="color", new_value="teal")
    screen.generate(revision["version_id"])

    # When: 수정 전/후 비교 뷰어를 연다
    comparison = screen.get(f"/api/versions/{revision['version_id']}/comparison")

    # Then: 두 화면 — 전후 영상을 각각 재생할 수 있다
    for side in ("before", "after"):
        assert screen.client.get(comparison[side]["video_url"]).status_code == 200
    assert comparison["before"]["video_url"] != comparison["after"]["video_url"]

    # Then: 의도한 수정 영역(컵이 나오는 장면의 색 조건)만 바뀌고 반영되었다
    changed = [
        (s["scene_key"], r["predicate"], r["before_value"], r["after_value"], r["after_verdict"])
        for s in comparison["scenes"]
        for r in s["requirements"]
        if r["change"] != "UNCHANGED"
    ]
    assert changed == [
        ("intro", "color", "red", "teal", "PASS"),
        ("closeup", "color", "red", "teal", "PASS"),
    ]
    regenerated = {s["scene_key"] for s in comparison["scenes"] if s["status"] == "REGENERATED"}
    assert regenerated == set(revision["changed_scene_keys"]) == {"intro", "closeup"}

    # Then: 나머지 영역은 유지 — 비대상 장면은 같은 클립(같은 바이트), 보존율 >= 90%
    (outro,) = [s for s in comparison["scenes"] if s["status"] == "REUSED"]
    assert outro["scene_key"] == "outro" and outro["conditions_changed"] is False
    before_clip = screen.client.get(outro["before"]["clip_url"]).content
    after_clip = screen.client.get(outro["after"]["clip_url"]).content
    assert before_clip == after_clip
    assert comparison["after"]["checks"]["preservation_rate"] >= 0.9


@pytest.mark.ac("AC-43")
def test_ac43_scene_scoped_condition_change_regenerates_only_that_scene():
    # Given: 장면 수준이 아닌 배경 객체 조건(조명)을 마지막 장면에만 바꿔 재생성했다
    screen = Screen()
    original = screen.create()["version_id"]
    screen.generate(original)
    revision = screen.revise(
        original,
        entity_key="cafe",
        predicate="lighting",
        new_value="cool",
        scope="SCENES",
        scene_keys=["outro"],
    )
    screen.generate(revision["version_id"])

    # When
    comparison = screen.get(f"/api/versions/{revision['version_id']}/comparison")

    # Then: outro 만 재생성되고, 다른 장면은 그대로 재사용된다
    assert [(s["scene_key"], s["status"]) for s in comparison["scenes"]] == [
        ("intro", "REUSED"),
        ("closeup", "REUSED"),
        ("outro", "REGENERATED"),
    ]
    assert comparison["summary"]["requirement_change_count"] == 1
    assert comparison["checks_delta"]["requirement_reflection_rate"] == 0.0
