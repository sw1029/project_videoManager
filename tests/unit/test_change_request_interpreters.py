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


def test_llm_reads_the_look_of_the_scene_as_its_background(ir):
    """실제 모델(gpt-4.1-mini)이 '배경은 유지' 를 객체 없는 APPEARANCE 로 돌려줬다 (2차 e2e 1회차).

    그대로 버리면 사용자가 말한 유지 요소가 사라진다. 장면의 모습, 곧 배경으로 읽는다.
    """
    keep = [
        {"aspect": "APPEARANCE", "entity_key": "barista"},
        {"aspect": "APPEARANCE", "entity_key": None},
        {"aspect": "BACKGROUND", "entity_key": None},
    ]
    interpreter, client = _llm(_answer(keep=keep))

    reading = interpreter.interpret(ir, REQUEST)

    assert reading.keep == (
        KeepItem(KeepAspect.APPEARANCE, "barista"),
        KeepItem(KeepAspect.BACKGROUND),
    )
    assert "이 영상에 없어" not in reading.notes
    assert "BACKGROUND" in client.calls[0]["system"]


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


# ---- 두 구현의 같은 경계 (감사 A-30) -----------------------------------------------------
# 포트 계약: 성공한 해석은 이 영상에 있는 객체만 가리킨다. 구현마다 따로 맞추면 같은 요청이 구현에 따라
# 다른 오류(규칙 기반 404 / LLM 422)가 되고, 유지 요소 하나 때문에 계획 전체가 거절된다.


def test_both_interpreters_refuse_a_subject_that_is_not_in_the_video(rules, ir):
    llm, _ = _llm(_answer(entity_key="dog"))

    for read in (
        lambda: rules.interpret(ir, "dog.color = teal"),
        lambda: llm.interpret(ir, REQUEST),
    ):
        with pytest.raises(ChangeRequestNotUnderstood, match="이 영상에 없습니다") as excinfo:
            read()
        assert excinfo.value.code == "CHANGE_REQUEST_NOT_UNDERSTOOD"


def test_both_interpreters_leave_out_what_to_keep_for_a_subject_that_is_not_there(rules, ir):
    keep = [{"aspect": "ACTION", "entity_key": "dog"}, {"aspect": "LIGHTING", "entity_key": None}]
    llm, _ = _llm(_answer(keep=keep))

    for reading in (
        rules.interpret(ir, "cup.color = teal; keep: dog.action, lighting"),
        llm.interpret(ir, REQUEST),
    ):
        assert reading.keep == (KeepItem(KeepAspect.LIGHTING),)
        assert "이 영상에 없어 뺀 것" in reading.notes and "dog" in reading.notes


def test_both_interpreters_leave_out_keeping_the_very_thing_the_change_alters(rules, ir):
    """컵 색을 바꾸면서 컵의 모습을 그대로 두라는 것은 어긋난다. 그 유지 요소만 빼고 알린다.

    그대로 돌려주면 수정 요청을 푸는 단계가 계획 전체를 거절한다 (모델이 유지 요소를 넉넉히 적는 일은 흔하다).
    """
    keep = [
        {"aspect": "APPEARANCE", "entity_key": "cup"},
        {"aspect": "ACTION", "entity_key": "barista"},
    ]
    llm, _ = _llm(_answer(keep=keep))

    for reading in (
        rules.interpret(ir, "cup.color = teal; keep: cup.모습, barista.action"),
        llm.interpret(ir, REQUEST),
    ):
        assert reading.keep == (KeepItem(KeepAspect.ACTION, "barista"),)
        assert "바꾸는 것 자체라 뺀 유지 요소" in reading.notes and "cup" in reading.notes
        assert "이 영상에 없어" not in reading.notes


def test_llm_keys_that_carry_extra_text_are_matched_to_the_video(ir):
    """실제 모델은 키 대신 질의의 한 줄을 통째로 돌려줄 때가 있다 ('cup (prop): 세라믹 머그컵')."""
    answer = _answer(
        entity_key="cup (prop): 세라믹 머그컵",
        scope="SCENES",
        scene_keys=["closeup (4s-8s)", "- intro"],
        keep=[{"aspect": "ACTION", "entity_key": "barista (person)"}],
    )
    llm, _ = _llm(answer)

    reading = llm.interpret(ir, REQUEST)

    assert reading.entity_key == "cup"
    assert (reading.scope, reading.scene_keys) == (ChangeScope.SCENES, ("closeup", "intro"))
    assert reading.keep == (KeepItem(KeepAspect.ACTION, "barista"),)
    assert "뺀" not in reading.notes


def test_a_key_that_only_starts_like_a_subject_is_not_that_subject(ir):
    llm, _ = _llm(_answer(entity_key="cupboard"))

    with pytest.raises(ChangeRequestNotUnderstood, match="cupboard"):
        llm.interpret(ir, REQUEST)
