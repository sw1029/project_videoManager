"""구조화 골드셋 지표 (US-06 AC-11): 객체 재현율·정밀도, 요구조건 F1, 장면 수·경계 오차.

모델마다 키 이름이 달라지므로(red_mug / cup) 객체는 종류·설명으로, 장면은 시간으로 맞춘 뒤 비교한다.
"""

import pytest

from frame.domain.errors import ValidationError
from frame.domain.ir import IntermediateRepresentation
from frame.evaluation.structuring_metrics import (
    GoldSpec,
    Tally,
    ValueMismatch,
    compare_headlines,
    failed_case,
    headline,
    score_case,
    summarize,
    values_match,
)


def _entity(key, kind="PROP", description="", **extra):
    return {"entity_key": key, "kind": kind, "description": description, **extra}


def _scene(key, start, end, entity_keys, requirements, **extra):
    return {
        "scene_key": key,
        "planned_start_ms": start,
        "planned_end_ms": end,
        "description": "",
        "entity_keys": entity_keys,
        "requirements": requirements,
        **extra,
    }


def _req(predicate, value, entity=None, related=None, **extra):
    return {
        "entity_key": entity,
        "related_entity_key": related,
        "predicate": predicate,
        "expected_value": value,
        **extra,
    }


def _doc(entities, scenes):
    return {"schema_version": 1, "entities": entities, "scenes": scenes}


def _ir(document):
    """모델 출력 역할의 IR. 요구조건의 scene_key 를 채워 IR 로 만든다."""
    for scene in document["scenes"]:
        for req in scene["requirements"]:
            req.setdefault("scene_key", scene["scene_key"])
    ir = IntermediateRepresentation.from_dict(document)
    ir.validate()
    return ir


def _gold_document():
    return _doc(
        [
            _entity("barista", "PERSON", "바리스타", aliases=["바리스타", "barista"]),
            _entity("cup", "PROP", "빨간 머그컵", aliases=["컵", "cup", "mug"]),
        ],
        [
            _scene(
                "intro",
                0,
                4000,
                ["barista", "cup"],
                [
                    _req("holds", "cup", "barista", "cup"),
                    _req("color", "red", "cup"),
                    _req("action", "shows", "barista", accept=["holds up"]),
                    _req("lighting", "warm"),
                ],
            ),
            _scene(
                "closeup",
                4000,
                8000,
                ["cup"],
                [
                    _req("color", "red", "cup"),
                    _req("text", "FRAME", "cup"),
                    _req("camera", "close up"),
                ],
            ),
        ],
    )


def _predicted(barista="barista", cup="cup", cup_kind="PROP"):
    """골드와 내용이 같은 모델 출력. 키·종류를 바꿔 볼 수 있다."""
    return _doc(
        [
            _entity(barista, "PERSON", "카페 바리스타"),
            _entity(cup, cup_kind, "빨간 머그컵"),
        ],
        [
            _scene(
                "scene_1",
                0,
                4000,
                [barista, cup],
                [
                    _req("holds", cup, barista, cup),
                    _req("color", "red", cup),
                    _req("action", "shows cup to camera", barista),
                    _req("lighting", "warm cafe lighting"),
                ],
            ),
            _scene(
                "scene_2",
                4000,
                8000,
                [cup],
                [
                    _req("color", "red", cup),
                    _req("text", "FRAME", cup),
                    _req("camera", "close-up"),
                ],
            ),
        ],
    )


def _score(predicted, gold=None):
    spec = GoldSpec.from_document(gold or _gold_document())
    return score_case("case", spec, _ir(predicted))


# ---- 객체 ------------------------------------------------------------------
def test_identical_output_scores_perfectly():
    score = _score(_gold_document())
    assert score.entities == Tally(tp=2)
    assert score.requirements == Tally(tp=7)
    assert score.appearances == Tally(tp=3)
    assert score.scene_count_match
    assert score.boundary_error_ms == 0.0 and score.duration_error_ms == 0
    assert score.error is None


