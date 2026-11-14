"""SeriesPlanner 계약 테스트 (ADR-0026). 틀 채우기와 LLM 기획이 같은 규약을 지킨다.

- 돌려준 내용은 build_episode_plan 을 통과한다 (테마의 자리를 모두, 그 자리만 채운다).
- 요청이 준 변수의 값을 바꾸지 않는다.
LLM 기획은 재생 응답으로 돌린다 (네트워크·과금 없음).
"""

from __future__ import annotations

import pytest

from frame.adapters.series import TemplateSeriesPlanner
from frame.application.ports.series import EarlierEpisode, PlanningRequest
from frame.domain.episode import build_episode_plan
from frame.domain.errors import ValidationError
from tests.support.themes import cafe_theme, matcha_brief


@pytest.fixture(params=["template"])
def make_planner(request):
    """기획기를 만드는 함수. 요청마다 새로 만든다."""
    return lambda: TemplateSeriesPlanner()


def _request(**brief_changes) -> PlanningRequest:
    return PlanningRequest(
        theme=cafe_theme(),
        brief=matcha_brief(**brief_changes),
        earlier=(
            EarlierEpisode("바닐라 라떼 편", "바닐라 라떼를 소개한다", {"menu": "바닐라 라떼"}),
        ),
    )


def test_content_fills_every_theme_slot_and_builds_a_valid_plan(make_planner):
    request = _request()
    content = make_planner().plan(request)
    plan = build_episode_plan(request.theme, request.brief, content)

    assert [s.slot_key for s in content.slots] == [s.slot_key for s in request.theme.format.slots]
    plan.validate()
    assert [s.duration_ms for s in plan.ir.scenes] == [4000, 4000, 4000, 4000]
    assert all(scene.description for scene in plan.ir.scenes)
    # 테마의 고정 요소는 기획이 무엇을 내든 그대로 들어간다
    outfits = {
        r.expected_value
        for r in plan.ir.iter_requirements()
        if r.entity_key == "barista" and r.predicate == "outfit"
    }
    assert outfits == {"white shirt and brown apron"}


def test_brief_variables_are_kept_as_given(make_planner):
    request = _request()
    plan = build_episode_plan(request.theme, request.brief, make_planner().plan(request))

    assert plan.variables == {"menu": "말차 라떼", "price": "5,000원"}
    assert plan.overlay("closeup:price").text == "말차 라떼 5,000원"


def test_missing_required_variable_is_a_validation_error(make_planner):
    with pytest.raises(ValidationError) as caught:
        make_planner().plan(_request(variables={"menu": "말차 라떼"}))
    assert "price" in caught.value.context["variables"]


# ---- 틀 채우기 기획 ----
def test_template_planner_swaps_the_source_values_for_this_episode():
    request = _request()
    content = TemplateSeriesPlanner().plan(request)
    lines = {s.slot_key: s.narration for s in content.slots}

    assert lines == {
        "greeting": "안녕하세요, 오늘의 메뉴예요.",
        "show": "말차 라떼입니다.",  # 원본: 바닐라 라떼입니다.
        "closeup": "가격은 5,000원.",  # 원본: 가격은 4,500원.
        "outro": "매장에서 만나요!",
    }
    assert content.slots[1].description == "바리스타가 오늘의 메뉴를 들어 보인다"
    assert content.entities == () and all(not s.requirements for s in content.slots)


def test_template_planner_is_deterministic_and_records_requests():
    planner = TemplateSeriesPlanner()
    request = _request()

    assert planner.plan(request) == planner.plan(request)
    assert planner.requests == [request, request]
