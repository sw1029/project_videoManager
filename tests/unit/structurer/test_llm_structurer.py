"""LLMStructurer: 프롬프트 구성, 자기수정 재시도, 가벼운 보정, 오류 전파. 모델은 ReplayLLMClient 로 대체한다."""

import json

import pytest

from frame.adapters.llm import ReplayLLMClient
from frame.adapters.structurer import LLMStructurer
from frame.adapters.structurer.llm_schema import IR_SCHEMA_NAME
from frame.application.errors import LLMTimeout, StructuringError
from frame.application.ports.structurer import ReferenceImageInput, StructuringRequest
from tests.support.factories import cafe_ir


def _document():
    return cafe_ir().to_dict()


def _request(brief="카페 신제품 숏폼", duration=12000):
    return StructuringRequest(
        brief,
        reference_images=(ReferenceImageInput("mem://refs/cup.png", "cup"),),
        target_duration_ms=duration,
    )


def test_valid_response_becomes_ir_and_prompts_carry_rules():
    client = ReplayLLMClient([json.dumps(_document())])
    structurer = LLMStructurer(client)

    ir = structurer.structure(_request())

    assert ir == cafe_ir()
    assert structurer.attempts == 1
    call = client.calls[0]
    assert call["schema_name"] == IR_SCHEMA_NAME
    assert "holds" in call["system"] and "color" in call["system"]
    assert "12000" in call["user"] and "카페 신제품 숏폼" in call["user"]
    assert "mem://refs/cup.png" in call["user"] and "cup" in call["user"]


def test_invalid_json_triggers_self_correction_with_feedback():
    client = ReplayLLMClient(["this is not json", json.dumps(_document())])
    ir = LLMStructurer(client).structure(_request())
    assert ir == cafe_ir()
    assert len(client.calls) == 2
    assert "rejected" in client.calls[1]["user"]


def test_rule_violation_is_fed_back_to_the_model():
    broken = _document()
    broken["entities"].append(dict(broken["entities"][0]))  # entity_key 중복
    client = ReplayLLMClient([json.dumps(broken), json.dumps(_document())])
    LLMStructurer(client).structure(_request())
    assert "entity_key" in client.calls[1]["user"]


def test_an_answer_of_the_wrong_shape_is_fed_back_like_any_other_mistake():
    """strict 가 아닌 서버는 스키마와 형이 다른 값을 줄 수 있다 (글 자리에 숫자). 재시도 고리 안에서 다룬다.

    고리를 빠져나가면 구조화 실패(STRUCTURING_ERROR)가 아니라 예상 밖 오류가 된다 (감사 A-26).
    """
    wrong = _document()
    wrong["scenes"][0]["requirements"][0]["expected_value"] = 3
    client = ReplayLLMClient([json.dumps(wrong), json.dumps(_document())])
    structurer = LLMStructurer(client)

    ir = structurer.structure(_request())

    assert ir == cafe_ir() and structurer.attempts == 2
    assert "rejected" in client.calls[1]["user"]


def test_answers_that_stay_malformed_end_as_a_structuring_error():
    wrong = _document()
    wrong["scenes"][0]["requirements"][0]["expected_value"] = 3
    client = ReplayLLMClient([json.dumps(wrong)] * 2)

    with pytest.raises(StructuringError, match="2회"):
        LLMStructurer(client, max_attempts=2).structure(_request())


def test_gives_up_after_max_attempts():
    client = ReplayLLMClient(["x", "y", "z"])
    with pytest.raises(StructuringError, match="3"):
        LLMStructurer(client, max_attempts=3).structure(_request())
    assert len(client.calls) == 3


def test_light_repair_snaps_timeline_and_links_entities():
    document = _document()
    scenes = document["scenes"]
    scenes[1]["planned_start_ms"], scenes[1]["planned_end_ms"] = 4500, 8500  # 빈 구간 + 밀림
    scenes[2]["planned_start_ms"], scenes[2]["planned_end_ms"] = 8000, 12000  # 겹침
    scenes[1]["entity_keys"] = ["cafe"]  # cup 조건이 있는데 cup 연결 누락
    document["scenes"][0]["requirements"][2]["expected_value"] = ""  # 관계 값 비어 있음

    ir = LLMStructurer(ReplayLLMClient([json.dumps(document)])).structure(_request())

    assert [(s.planned_start_ms, s.planned_end_ms) for s in ir.scenes] == [
        (0, 4000),
        (4000, 8000),
        (8000, 12000),
    ]
    assert "cup" in ir.scene("closeup").entity_keys
    holds = next(r for r in ir.scene("intro").requirements if r.predicate == "holds")
    assert holds.expected_value == "cup"


def test_llm_transport_errors_propagate_with_their_code():
    client = ReplayLLMClient([LLMTimeout("timed out")])
    with pytest.raises(LLMTimeout) as excinfo:
        LLMStructurer(client).structure(_request())
    assert excinfo.value.code == "LLM_TIMEOUT"
    assert len(client.calls) == 1


def test_last_completion_is_kept_for_debugging():
    client = ReplayLLMClient([json.dumps(_document())])
    structurer = LLMStructurer(client)
    structurer.structure(_request())
    assert structurer.last_completion is not None
    assert json.loads(structurer.last_completion.text)["schema_version"] == 1


def test_system_prompt_states_kind_relation_and_action_rules():
    client = ReplayLLMClient([json.dumps(_document())])
    LLMStructurer(client).structure(_request())
    system = client.calls[0]["system"]
    assert "ANIMAL" in system and "BACKGROUND" in system
    assert "action" in system and "location" in system


def test_system_prompt_keeps_changeable_attributes_out_of_entity_keys():
    """2차 실호출: 객체 키가 red_mug 로 나와, 색을 teal 로 바꾼 뒤에도 이름·다른 조건에 red 가 남았다."""
    client = ReplayLLMClient([json.dumps(_document())])
    LLMStructurer(client).structure(_request())
    system = client.calls[0]["system"]
    assert '"mug", not "red_mug"' in system
    assert '"shows mug to camera", not "shows red mug to camera"' in system


def test_repair_notes_are_kept_for_debugging():
    document = _document()
    document["scenes"][0]["requirements"].append(
        {
            "scene_key": "intro",
            "entity_key": "barista",
            "related_entity_key": "cafe",
            "predicate": "near",
            "expected_value": "cafe",
        }
    )
    structurer = LLMStructurer(ReplayLLMClient([json.dumps(document)]))
    ir = structurer.structure(_request())
    assert all(r.related_entity_key != "cafe" for r in ir.iter_requirements())
    assert structurer.last_repair_notes and "cafe" in structurer.last_repair_notes[0]
