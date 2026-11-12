"""변경 요청 해석기 (ADR-0019): 글로 쓴 수정 요청 -> 구조화된 변경 하나 + 유지할 요소.

컨셉 화면의 '변경 요청' 칸: "컵 색상만 청록색으로 동일해줘. 인물과 배경은 유지해줘."
"""

from __future__ import annotations

import json

import pytest

from frame.adapters.change_request import (
    LLMChangeRequestInterpreter,
    RuleBasedChangeRequestInterpreter,
)
from frame.adapters.llm import ReplayLLMClient
from frame.application.errors import LLMBadResponse
from frame.application.ports.change_request import ChangeRequestNotUnderstood
from frame.domain.enums import ChangeScope, KeepAspect
from frame.domain.revision import KeepItem

REQUEST = "컵 색상만 청록색으로 동일해줘. 인물과 배경은 유지해줘."


# ---- 규칙 기반 (DSL) -------------------------------------------------------------------


@pytest.fixture
def rules():
    return RuleBasedChangeRequestInterpreter()


def test_rule_based_reads_an_attribute_change(rules, ir):
    reading = rules.interpret(ir, "cup.color = teal")

    assert (reading.entity_key, reading.predicate, reading.new_value) == ("cup", "color", "teal")
    assert reading.scope is None and reading.scene_keys == () and reading.keep == ()


def test_rule_based_reads_scenes_scene_level_conditions_and_relations(rules, ir):
    scoped = rules.interpret(ir, "cup.color = dark teal @ closeup, intro")
    scene = rules.interpret(ir, "scene.lighting = cold")
    relation = rules.interpret(ir, "barista holds -> cup")

    assert (scoped.scope, scoped.scene_keys, scoped.new_value) == (
        ChangeScope.SCENES,
        ("closeup", "intro"),
        "dark teal",
    )
    assert (scene.entity_key, scene.predicate, scene.new_value) == (None, "lighting", "cold")
    assert (relation.entity_key, relation.predicate, relation.new_value) == (
        "barista",
        "holds",
        "cup",
    )


def test_rule_based_reads_what_to_keep(rules, ir):
    reading = rules.interpret(
        ir, "cup.color = teal; keep: barista.action, barista.모습, cup.shape, lighting, 배경"
    )

    assert reading.keep == (
        KeepItem(KeepAspect.ACTION, "barista"),
        KeepItem(KeepAspect.APPEARANCE, "barista"),
        KeepItem(KeepAspect.SHAPE, "cup"),
        KeepItem(KeepAspect.LIGHTING),
        KeepItem(KeepAspect.BACKGROUND),
    )


@pytest.mark.parametrize(
    "text",
    [
        REQUEST,  # 자연어는 규칙 기반으로 읽지 못한다
        "cup.color = teal; cup.texture = matte",  # 한 번에 하나
        "cup.color = teal; keep: cup.mood",
        "cup.color = teal; keep: action",  # 객체 요소에 객체가 없다
        "keep: lighting",  # 변경이 없다
    ],
)
def test_rule_based_says_so_when_it_cannot_read_the_request(rules, ir, text):
    with pytest.raises(ChangeRequestNotUnderstood) as excinfo:
        rules.interpret(ir, text)
    assert excinfo.value.code == "CHANGE_REQUEST_NOT_UNDERSTOOD"


# ---- LLM -----------------------------------------------------------------------------


def _answer(**overrides):
    answer = {
        "understood": True,
        "entity_key": "cup",
        "predicate": "color",
        "new_value": "teal",
        "scope": "ALL_APPEARANCES",
        "scene_keys": [],
        "keep": [
            {"aspect": "APPEARANCE", "entity_key": "barista"},
            {"aspect": "ACTION", "entity_key": "barista"},
            {"aspect": "BACKGROUND", "entity_key": None},
        ],
        "notes": "컵의 색을 청록으로 바꾸고 인물과 배경은 그대로 둡니다.",
    }
    answer.update(overrides)
    return json.dumps(answer, ensure_ascii=False)


def _llm(*answers):
    client = ReplayLLMClient(list(answers))
    return LLMChangeRequestInterpreter(client), client


def test_llm_turns_a_sentence_into_one_change_and_keep_items(ir):
    interpreter, client = _llm(_answer())

    reading = interpreter.interpret(ir, REQUEST, entity_key="cup")

    assert (reading.entity_key, reading.predicate, reading.new_value) == ("cup", "color", "teal")
    assert reading.scope is ChangeScope.ALL_APPEARANCES
    assert reading.keep == (
        KeepItem(KeepAspect.APPEARANCE, "barista"),
        KeepItem(KeepAspect.ACTION, "barista"),
        KeepItem(KeepAspect.BACKGROUND),
    )
    assert reading.notes.startswith("컵의 색을")
    (call,) = client.calls
    user = call["user"].splitlines()
    assert "- cup (prop): 세라믹 머그컵" in user
    assert any(
        line.startswith("- intro (0s-4s): ") and "[subjects: barista, cup, cafe]" in line
        for line in user
    )
    assert "    cup.color = red" in user
    assert "The user is editing this subject on screen: cup" in user
    assert user[-2:] == ["Request:", REQUEST]
    assert "color" in next(line for line in user if line.startswith("Predicates: "))
    assert call["schema_name"] == "change_request" and call["images"] == ()


def test_llm_keeps_only_what_exists_in_this_video(ir):
    answer = _answer(
        scope="SCENES",
        scene_keys=["closeup", "ending"],
        keep=[
            {"aspect": "ACTION", "entity_key": "dog"},
            {"aspect": "SHAPE", "entity_key": None},  # 객체 요소인데 객체가 없다
            {"aspect": "LIGHTING", "entity_key": None},
            {"aspect": "LIGHTING", "entity_key": None},
        ],
    )
    interpreter, _ = _llm(answer)

    reading = interpreter.interpret(ir, REQUEST)

    assert (reading.scope, reading.scene_keys) == (ChangeScope.SCENES, ("closeup",))
    assert reading.keep == (KeepItem(KeepAspect.LIGHTING),)
    assert "장면 ending" in reading.notes and "dog" in reading.notes


def test_llm_scope_without_a_known_scene_is_left_open(ir):
    interpreter, _ = _llm(_answer(scope="SCENES", scene_keys=["ending"]))
    assert interpreter.interpret(ir, REQUEST).scope is None


@pytest.mark.parametrize(
    "answer",
    [
        _answer(understood=False, notes="무엇을 바꾸라는 요청인지 알 수 없습니다."),
        _answer(entity_key="dog"),
        _answer(predicate=""),
        _answer(new_value="  "),
    ],
)
def test_llm_says_so_when_the_request_cannot_be_structured(ir, answer):
    interpreter, _ = _llm(answer)
    with pytest.raises(ChangeRequestNotUnderstood):
        interpreter.interpret(ir, "멋지게 해줘")


def test_llm_answer_that_is_not_json_is_a_bad_response(ir):
    interpreter, _ = _llm("not json")
    with pytest.raises(LLMBadResponse):
        interpreter.interpret(ir, REQUEST)