def test_entities_are_matched_by_kind_and_description_not_by_key():
    score = _score(_predicted(barista="worker", cup="red_mug"))
    assert score.entities == Tally(tp=2)
    assert score.requirements == Tally(tp=7)
    assert score.missed_entities == () and score.spurious_entities == ()


def test_kind_mismatch_is_not_a_match():
    score = _score(_predicted(cup_kind="OTHER"))
    assert score.entities == Tally(tp=1, fp=1, fn=1)
    assert score.missed_entities == ("cup",)
    assert score.spurious_entities == ("cup",)
    # 컵이 걸린 요구조건 4건은 골드 좌표로 옮길 수 없어 모두 놓치고, 모델 쪽 4건은 오탐이 된다
    assert score.requirements == Tally(tp=3, fp=4, fn=4)


def test_optional_entity_is_neither_required_nor_spurious():
    gold = _gold_document()
    gold["entities"].append(_entity("counter", "PROP", "카운터", aliases=["카운터"], optional=True))
    gold["scenes"][0]["entity_keys"].append("counter")
    gold["scenes"][0]["requirements"].append(_req("on", "counter", "cup", "counter"))

    without = _score(_predicted(), gold)
    assert without.entities == Tally(tp=2)
    assert without.requirements == Tally(tp=7)  # 선택 객체의 관계도 선택이다

    predicted = _predicted()
    predicted["entities"].append(_entity("counter", "PROP", "카페 카운터"))
    predicted["scenes"][0]["entity_keys"].append("counter")
    predicted["scenes"][0]["requirements"].append(_req("on", "counter", "cup", "counter"))
    with_counter = _score(predicted, gold)
    assert with_counter.entities == Tally(tp=2)
    assert with_counter.requirements == Tally(tp=7)


def test_spurious_entity_and_its_requirements_are_false_positives():
    predicted = _predicted()
    predicted["entities"].append(_entity("plant", "PROP", "화분"))
    predicted["scenes"][0]["entity_keys"].append("plant")
    predicted["scenes"][0]["requirements"].append(_req("color", "green", "plant"))
    score = _score(predicted)
    assert score.entities == Tally(tp=2, fp=1)
    assert score.spurious_entities == ("plant",)
    assert score.requirements == Tally(tp=7, fp=1)
    assert score.spurious_requirements == ("scene_1:plant:color",)


def test_korean_alias_matches_the_head_of_a_compound_word():
    gold = _gold_document()
    gold["entities"][1]["aliases"] = ["컵"]
    predicted = _predicted(cup="vessel")
    predicted["entities"][1]["description"] = "빨간 머그컵"
    assert _score(predicted, gold).entities == Tally(tp=2)

    predicted["entities"][1]["description"] = "컵받침"  # '컵' 으로 시작할 뿐 컵이 아니다
    assert _score(predicted, gold).entities == Tally(tp=1, fp=1, fn=1)


def test_two_people_are_assigned_by_the_better_description():
    gold = _doc(
        [
            _entity("tall_friend", "PERSON", "키 큰 친구", aliases=["taller", "friend"]),
            _entity("other_friend", "PERSON", "다른 친구", aliases=["other", "friend"]),
        ],
        [
            _scene(
                "s1",
                0,
                4000,
                ["tall_friend", "other_friend"],
                [_req("action", "hands bag", "tall_friend")],
            )
        ],
    )
    predicted = _doc(
        [
            _entity("friend_a", "PERSON", "friend"),
            _entity("friend_b", "PERSON", "taller friend"),
        ],
        [
            _scene(
                "s1",
                0,
                4000,
                ["friend_a", "friend_b"],
                [_req("action", "hands a paper bag", "friend_b")],
            )
        ],
    )
    score = _score(predicted, gold)
    assert score.entities == Tally(tp=2)
    assert score.requirements == Tally(tp=1)


