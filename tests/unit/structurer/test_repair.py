"""light_repair: 객체 종류 별칭, 배경을 상대로 한 관계 제거, 미정의 객체 관계 제거, 보정 메모."""

from frame.adapters.structurer.repair import light_repair, light_repair_with_notes


def _doc():
    return {
        "schema_version": 1,
        "entities": [
            {"entity_key": "dog", "kind": "animal", "description": "강아지"},
            {"entity_key": "owner", "kind": "PERSON", "description": "주인"},
            {"entity_key": "park", "kind": "place", "description": "공원"},
            {"entity_key": "ball", "kind": "item", "description": "공"},
        ],
        "scenes": [
            {
                "scene_key": "play",
                "planned_start_ms": 0,
                "planned_end_ms": 5000,
                "description": "강아지가 공원에서 논다",
                "entity_keys": ["dog", "owner", "park", "ball"],
                "requirements": [
                    {
                        "scene_key": "play",
                        "entity_key": "dog",
                        "related_entity_key": "park",
                        "predicate": "near",
                        "expected_value": "park",
                    },
                    {
                        "scene_key": "play",
                        "entity_key": "park",
                        "related_entity_key": "dog",
                        "predicate": "near",
                        "expected_value": "dog",
                    },
                    {
                        "scene_key": "play",
                        "entity_key": "owner",
                        "related_entity_key": "frisbee",
                        "predicate": "holds",
                        "expected_value": "frisbee",
                    },
                    {
                        "scene_key": "play",
                        "entity_key": "owner",
                        "related_entity_key": "ball",
                        "predicate": "holds",
                        "expected_value": "ball",
                    },
                    {
                        "scene_key": "play",
                        "entity_key": None,
                        "related_entity_key": None,
                        "predicate": "mood",
                        "expected_value": "bright",
                    },
                ],
            }
        ],
    }


def test_kind_aliases_are_mapped_to_entity_kinds():
    repaired = light_repair(_doc())
    kinds = {e["entity_key"]: e["kind"] for e in repaired["entities"]}
    assert kinds == {"dog": "ANIMAL", "owner": "PERSON", "park": "BACKGROUND", "ball": "PROP"}


def test_relations_involving_backgrounds_or_unknown_entities_are_dropped_with_notes():
    repaired, notes = light_repair_with_notes(_doc())
    remaining = [
        (r["entity_key"], r["predicate"], r["related_entity_key"])
        for r in repaired["scenes"][0]["requirements"]
    ]
    assert remaining == [("owner", "holds", "ball"), (None, "mood", None)]
    assert len(notes) == 3
    assert any("park" in note for note in notes) and any("frisbee" in note for note in notes)


def test_repair_is_pure_and_keeps_valid_documents_unchanged():
    from tests.support.factories import cafe_ir

    document = cafe_ir().to_dict()
    before = repr(document)
    repaired, notes = light_repair_with_notes(document)
    assert repaired == document and notes == [] and repr(document) == before
