"""RequirementStructurer 계약 테스트: 어떤 구현(규칙 기반/Fake/LLM)이든 같은 스위트를 통과해야 한다.

실제 LLM 어댑터를 붙이면 아래 STRUCTURERS 에 (pytest.mark.slow 와 함께) 추가한다.
"""

import pytest

from frame.adapters.structurer import FakeStructurer, RuleBasedStructurer
from frame.application.errors import StructuringError
from frame.application.ports.structurer import StructuringRequest
from frame.domain.enums import EntityKind
from tests.support.factories import CAFE_PROMPT

STRUCTURERS = [
    pytest.param(RuleBasedStructurer, id="rule_based"),
    pytest.param(FakeStructurer, id="fake"),
]


@pytest.fixture(params=STRUCTURERS)
def structurer(request):
    return request.param()


def test_returns_valid_ir_for_a_well_formed_brief(structurer):
    ir = structurer.structure(StructuringRequest(CAFE_PROMPT))
    ir.validate()
    assert [s.scene_key for s in ir.scenes] == ["intro", "closeup", "outro"]
    assert ir.entity("barista").kind is EntityKind.PERSON
    assert ir.scene("closeup").entity_keys == ("cup", "cafe")
    keys = {r.key for r in ir.iter_requirements()}
    assert {
        "intro:cup:color",
        "closeup:cup:color",
        "intro:barista:holds:cup",
        "outro:cafe:lighting",
    } <= keys
    assert "outro:cup:color" not in keys  # 컵은 outro 에 없다


def test_relation_requirement_links_related_entity(structurer):
    ir = structurer.structure(StructuringRequest(CAFE_PROMPT))
    holds = next(r for r in ir.scene("intro").requirements if r.predicate == "holds")
    assert (holds.entity_key, holds.related_entity_key, holds.expected_value) == (
        "barista",
        "cup",
        "cup",
    )


def test_same_input_gives_same_ir(structurer):
    a = structurer.structure(StructuringRequest(CAFE_PROMPT))
    b = structurer.structure(StructuringRequest(CAFE_PROMPT))
    assert a.fingerprint() == b.fingerprint()


def test_empty_brief_raises_structuring_error(structurer):
    with pytest.raises(StructuringError):
        structurer.structure(StructuringRequest("   "))


def test_missing_scene_defaults_to_single_scene(structurer):
    ir = structurer.structure(
        StructuringRequest("entity cup PROP\ncup.color = red", target_duration_ms=5000)
    )
    assert len(ir.scenes) == 1
    assert (ir.scenes[0].planned_start_ms, ir.scenes[0].planned_end_ms) == (0, 5000)
    assert ir.scenes[0].entity_keys == ("cup",)


def test_scene_scoped_requirement_links_entity_to_scene(structurer):
    ir = structurer.structure(
        StructuringRequest(
            "scene a 0-1000\nscene b 1000-2000\nentity cup PROP\ncup.color = red @ b"
        )
    )
    assert ir.scene("b").entity_keys == ("cup",)
    assert ir.scene("a").entity_keys == ()


@pytest.mark.parametrize(
    "prompt",
    [
        "entity cup PROP\ncup.color = red\nscene a 0-1000",  # cup 이 어떤 장면에도 없음
        "scene a 0-1000\nentity cup PROP\nin a: ghost",  # 정의되지 않은 객체
        "scene a 1000-500\nentity cup PROP\nin a: cup",  # 시간 역전
        "자연어 문장은 규칙 기반에서 해석할 수 없다",
    ],
)
def test_malformed_brief_raises_structuring_error(structurer, prompt):
    with pytest.raises(StructuringError):
        structurer.structure(StructuringRequest(prompt))


def test_fake_can_inject_canned_ir_and_records_calls():
    from tests.support.factories import cafe_ir

    fake = FakeStructurer(ir=cafe_ir())
    request = StructuringRequest("아무 말이나")
    assert fake.structure(request) == cafe_ir()
    assert fake.calls == [request]


def test_scene_level_condition_and_predicate_aliases(structurer):
    ir = structurer.structure(
        StructuringRequest(
            "scene a 0-1000\nentity cup PROP\nin a: cup\ncup.색상 = 빨강\nscene.mood = calm @ a"
        )
    )
    keys = {r.key: r for r in ir.iter_requirements()}
    assert keys["a:cup:color"].expected_value == "빨강"
    assert keys["a:-:mood"].entity_key is None


def test_relation_lines_accept_aliases_and_produce_relation_keys(structurer):
    ir = structurer.structure(
        StructuringRequest("scene a 0-1000\nentity p PERSON\nentity cup PROP\np 들다 -> cup @ a")
    )
    rel = next(iter(ir.iter_requirements()))
    assert (rel.predicate, rel.related_entity_key, rel.key) == ("holds", "cup", "a:p:holds:cup")


def test_non_contiguous_scenes_are_rejected(structurer):
    with pytest.raises(StructuringError, match="비어"):
        structurer.structure(
            StructuringRequest(
                "scene a 0-1000\nscene b 2000-3000\nentity cup PROP\nin a: cup\nin b: cup"
            )
        )