# ---- 요구조건 값 ------------------------------------------------------------
@pytest.mark.parametrize(
    ("predicate", "expected", "actual", "matched"),
    [
        ("expression", "smile", "smiling", True),
        ("expression", "smile", "smiles", True),
        ("lighting", "warm", "warm cafe lighting", True),
        ("lighting", "warm cafe lighting", "warm", False),
        ("outfit", "white shirt and brown apron", "white shirt, brown apron", True),
        ("camera", "close up", "closeup", True),
        ("camera", "closeup", "close-up shot", True),
        ("camera", "close up slow motion", "closeup in slow motion", True),
        ("camera", "bird's eye view", "birds eye view", True),
        ("camera", "close up", "up close", True),  # 내용어만 견주므로 순서는 보지 않는다
        ("weather", "rain", "rainy", True),
        ("weather", "sunny", "sun", True),
        ("action", "applies lipstick", "applying lipstick", True),
        ("action", "runs", "running on treadmill", True),
        ("action", "throws ball", "catches ball", False),
        ("camera", "slow orbit", "slowly orbits the laptop", True),
        ("color", "red", "빨간색", True),
        ("color", "gray", "grey", True),
        ("position", "bottom right", "lower right corner", True),
        ("text", "FRAME", "'FRAME'", True),
        ("text", "FRAME", "frame", False),
        ("brand", "FIT ZONE", " FIT  ZONE ", True),
        ("count", "3", "three", True),
        ("count", "2", "두 개", True),
        ("count", "3", "2", False),
        ("holds", "cup", "mug", True),  # 관계는 상대 객체를 맞춘 것으로 비교가 끝난다
    ],
)
def test_values_are_compared_after_normalization(predicate, expected, actual, matched):
    assert values_match(predicate, expected, actual) is matched


def test_accepted_values_and_predicates():
    gold = _gold_document()
    gold["scenes"][0]["requirements"].append(
        _req("pose", "facing camera", "barista", accept_predicates=["expression"])
    )
    predicted = _predicted()
    predicted["scenes"][0]["requirements"][2]["expected_value"] = "holds up the mug"
    predicted["scenes"][0]["requirements"].append(
        _req("expression", "facing the camera", "barista")
    )
    score = _score(predicted, gold)
    assert score.requirements == Tally(tp=8)


def test_wrong_value_is_one_false_positive_and_one_false_negative():
    predicted = _predicted()
    predicted["scenes"][1]["requirements"][0]["expected_value"] = "blue"
    score = _score(predicted)
    assert score.requirements == Tally(tp=6, fp=1, fn=1)
    assert score.mismatched_requirements == (ValueMismatch("closeup:cup:color", "red", "blue"),)
    assert str(score.mismatched_requirements[0]) == "closeup:cup:color: 기대 'red', 결과 'blue'"
    assert score.missed_requirements == ()


def test_unstated_condition_lowers_precision_only():
    predicted = _predicted()
    predicted["scenes"][0]["requirements"].append(_req("mood", "cozy"))
    score = _score(predicted)
    assert score.requirements == Tally(tp=7, fp=1)
    assert score.spurious_requirements == ("scene_1:-:mood",)


def test_optional_requirement_with_a_wrong_value_is_still_a_false_positive():
    gold = _gold_document()
    gold["scenes"][1]["requirements"].append(_req("lighting", "warm", optional=True))
    predicted = _predicted()
    predicted["scenes"][1]["requirements"].append(_req("lighting", "cold blue"))
    assert _score(predicted, gold).requirements == Tally(tp=7, fp=1)

    predicted["scenes"][1]["requirements"][-1]["expected_value"] = "warm"
    assert _score(predicted, gold).requirements == Tally(tp=7)


