"""구조화 골드셋 (US-06 AC-11, T2·T6): 골드 자체가 구조화 프롬프트의 규칙을 지키는지 검사한다."""

import re
from collections import Counter
from pathlib import Path

import pytest

from frame.adapters.structurer.llm_prompt import MIN_SCENE_MS, max_scenes_for
from frame.domain.enums import EntityKind
from frame.domain.vocabulary import DEFAULT_VOCABULARY, ValueKind
from frame.evaluation.structuring_gold import load_gold_cases

GOLD_DIR = Path(__file__).parent / "structuring_gold"
_SNAKE = re.compile(r"^[a-z][a-z0-9_]*$")


@pytest.fixture(scope="module")
def cases():
    return load_gold_cases(GOLD_DIR)


def _case(cases, case_id):
    return next(c for c in cases if c.case_id == case_id)


# ---- 골드 자체 ----------------------------------------------------------------
def test_gold_set_has_ten_korean_and_five_english_briefs(cases):
    assert len(cases) == 15
    assert Counter(c.language for c in cases) == {"ko": 10, "en": 5}
    assert len({c.case_id for c in cases}) == 15
    assert sorted(p.stem for p in GOLD_DIR.glob("*.json")) == sorted(c.case_id for c in cases)


def test_gold_irs_follow_the_structuring_prompt_rules(cases):
    for case in cases:
        ir = case.spec.ir  # 불러올 때 IR.validate() 를 통과했다
        assert ir.total_duration_ms == case.target_duration_ms, case.case_id
        assert len(ir.scenes) <= max_scenes_for(case.request()), case.case_id
        assert all(s.duration_ms >= MIN_SCENE_MS for s in ir.scenes), case.case_id
        keys = [e.entity_key for e in ir.entities] + [s.scene_key for s in ir.scenes]
        assert all(_SNAKE.match(k) for k in keys), case.case_id
        appearing = {k for s in ir.scenes for k in s.entity_keys}
        assert appearing == {e.entity_key for e in ir.entities}, case.case_id
        for entity in case.document["entities"]:
            assert entity.get("aliases"), f"{case.case_id}:{entity['entity_key']} 별칭이 없다"


def test_gold_set_covers_relations_text_actions_and_scene_level_conditions(cases):
    def count(condition):
        return sum(1 for c in cases if any(condition(r.spec) for r in c.spec.requirements))

    assert count(lambda r: r.is_relational) >= 10
    assert count(lambda r: DEFAULT_VOCABULARY.value_kind(r.predicate) is ValueKind.LITERAL) >= 8
    assert count(lambda r: r.predicate == "action") >= 12
    assert count(lambda r: r.entity_key is None) == 15
    kinds = {e.kind for c in cases for e in c.spec.ir.entities}
    assert {EntityKind.PERSON, EntityKind.ANIMAL, EntityKind.PROP, EntityKind.TEXT} <= kinds


def test_concept_screen_cafe_brief_is_in_the_gold_set(cases):
    """컨셉 화면의 예시 데이터: 16초·4장면, 컵은 장면 2·3 에만 나온다."""
    case = _case(cases, "ko-01-cafe-concept")
    ir = case.spec.ir
    assert case.target_duration_ms == 16000 and len(ir.scenes) == 4
    assert [i for i, s in enumerate(ir.scenes, 1) if "cup" in s.entity_keys] == [2, 3]
    colors = {
        (r.scene_key, r.expected_value) for r in ir.iter_requirements() if r.predicate == "color"
    }
    assert colors == {("show_cup", "red"), ("cup_closeup", "red")}
