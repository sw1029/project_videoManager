"""변경 계획 미리보기 API (ADR-0019): POST /api/versions/{id}/revision-plans.

컨셉 화면의 '④ 실행' 첫 단계 '변경 계획 확인': 글로 쓴 요청을 읽고, 다시 만들 장면과 어림 비용·시간을 보여 준 뒤
사용자가 확인하면 그 계획(revision_request)을 그대로 POST /revisions 에 보낸다.
"""

import json

import pytest
from fastapi.testclient import TestClient

from frame.adapters.change_request import LLMChangeRequestInterpreter
from frame.adapters.llm import ReplayLLMClient
from frame.application.errors import LLMTimeout
from frame.application.ports.checker import Checker, CheckOutcome
from frame.application.ports.generator import GeneratedClip, VideoGenerator
from frame.domain.enums import CheckType, Verdict
from frame.presentation.app import create_app
from tests.support.factories import CAFE_PROMPT, dispatcher_of, make_container

REQUEST = "컵 색상만 청록색으로 동일해줘. 인물과 배경은 유지해줘."


class CostedGenerator(VideoGenerator):
    """클립마다 비용·시간 기록을 남기는 생성기 (실제 어댑터의 clip.metadata 흉내)."""

    def __init__(self, store):
        self._store = store

    def generate_scene(self, request):
        scene = request.scene
        uri = self._store.put(
            f"clips/{request.version_id}/{scene.scene_key}.json", b"{}", "application/json"
        )
        metadata = {"cost_usd": 0.2125, "generation_seconds": 100.0}
        return GeneratedClip(scene.scene_key, uri, scene.duration_ms, request.fingerprint, metadata)


class CupColorFails(Checker):
    """컵 색 요구조건만 FAIL 로 판정한다 (요구는 red 인데 영상이 어긋난 상황)."""

    check_type = CheckType.REQUIREMENT

    def check(self, ctx):
        return [
            CheckOutcome(
                self.check_type,
                Verdict.FAIL if r.key.endswith(":cup:color") else Verdict.PASS,
                requirement_key=r.key,
                entity_key=r.entity_key,
                scene_key=r.scene_key,
            )
            for r in ctx.ir.iter_requirements()
        ]


def _api(**overrides):
    container = make_container(**overrides)
    client = TestClient(create_app(container))
    dispatcher = dispatcher_of(container)
    created = client.post(
        "/api/projects", json={"title": "카페 신제품 숏폼", "prompt": CAFE_PROMPT}
    )
    dispatcher.run_all()
    return client, dispatcher, created.json()["version_id"]


def _plan(client, version_id, **body):
    return client.post(f"/api/versions/{version_id}/revision-plans", json=body)


def test_structured_change_is_planned_without_creating_a_version():
    client, _, version_id = _api()

    response = _plan(client, version_id, entity_key="cup", predicate="color", new_value="teal")

    assert response.status_code == 200, response.text
    plan = response.json()
    assert (plan["kind"], plan["interpreted"], plan["previous_value"]) == ("CHANGE", False, "red")
    assert plan["target_scene_keys"] == ["intro", "closeup"]
    assert [(s["scene_key"], s["will_regenerate"], s["current_value"]) for s in plan["scenes"]] == [
        ("intro", True, "red"),
        ("closeup", True, "red"),
        ("outro", False, None),  # 컵이 없는 장면
    ]
    assert plan["estimate"] == {
        "regenerated_scene_count": 2,
        "reused_scene_count": 1,
        "recorded_scene_count": 0,
        "cost_usd": None,
        "seconds": None,
    }
    project_id = client.get(f"/api/versions/{version_id}").json()["project_id"]
    assert len(client.get(f"/api/projects/{project_id}").json()["versions"]) == 1  # 버전은 그대로


@pytest.mark.ac("AC-01")
def test_confirmed_plan_is_sent_back_as_it_is_to_create_the_revision():
    """컨셉 화면의 흐름: 요구는 그대로인데 영상이 어긋났다 -> 계획 확인 -> 해당 구간 재생성."""
    store_holder = {}

    def generator_factory():
        from frame.adapters.storage import InMemoryArtifactStore

        store_holder["store"] = InMemoryArtifactStore()
        return CostedGenerator(store_holder["store"])

    generator = generator_factory()
    client, dispatcher, version_id = _api(
        artifact_store=store_holder["store"], generator=generator, checkers=(CupColorFails(),)
    )
    client.post(f"/api/versions/{version_id}/generations")
    dispatcher.run_all()

    plan = _plan(
        client,
        version_id,
        request_text="cup.color = red; keep: barista.action, lighting",
    ).json()

    assert (plan["kind"], plan["interpreted"]) == ("REPAIR", True)  # 값이 이미 red: 다시 맞추기
    assert [(s["scene_key"], s["verdict"]) for s in plan["scenes"]] == [
        ("intro", "FAIL"),
        ("closeup", "FAIL"),
        ("outro", None),
    ]
    assert plan["estimate"]["cost_usd"] == pytest.approx(0.425)
    assert plan["estimate"]["seconds"] == 200.0 and plan["estimate"]["recorded_scene_count"] == 2
    request = plan["revision_request"]
    assert request["kind"] == "REPAIR" and request["new_value"] == "red"
    assert request["keep"] == [
        {"aspect": "ACTION", "entity_key": "barista"},
        {"aspect": "LIGHTING", "entity_key": None},
    ]

    created = client.post(f"/api/versions/{version_id}/revisions", json=request)

    assert created.status_code == 201, created.text
    assert created.json()["kind"] == "REPAIR"
    assert created.json()["changed_scene_keys"] == plan["target_scene_keys"]
    revision = client.get(f"/api/versions/{created.json()['version_id']}").json()["revision"]
    assert revision["request_text"] == "cup.color = red; keep: barista.action, lighting"