def test_prop_used_as_location_is_penalized():
    """실호출에서 본 `location = wooden table`: 소품은 객체이고, 놓인 자리는 관계로 적는다."""
    gold = _doc(
        [
            _entity("cup", "PROP", "유리컵", aliases=["컵", "cup", "glass"]),
            _entity("table", "PROP", "나무 테이블", aliases=["테이블", "table"]),
        ],
        [
            _scene(
                "s1",
                0,
                4000,
                ["cup", "table"],
                [
                    _req("on", "table", "cup", "table"),
                    _req("material", "wood", "table", accept=["wooden"]),
                    _req("location", "living room"),
                ],
            )
        ],
    )
    predicted = _doc(
        [_entity("cup", "PROP", "유리컵")],
        [_scene("s1", 0, 4000, ["cup"], [_req("location", "wooden table")])],
    )
    score = _score(predicted, gold)
    assert score.entities == Tally(tp=1, fn=1)
    assert score.requirements == Tally(fp=1, fn=3)
    assert score.missed_requirements == ("s1:cup:on:table", "s1:table:material")


# ---- 장면 ------------------------------------------------------------------
def test_split_scene_repeats_collapse_into_one_condition():
    predicted = _predicted()
    first = predicted["scenes"][1]
    first["planned_end_ms"] = 6000
    second = _scene(
        "scene_3",
        6000,
        8000,
        ["cup"],
        [_req("color", "red", "cup"), _req("camera", "close up")],
    )
    predicted["scenes"].append(second)
    score = _score(predicted)
    assert score.requirements == Tally(tp=7)
    assert (score.expected_scene_count, score.predicted_scene_count) == (2, 3)
    assert not score.scene_count_match
    assert score.boundary_error_ms is None


def test_merged_scenes_lose_the_conditions_of_the_unmatched_scene():
    predicted = _doc(
        [_entity("barista", "PERSON", "바리스타"), _entity("cup", "PROP", "머그컵")],
        [
            _scene(
                "all",
                0,
                8000,
                ["barista", "cup"],
                [
                    _req("holds", "cup", "barista", "cup"),
                    _req("color", "red", "cup"),
                    _req("action", "shows cup", "barista"),
                    _req("lighting", "warm"),
                    _req("text", "FRAME", "cup"),
                    _req("camera", "close up"),
                ],
            )
        ],
    )
    score = _score(predicted)
    assert score.requirements == Tally(tp=4, fp=2, fn=3)
    assert score.missed_requirements == (
        "closeup:cup:color",
        "closeup:cup:text",
        "closeup:-:camera",
    )
    assert not score.scene_count_match


def test_boundary_and_duration_errors():
    shifted = _predicted()
    shifted["scenes"][0]["planned_end_ms"] = 3000
    shifted["scenes"][1]["planned_start_ms"] = 3000
    score = _score(shifted)
    assert score.boundary_error_ms == 1000.0 and score.duration_error_ms == 0

    longer = _predicted()
    longer["scenes"][0]["planned_end_ms"] = 4500
    longer["scenes"][1]["planned_start_ms"] = 4500
    longer["scenes"][1]["planned_end_ms"] = 9000
    score = _score(longer)
    assert score.boundary_error_ms == 500.0 and score.duration_error_ms == 1000
    assert score.requirements == Tally(tp=7)  # 길이가 달라도 비율로 장면을 맞춘다


def test_entity_appearances_count_scene_membership():
    predicted = _predicted()
    predicted["scenes"][1]["entity_keys"].append("barista")
    score = _score(predicted)
    assert score.appearances == Tally(tp=3, fp=1)
    assert score.requirements == Tally(tp=7)


def test_optional_entities_of_a_scene():
    gold = _gold_document()
    gold["scenes"][1]["entity_keys"].append("barista")
    gold["scenes"][1]["optional_entities"] = ["barista"]
    gold["scenes"][1]["requirements"].append(_req("action", "waves", "barista"))
    score = _score(_predicted(), gold)
    assert score.appearances == Tally(tp=3)
    assert score.requirements == Tally(tp=7)  # 그 장면에서 선택인 객체의 조건도 선택이다


