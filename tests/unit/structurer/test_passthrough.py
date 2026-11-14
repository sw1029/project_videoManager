"""PassthroughStructurer: 화면이 조립한 IR 문서를 LLM 없이 보정(light_repair) → 검증만 해서 IR 로 만든다 (#10 #11)."""

import copy

import pytest

from frame.adapters.structurer.passthrough import PassthroughStructurer
from frame.application.ports.structurer import IrDocumentStructurer
from frame.domain.enums import EntityKind
from frame.domain.errors import ValidationError
from tests.support.factories import cafe_ir


def _document() -> dict:
    return cafe_ir().to_dict()


def test_it_is_the_ir_document_port():
    assert isinstance(PassthroughStructurer(), IrDocumentStructurer)


def test_valid_document_becomes_the_same_ir_without_notes():
    result = PassthroughStructurer().structure_document(_document())

    assert result.ir == cafe_ir()
    assert result.repair_notes == ()


def test_fixable_problems_are_repaired_and_reported():
    document = _document()
    document["entities"][0]["kind"] = "person"  # 별칭
    document["scenes"][1]["planned_start_ms"] = 4500  # 앞 장면과 사이가 빈다
    document["scenes"][1]["planned_end_ms"] = 8500
    # outro 의 요구조건이 가리키는 barista 가 장면에 연결되지 않았다
    document["scenes"][2]["entity_keys"] = ["cafe"]
    # 블록 입력은 요구조건의 장면 키를 생략할 수 있다
    del document["scenes"][0]["requirements"][0]["scene_key"]

    result = PassthroughStructurer().structure_document(document)

    ir = result.ir
    assert ir.entity("barista").kind is EntityKind.PERSON
    assert (ir.scene("closeup").planned_start_ms, ir.scene("closeup").planned_end_ms) == (
        4000,
        8000,
    )
    assert "barista" in ir.scene("outro").entity_keys
    assert ir.scene("intro").requirements[0].scene_key == "intro"
    # 별칭과 생략한 장면 키는 메모 없이 고치고, 구간 이동·객체 연결은 메모로 알린다
    assert len(result.repair_notes) == 2
    assert any("closeup" in note for note in result.repair_notes)
    assert any("barista" in note and "outro" in note for note in result.repair_notes)


def test_input_document_is_not_modified():
    document = _document()
    document["scenes"][1]["planned_start_ms"] = 4500
    before = copy.deepcopy(document)

    PassthroughStructurer().structure_document(document)

    assert document == before


def test_same_document_gives_the_same_result():
    document = _document()
    document["entities"][1]["kind"] = "object"
    first = PassthroughStructurer().structure_document(document)
    second = PassthroughStructurer().structure_document(document)
    assert first == second


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d["scenes"].clear(), "장면이 하나 이상"),
        (lambda d: d["scenes"][1].update(scene_key="intro"), "scene_key"),
        (lambda d: d["entities"].append(dict(d["entities"][0])), "entity_key"),
        (lambda d: d["scenes"][0]["requirements"][0].update(expected_value=""), "값이 비어"),
        (lambda d: d["entities"][0].pop("entity_key"), "entity_key"),
        (lambda d: d.update(schema_version=99), "schema_version"),
    ],
    ids=[
        "no-scenes",
        "duplicate-scene",
        "duplicate-entity",
        "empty-value",
        "missing-key",
        "schema-version",
    ],
)
def test_unfixable_documents_are_rejected_with_the_reason(mutate, message):
    document = _document()
    mutate(document)

    with pytest.raises(ValidationError) as excinfo:
        PassthroughStructurer().structure_document(document)

    assert message in excinfo.value.message


def test_non_object_document_is_rejected():
    with pytest.raises(ValidationError):
        PassthroughStructurer().structure_document(["scenes"])  # type: ignore[arg-type]
