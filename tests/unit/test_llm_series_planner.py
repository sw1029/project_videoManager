"""LLMSeriesPlanner: 질의에 담는 것, 응답 해석, 검증 실패 뒤 다시 묻기 (재생 응답, 과금 없음)."""

from __future__ import annotations

import copy
import json

import pytest

from frame.adapters.llm import ReplayLLMClient
from frame.adapters.series.llm import (
    RESPONSE_SCHEMA,
    SYSTEM_PROMPT,
    LLMSeriesPlanner,
    build_user_prompt,
    parse_content,
)
from frame.application.errors import LLMTimeout
from frame.application.ports.series import EarlierEpisode, PlanningRequest, SeriesPlanningError
from frame.domain.enums import EntityKind
from frame.domain.episode import build_episode_plan
from frame.domain.errors import ValidationError
from tests.support.themes import cafe_theme, matcha_brief, matcha_content_document


def _request(**brief_changes) -> PlanningRequest:
    return PlanningRequest(
        theme=cafe_theme(),
        brief=matcha_brief(**brief_changes),
        earlier=(
            EarlierEpisode("바닐라 라떼 편", "바닐라 라떼를 소개한다", {"menu": "바닐라 라떼"}),
        ),
    )


def _planner(*responses, **options) -> tuple[LLMSeriesPlanner, ReplayLLMClient]:
    llm = ReplayLLMClient(
        [
            r if isinstance(r, str | Exception) else json.dumps(r, ensure_ascii=False)
            for r in responses
        ]
    )
    return LLMSeriesPlanner(llm, **options), llm


def test_answer_becomes_episode_content():
    planner, llm = _planner(matcha_content_document())
    content = planner.plan(_request())

    assert [s.slot_key for s in content.slots] == ["greeting", "show", "closeup", "outro"]
    assert [(e.entity_key, e.kind) for e in content.entities] == [("latte", EntityKind.PROP)]
    show = content.slots[1]
    assert show.entity_keys == ("latte",) and show.narration == "말차 라떼입니다."
    assert [r.key for r in show.requirements] == ["show:barista:holds:mug", "show:latte:color"]
    # 속성 조건에 붙어 온 상대 객체는 뗀다
    assert content.slots[2].requirements[0].related_entity_key is None
    assert content.slots[0].variables == {"caption": "오늘은 초록초록!"}
    assert planner.attempts == 1 and planner.last_completion is not None
    assert llm.calls[0]["schema"] == RESPONSE_SCHEMA and llm.calls[0]["system"] == SYSTEM_PROMPT


def test_brief_values_survive_whatever_the_model_says():
    """모델이 메뉴를 '녹차 라떼' 로 바꿔 내도 요청의 '말차 라떼' 가 화면 문구에 쓰인다."""
    planner, _ = _planner(matcha_content_document())
    request = _request()
    plan = build_episode_plan(request.theme, request.brief, planner.plan(request))

    assert plan.variables == {"menu": "말차 라떼", "price": "5,000원"}
    assert plan.overlay("closeup:price").text == "말차 라떼 5,000원"
    assert any("녹차 라떼" in note for note in plan.notes)


def test_model_fills_a_variable_the_brief_left_out():
    planner, llm = _planner(matcha_content_document())
    request = _request(variables={"menu": "말차 라떼"})
    plan = build_episode_plan(request.theme, request.brief, planner.plan(request))

    assert plan.variables["price"] == "5,000원"
    assert (
        "price (per episode)" in llm.calls[0]["user"]
        and "not given: fill it in" in llm.calls[0]["user"]
    )


def test_prompt_tells_the_model_what_is_fixed_and_how_long_each_line_may_be():
    prompt = build_user_prompt(_request())

    assert "Series theme: 카페 신메뉴 소개" in prompt
    assert (
        "- barista (person): 바리스타 — role: 진행자 — fixed: outfit: white shirt and brown apron"
        in prompt
    )
    assert (
        "Style (added by the system): lighting: warm cafe lighting; location: cafe counter"
        in prompt
    )
    # 4초 장면에서 앞뒤 0.2초를 뺀 3.6초 × 초당 5.5글자 = 19글자
    assert "- greeting (4s): 바리스타가 카메라를 보며 인사한다 | cast: barista" in prompt
    assert "narration limit: 19 characters" in prompt
    assert 'narration purpose: 메뉴 이름을 말한다 (source said: "바닐라 라떼입니다.")' in prompt
    assert "- closeup (4s)" in prompt and "fixed: camera: close up" in prompt
    assert "tone: 친근한 존댓말, 밝고 빠르게" in prompt and "language ko" in prompt
    assert '- price (price, closeup): "{menu} {price}"' in prompt
    assert (
        '- menu (per episode): 소개하는 메뉴 이름 — in the source: "바닐라 라떼" — given: "말차 라떼" (keep it)'
        in prompt
    )
    assert "- caption (per slot)" in prompt
    assert "- 바닐라 라떼 편: 바닐라 라떼를 소개한다" in prompt
    assert "title: 말차 라떼 편" in prompt and "Topic: 초록색 말차 라떼를 소개한다" in prompt


def test_rejected_answer_is_sent_back_with_the_reason():
    broken = copy.deepcopy(matcha_content_document())
    broken["slots"] = broken["slots"][:3]  # 자리 하나가 빠졌다
    planner, llm = _planner(broken, matcha_content_document())
    content = planner.plan(_request())

    assert planner.attempts == 2 and len(content.slots) == 4
    retry = llm.calls[1]["user"]
    assert "Your previous answer was rejected" in retry and "outro" in retry
    assert retry.startswith(llm.calls[0]["user"])


def test_planning_fails_after_the_last_attempt_and_llm_errors_pass_through():
    broken = copy.deepcopy(matcha_content_document())
    broken["slots"][1]["entity_keys"] = ["ghost"]  # 정의하지 않은 객체
    planner, llm = _planner(broken, "not json", max_attempts=2)

    with pytest.raises(SeriesPlanningError, match="2회"):
        planner.plan(_request())
    assert len(llm.calls) == 2
    timeout, _ = _planner(LLMTimeout("응답 없음"))
    with pytest.raises(LLMTimeout):
        timeout.plan(_request())
    with pytest.raises(ValidationError, match="제목"):
        _planner(matcha_content_document())[0].plan(_request(title=" "))


def test_parse_content_rejects_malformed_documents():
    theme = cafe_theme()
    with pytest.raises(ValueError):
        parse_content(theme, ["not", "an", "object"])
    with pytest.raises(KeyError):
        parse_content(theme, {"slots": [{"description": "키가 없다"}]})
    bad_kind = copy.deepcopy(matcha_content_document())
    bad_kind["entities"][0]["kind"] = "thing"
    with pytest.raises(ValueError):
        parse_content(theme, bad_kind)
    # 자리별 변수가 아닌 값은 자리에 두지 않는다
    stray = copy.deepcopy(matcha_content_document())
    stray["slots"][0]["variables"].append({"key": "menu", "value": "다른 메뉴"})
    assert parse_content(theme, stray).slots[0].variables == {"caption": "오늘은 초록초록!"}