def test_failed_case_misses_every_required_item():
    spec = GoldSpec.from_document(_gold_document())
    score = failed_case("case", spec, "LLM_TIMEOUT: 시간 초과")
    assert score.entities == Tally(fn=2)
    assert score.requirements == Tally(fn=7)
    assert score.predicted_scene_count == 0 and not score.scene_count_match
    assert score.boundary_error_ms is None and score.duration_error_ms is None
    assert score.error == "LLM_TIMEOUT: 시간 초과"


def test_gold_must_be_a_valid_ir():
    gold = _gold_document()
    gold["scenes"][1]["planned_start_ms"] = 4500  # 빈 구간
    with pytest.raises(ValidationError):
        GoldSpec.from_document(gold)

    gold = _gold_document()
    gold["scenes"][1]["optional_entities"] = ["barista"]  # 장면에 없는 객체
    with pytest.raises(ValidationError):
        GoldSpec.from_document(gold)


# ---- 집계·비교 ---------------------------------------------------------------
def test_tally_ratios():
    tally = Tally(tp=8, fp=2, fn=0)
    assert (tally.precision, tally.recall) == (0.8, 1.0)
    assert tally.f1 == pytest.approx(2 * 0.8 / 1.8)
    assert Tally().precision == 0.0 and Tally().f1 == 0.0


def test_summarize_pools_counts_and_checks_targets():
    spec = GoldSpec.from_document(_gold_document())
    perfect = score_case("a", spec, _ir(_gold_document()))
    predicted = _predicted()
    predicted["scenes"][1]["requirements"].pop()  # camera 를 빠뜨림
    partial = score_case("b", spec, _ir(predicted))
    failed = failed_case("c", spec, "LLM_UNAVAILABLE")

    evaluation = summarize([perfect, partial])
    assert evaluation.cases == 2 and evaluation.failures == 0
    assert evaluation.requirements == Tally(tp=13, fn=1)
    assert evaluation.requirement_recall == pytest.approx(13 / 14)
    assert evaluation.scene_count_match_rate == 1.0
    assert evaluation.mean_boundary_error_ms == 0.0
    assert evaluation.meets_target

    with_failure = summarize([perfect, partial, failed])
    assert with_failure.failures == 1
    assert with_failure.entity_recall == pytest.approx(4 / 6)
    assert with_failure.scene_count_match_rate == pytest.approx(2 / 3)
    assert not with_failure.meets_target


def test_summarize_without_cases_does_not_meet_target():
    evaluation = summarize([])
    assert evaluation.cases == 0 and not evaluation.meets_target
    assert evaluation.mean_boundary_error_ms is None


def test_headline_and_comparison_mark_regressions():
    spec = GoldSpec.from_document(_gold_document())
    baseline = headline(summarize([score_case("a", spec, _ir(_gold_document()))]))
    predicted = _predicted()
    predicted["scenes"][1]["requirements"].pop()
    predicted["scenes"][0]["planned_end_ms"] = 3000
    predicted["scenes"][1]["planned_start_ms"] = 3000
    candidate = headline(summarize([score_case("a", spec, _ir(predicted))]))

    assert list(baseline)[:3] == ["entity_recall", "entity_precision", "requirement_precision"]
    changes = {c.name: c for c in compare_headlines(baseline, candidate)}
    assert changes["requirement_f1"].regressed
    assert changes["requirement_f1"].delta == pytest.approx(candidate["requirement_f1"] - 1.0)
    assert not changes["entity_recall"].regressed
    assert changes["mean_boundary_error_ms"].regressed  # 낮을수록 좋은 지표가 늘었다
    reverse = {c.name: c for c in compare_headlines(candidate, baseline)}
    assert not reverse["requirement_f1"].regressed
    assert not reverse["mean_boundary_error_ms"].regressed
