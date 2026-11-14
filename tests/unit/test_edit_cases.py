"""지표 1 평가 케이스 파일 읽기: 비용이 드는 실행 전에 형식 오류를 모두 걸러 낸다."""

import json

import pytest

from frame.domain.enums import ChangeScope, KeepAspect, RevisionKind
from frame.domain.revision import KeepItem
from frame.evaluation.edit_cases import CaseFileError, EditCaseSpec, load_cases, parse_cases


def _case(**fields):
    base = {
        "parent_version_id": "ver_1",
        "entity_key": "cup",
        "predicate": "color",
        "new_value": "teal",
    }
    base.update(fields)
    return base


def test_minimal_case_gets_defaults():
    (spec,) = parse_cases({"cases": [_case()]})
    assert spec.case_id == "case_01"
    assert spec.parent_version_id == "ver_1"
    assert spec.change.entity_key == "cup"
    assert spec.change.predicate == "color"
    assert spec.change.new_value == "teal"
    assert spec.change.scope is ChangeScope.ALL_APPEARANCES
    assert spec.kind is None  # 계획이 정한다 (값이 같으면 REPAIR)
    assert spec.keep == ()


def test_full_case_is_parsed():
    (spec,) = parse_cases(
        {
            "cases": [
                _case(
                    case_id="cup-repair",
                    kind="REPAIR",
                    scope="SCENES",
                    scene_keys=["intro"],
                    keep=[
                        {"aspect": "APPEARANCE", "entity_key": "barista"},
                        {"aspect": "BACKGROUND"},
                    ],
                    current_value="red",
                    note="컵 색만 다시 맞춘다",
                )
            ]
        }
    )
    assert spec.case_id == "cup-repair"
    assert spec.kind is RevisionKind.REPAIR
    assert spec.change.scope is ChangeScope.SCENES
    assert spec.change.scene_keys == ("intro",)
    assert spec.change.current_value == "red"
    assert spec.keep == (
        KeepItem(KeepAspect.APPEARANCE, "barista"),
        KeepItem(KeepAspect.BACKGROUND),
    )
    assert spec.note == "컵 색만 다시 맞춘다"


def test_scene_level_condition_has_no_entity():
    (spec,) = parse_cases([_case(entity_key=None, predicate="lighting", new_value="cool")])
    assert spec.change.entity_key is None


def test_predicate_alias_is_normalized_like_the_api():
    (spec,) = parse_cases([_case(predicate="색상")])
    assert spec.change.predicate == "color"


def test_top_level_list_is_accepted_and_ids_follow_position():
    specs = parse_cases([_case(), _case(case_id="b"), _case()])
    assert [s.case_id for s in specs] == ["case_01", "b", "case_03"]


def test_to_dict_round_trips():
    (spec,) = parse_cases(
        [_case(case_id="x", kind="CHANGE", keep=[{"aspect": "LIGHTING"}], note="n")]
    )
    (again,) = parse_cases([spec.to_dict()])
    assert again == spec


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ("cases", "목록"),
        ({"items": []}, "cases"),
        ({"cases": []}, "비어"),
        ([_case(parent_version_id=None)], "parent_version_id"),
        ([{"parent_version_id": "v", "predicate": "color"}], "new_value"),
        ([_case(new_value=3)], "new_value"),
        ([_case(newvalue="teal")], "newvalue"),
        ([_case(kind="FIX")], "kind"),
        ([_case(scope="ALL")], "scope"),
        ([_case(scene_keys="intro")], "scene_keys"),
        ([_case(keep=[{"aspect": "SMELL"}])], "keep"),
        ([_case(keep=[{"aspect": "APPEARANCE"}])], "keep"),
        ([_case(keep=[{"aspect": "BACKGROUND", "entity": "cafe"}])], "keep"),
        ([_case(new_value="   ")], "new_value"),
        ([_case(case_id="a"), _case(case_id="a")], "a"),
        ([["not", "an", "object"]], "객체"),
    ],
)
def test_malformed_cases_are_rejected_with_the_case_named(data, message):
    with pytest.raises(CaseFileError) as info:
        parse_cases(data)
    assert message in str(info.value)


def test_error_names_the_case_position():
    with pytest.raises(CaseFileError) as info:
        parse_cases([_case(), _case(kind="FIX")])
    assert "2번째" in str(info.value)


def test_load_cases_reads_utf8_json(tmp_path):
    path = tmp_path / "cases.json"
    path.write_text(json.dumps({"cases": [_case(note="한글 메모")]}, ensure_ascii=False), "utf-8")
    (spec,) = load_cases(path)
    assert isinstance(spec, EditCaseSpec) and spec.note == "한글 메모"


def test_load_cases_reports_invalid_json(tmp_path):
    path = tmp_path / "cases.json"
    path.write_text("{not json", "utf-8")
    with pytest.raises(CaseFileError) as info:
        load_cases(path)
    assert "JSON" in str(info.value)


def test_load_cases_reports_missing_file(tmp_path):
    with pytest.raises(CaseFileError):
        load_cases(tmp_path / "missing.json")