def test_what_the_user_picked_on_screen_wins_over_the_reading():
    client, _, version_id = _api()

    plan = _plan(
        client,
        version_id,
        request_text="cup.color = teal; keep: lighting",
        scope="SCENES",
        scene_keys=["closeup"],
        keep=[{"aspect": "SHAPE", "entity_key": "cup"}],
    ).json()

    assert plan["target_scene_keys"] == ["closeup"]
    assert plan["revision_request"]["scope"] == "SCENES"
    assert plan["revision_request"]["keep"] == [{"aspect": "SHAPE", "entity_key": "cup"}]


def _llm_answer(**overrides):
    answer = {
        "understood": True,
        "entity_key": "cup",
        "predicate": "color",
        "new_value": "teal",
        "scope": None,
        "scene_keys": [],
        "keep": [
            {"aspect": "APPEARANCE", "entity_key": "barista"},
            {"aspect": "BACKGROUND", "entity_key": None},
        ],
        "notes": "컵의 색을 청록으로 바꾸고 인물과 배경은 그대로 둡니다.",
    }
    answer.update(overrides)
    return json.dumps(answer, ensure_ascii=False)


def test_a_sentence_is_read_by_the_llm_interpreter():
    llm = ReplayLLMClient([_llm_answer()])
    client, _, version_id = _api(change_interpreter=LLMChangeRequestInterpreter(llm))

    plan = _plan(client, version_id, request_text=REQUEST, entity_key="cup").json()

    assert (plan["kind"], plan["interpreted"]) == ("CHANGE", True)
    assert plan["notes"].startswith("컵의 색을")
    assert plan["revision_request"] == {
        "entity_key": "cup",
        "predicate": "color",
        "new_value": "teal",
        "scope": "ALL_APPEARANCES",
        "scene_keys": [],
        "current_value": None,
        "kind": "CHANGE",
        "keep": [
            {"aspect": "APPEARANCE", "entity_key": "barista"},
            {"aspect": "BACKGROUND", "entity_key": None},
        ],
        "request_text": REQUEST,
    }
    assert "The user is editing this subject on screen: cup" in llm.calls[0]["user"]


def test_requests_that_cannot_be_read_or_served_are_reported_with_their_own_codes():
    not_understood = ReplayLLMClient(
        [_llm_answer(understood=False, notes="무엇을 바꿀지 모르겠습니다.")]
    )
    client, _, version_id = _api(change_interpreter=LLMChangeRequestInterpreter(not_understood))
    unclear = _plan(client, version_id, request_text="멋지게 해줘")

    timed_out = ReplayLLMClient([LLMTimeout("LLM 응답 없음")])
    other, _, other_id = _api(change_interpreter=LLMChangeRequestInterpreter(timed_out))
    unavailable = _plan(other, other_id, request_text=REQUEST)

    assert unclear.status_code == 422
    assert unclear.json()["error_code"] == "CHANGE_REQUEST_NOT_UNDERSTOOD"
    assert "무엇을 바꿀지" in unclear.json()["error_message"]
    assert unavailable.status_code == 503 and unavailable.json()["error_code"] == "LLM_TIMEOUT"


def test_plan_requests_are_validated_like_revisions():
    client, _, version_id = _api()

    nothing = _plan(client, version_id)
    missing = _plan(client, "ver_missing", entity_key="cup", predicate="color", new_value="teal")
    ghost = _plan(client, version_id, entity_key="ghost", predicate="color", new_value="teal")
    wrong_repair = _plan(
        client, version_id, entity_key="cup", predicate="color", new_value="teal", kind="REPAIR"
    )

    assert nothing.status_code == 422 and missing.status_code == 404 and ghost.status_code == 404
    assert wrong_repair.status_code == 422 and "CHANGE" in wrong_repair.json()["error_message"]


def test_health_tells_which_kind_of_request_text_is_understood():
    client, _, _ = _api()
    assert client.get("/health").json()["adapters"]["change_interpreter"] == "rule_based"
