"""구조화 골드셋 (US-06 AC-11, T2·T6): 골드 자체의 규칙과, 리플레이로 지표 계산을 검증한다 (모델 호출 없음).

실제 모델로 돌리는 것은 tests/live/test_structuring_gold_live.py (slow, FRAME_EVAL_STRUCTURING_LIVE=1) 다.
"""

import importlib
import json
import logging
import re
from collections import Counter
from pathlib import Path

import pytest

from frame.adapters.llm import ReplayLLMClient
from frame.adapters.structurer import FakeStructurer, LLMStructurer
from frame.adapters.structurer.llm_prompt import MIN_SCENE_MS, max_scenes_for
from frame.application.errors import LLMUnavailable
from frame.domain.enums import EntityKind
from frame.domain.vocabulary import DEFAULT_VOCABULARY, ValueKind
from frame.evaluation.structuring import (
    GoldCase,
    StructuringRunDetails,
    Tally,
    build_record,
    compare_records,
    format_comparison,
    format_summary,
    latest_record,
    load_gold_cases,
    read_record,
    recorded_responses,
    rescore_record,
    run_gold_set,
    summarize,
    write_record,
)

GOLD_DIR = Path(__file__).parent / "structuring_gold"
REPLAY_DIR = Path(__file__).parent / "replay"
_SNAKE = re.compile(r"^[a-z][a-z0-9_]*$")
_STRUCTURER = {"name": "LLMStructurer", "model": "replay", "prompt_version": "2026-11-12.1"}


@pytest.fixture(scope="module")
def cases():
    return load_gold_cases(GOLD_DIR)


def _case(cases, case_id):
    return next(c for c in cases if c.case_id == case_id)


def _replay_runs(cases, responses):
    """사례마다 응답 하나를 재생한다. 재시도가 다음 사례의 응답을 먹지 않도록 구조화기를 따로 만든다."""
    runs = []
    for case in cases:
        client = ReplayLLMClient([responses[case.case_id]], model="replay-model")
        runs.extend(run_gold_set(LLMStructurer(client, max_attempts=1), [case]))
    return runs


def _perfect_responses(cases):
    return {c.case_id: json.dumps(c.document, ensure_ascii=False) for c in cases}


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


# ---- 리플레이로 지표 계산 검증 ---------------------------------------------------------
def test_replaying_the_gold_itself_scores_perfectly(cases):
    runs = _replay_runs(cases, _perfect_responses(cases))
    evaluation = summarize(r.score for r in runs)
    assert evaluation.cases == 15 and evaluation.failures == 0
    assert evaluation.entity_recall == evaluation.entity_precision == 1.0
    assert evaluation.requirement_precision == evaluation.requirement_recall == 1.0
    assert evaluation.appearances.f1 == 1.0
    assert evaluation.scene_count_match_rate == 1.0
    assert evaluation.mean_boundary_error_ms == 0.0 and evaluation.mean_duration_error_ms == 0.0
    assert evaluation.meets_target
    assert all(r.attempts == 1 and r.model == "replay-model" and r.response for r in runs)


def test_recorded_gpt_outputs_for_the_cafe_brief_show_what_each_prompt_missed(cases):
    """gpt-4.1-mini 실호출 결과(concept-ux 2차 e2e)를 다시 채점한다.

    키는 red_mug / cup 으로 달라도 같은 객체로 맞추고, 두 프롬프트가 서로 다른 조건을 하나씩 놓친 것이 드러난다.
    """
    fixture = json.loads(
        (REPLAY_DIR / "ko-01-cafe-concept.gpt-4.1-mini.json").read_text(encoding="utf-8")
    )
    case = _case(cases, "ko-01-cafe-concept")
    scores = {}
    for item in fixture["responses"]:
        response = json.dumps(item["ir"], ensure_ascii=False)
        scores[(item["run"], item["prompt_version"])] = _replay_runs(
            [case], {case.case_id: response}
        )[0].score

    for score in scores.values():
        assert score.entities == Tally(tp=2)
        assert score.requirements == Tally(tp=17, fn=1)
        assert score.appearances == Tally(tp=5)
        assert score.scene_count_match and score.boundary_error_ms == 0.0
    # 옛 프롬프트: 컵 색을 클로즈업에만 적고 들어 보이는 장면에서 뺐다
    assert scores[("c3", "2026-11-07.2")].missed_requirements == ("show_cup:cup:color",)
    # 고친 프롬프트: 컵 색은 두 장면에 다 적었지만 '카메라를 보며' 를 뺐다 (두 번 모두)
    for run in ("c4", "c5"):
        assert scores[(run, "2026-11-12.1")].missed_requirements == ("smile:barista:action",)


def test_failed_structuring_is_recorded_as_a_failed_case(cases):
    case = _case(cases, "ko-05-ramen-recipe")
    runs = _replay_runs([case], {case.case_id: "not json"})
    score = runs[0].score
    assert score.error is not None and "STRUCTURING_ERROR" in score.error
    assert score.requirements == Tally(fn=6)
    assert runs[0].response == "not json"


# ---- 결과 기록과 버전 비교 --------------------------------------------------------------
def test_record_round_trip_replay_and_comparison(cases, tmp_path):
    base = build_record(
        _replay_runs(cases, _perfect_responses(cases)),
        structurer=_STRUCTURER,
        run_at="2026-11-14T10:00:00+09:00",
    )
    assert base["record_version"] == 1
    assert base["structurer"]["prompt_version"] == "2026-11-12.1"
    assert base["structurer"]["reported_models"] == ["replay-model"]
    assert base["summary"]["requirement_f1"] == 1.0 and base["summary"]["meets_target"]
    assert base["by_language"]["en"]["cases"] == 5 and base["by_language"]["ko"]["cases"] == 10
    assert len(base["cases"]) == 15 and base["cases"][0]["response"]

    path = write_record(base, tmp_path)
    assert path.name == "2026-11-12.1__replay__20261114T100000.json"
    loaded = read_record(path)
    assert loaded == base

    # 기록에 남은 원본 응답만으로 다시 채점하면 같은 결과가 나온다 (모델을 다시 부르지 않는다)
    again = _replay_runs(cases, recorded_responses(loaded))
    rebuilt = build_record(again, structurer=_STRUCTURER, run_at=loaded["run_at"])
    assert rebuilt["summary"] == loaded["summary"]

    # 프롬프트를 바꾼 뒤 한 사례가 나빠진 기록과 견주면 회귀가 수치와 사례 목록으로 드러난다
    responses = _perfect_responses(cases)
    worse_doc = json.loads(responses["ko-01-cafe-concept"])
    show_cup = worse_doc["scenes"][1]
    show_cup["requirements"] = [r for r in show_cup["requirements"] if r["predicate"] != "color"]
    responses["ko-01-cafe-concept"] = json.dumps(worse_doc, ensure_ascii=False)
    worse = build_record(
        _replay_runs(cases, responses),
        structurer={**_STRUCTURER, "prompt_version": "2026-11-15.1"},
        run_at="2026-11-15T09:30:00+09:00",
    )
    newer = write_record(worse, tmp_path)
    assert latest_record(tmp_path)["run_at"] == worse["run_at"]
    assert latest_record(tmp_path, exclude=newer)["run_at"] == base["run_at"]

    comparison = compare_records(base, worse)
    assert comparison.regressed
    changes = {c.name: c for c in comparison.metrics}
    assert changes["requirement_recall"].regressed and not changes["entity_recall"].regressed
    assert [c.case_id for c in comparison.cases] == ["ko-01-cafe-concept"]
    assert comparison.cases[0].newly_missed == ("show_cup:cup:color",)
    assert comparison.cases[0].newly_found == ()
    assert comparison.same_gold
    assert not compare_records(worse, worse).regressed

    text = format_comparison(comparison)
    assert "2026-11-15.1" in text and "requirement_f1" in text and "show_cup:cup:color" in text
    assert "골드셋이 다르다" not in text
    summary = format_summary(worse)
    assert "ko-01-cafe-concept" in summary and "show_cup:cup:color" in summary

    # 골드셋을 고친 뒤의 기록과는 같은 잣대가 아니라고 알린다
    edited = {**worse, "gold": {**worse["gold"], "fingerprint": "다른 골드셋"}}
    other = compare_records(base, edited)
    assert not other.same_gold and "골드셋이 다르다" in format_comparison(other)


def test_latest_record_of_an_empty_folder_is_none(tmp_path):
    assert latest_record(tmp_path) is None
    assert latest_record(tmp_path / "missing") is None


def _revised(case, edit):
    """골드 한 건을 고친 사본 (edit 가 기대 IR 문서를 고친다)."""
    document = json.loads(json.dumps(case.document))
    edit(document)
    return GoldCase.from_dict(
        {
            "case_id": case.case_id,
            "language": case.language,
            "brief": case.brief,
            "target_duration_ms": case.target_duration_ms,
            "expected": document,
        }
    )


def _replay_structurer(text):
    return LLMStructurer(ReplayLLMClient([text], model="rescoring-model"), max_attempts=1)


def test_rescoring_with_a_revised_gold_keeps_the_run_and_updates_the_scores(cases):
    """골드를 고친 뒤 옛 기록의 원본 응답을 다시 채점한다. 모델을 부르지 않고 실행 정보는 그대로다."""
    record = build_record(
        _replay_runs(cases, _perfect_responses(cases)),
        structurer=_STRUCTURER,
        run_at="2026-11-14T10:00:00+09:00",
    )
    ramen = _case(cases, "ko-05-ramen-recipe")

    def add_condition(document):  # 응답에는 없는 필수 조건을 골드에 더한다
        document["scenes"][2]["requirements"].append(
            {"entity_key": "caption", "predicate": "color", "expected_value": "white"}
        )

    revised = [_revised(c, add_condition) if c is ramen else c for c in cases]
    rescored = rescore_record(
        record, revised, _replay_structurer, rescored_at="2026-11-15T08:00:00+09:00"
    )

    assert rescored["run_at"] == record["run_at"]
    assert rescored["structurer"] == record["structurer"]  # 응답이 알려 준 모델도 원래 것
    assert rescored["gold"]["fingerprint"] != record["gold"]["fingerprint"]
    assert rescored["rescored"] == {
        "at": "2026-11-15T08:00:00+09:00",
        "gold_fingerprint_before": record["gold"]["fingerprint"],
    }
    entry = next(c for c in rescored["cases"] if c["case_id"] == ramen.case_id)
    original = next(c for c in record["cases"] if c["case_id"] == ramen.case_id)
    assert entry["missed_requirements"] == ["done:caption:color"]
    for field in ("response", "model", "attempts", "tokens", "elapsed_ms", "repair_notes"):
        assert entry[field] == original[field]
    assert rescored["summary"]["requirement_recall"] < 1.0
    assert compare_records(record, rescored).cases[0].newly_missed == ("done:caption:color",)


def test_rescoring_keeps_a_failed_case_failed(cases):
    case = _case(cases, "ko-05-ramen-recipe")
    record = build_record(
        _replay_runs([case], {case.case_id: "not json"}),
        structurer=_STRUCTURER,
        run_at="2026-11-14T10:00:00+09:00",
    )
    rescored = rescore_record(record, [case], _replay_structurer, rescored_at="2026-11-15")
    assert rescored["summary"]["failures"] == 1
    assert rescored["cases"][0]["error"]


# ---- 실행 정보 ---------------------------------------------------------------------
def test_llm_structurer_exposes_the_run_details_that_records_read():
    """기록은 구조화기의 시도 횟수·마지막 응답·보정 메모를 읽는다. 포트에 없는 속성이라 이름이 바뀌면 기록의 칸이
    조용히 비므로, LLMStructurer 가 그 이름을 지키는지 여기서 확인한다."""
    assert isinstance(LLMStructurer(ReplayLLMClient([])), StructuringRunDetails)
    assert not isinstance(FakeStructurer(), StructuringRunDetails)


def test_structurer_without_run_details_leaves_those_fields_empty(cases):
    case = _case(cases, "ko-05-ramen-recipe")
    runs = run_gold_set(FakeStructurer(ir=case.spec.ir), [case])
    run = runs[0]
    assert run.score.requirements == Tally(tp=6)
    assert (run.response, run.model, run.attempts, run.input_tokens) == (None, None, None, None)
    assert run.repair_notes == ()


def test_unexpected_error_fails_only_that_case_and_the_run_continues(cases):
    """구조화기 안의 결함(KeyError 등)이 나도 그 사례만 실패로 남기고 나머지를 돈다 (돈 들인 응답을 잃지 않게)."""
    first, second = _case(cases, "ko-05-ramen-recipe"), _case(cases, "ko-03-dog-park")

    class Flaky(FakeStructurer):
        def structure(self, request):
            if request.prompt == first.brief:
                raise KeyError("scenes")
            return second.spec.ir

    runs = run_gold_set(Flaky(), [first, second])
    assert runs[0].score.error == "UNEXPECTED_ERROR: KeyError: 'scenes'"
    assert runs[0].score.requirements == Tally(fn=6)
    assert runs[1].score.error is None and runs[1].score.requirements.fn == 0


def test_interrupt_still_stops_the_run(cases):
    class Interrupted(FakeStructurer):
        def structure(self, request):
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_gold_set(Interrupted(), cases[:1])


# ---- 다시 채점이 기록을 지우지 않게 ----------------------------------------------------
def test_loading_a_missing_or_empty_gold_folder_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="골드 폴더가 없"):
        load_gold_cases(tmp_path / "missing")
    with pytest.raises(ValueError, match="골드 사례가 없"):
        load_gold_cases(tmp_path)


def test_rescoring_refuses_to_drop_record_cases_missing_from_the_gold(cases):
    """골드에서 사례가 빠졌다고 기록의 사례(돈 들여 받은 응답)를 조용히 지우지 않는다."""
    record = build_record(
        _replay_runs(cases, _perfect_responses(cases)),
        structurer=_STRUCTURER,
        run_at="2026-11-14T10:00:00+09:00",
    )
    fewer = [c for c in cases if c.case_id != "ko-05-ramen-recipe"]
    with pytest.raises(ValueError, match="ko-05-ramen-recipe"):
        rescore_record(record, fewer, _replay_structurer, rescored_at="2026-11-15")


def test_record_and_summary_say_when_entities_were_matched_in_order(cases, monkeypatch):
    """짝 맞추기가 상한을 넘어 차례로 고른 사례는 기록과 요약에 드러난다 (보통 기록에는 그 칸이 없다)."""
    case = _case(cases, "ko-01-cafe-concept")
    usual = build_record(
        _replay_runs([case], _perfect_responses([case])),
        structurer=_STRUCTURER,
        run_at="2026-11-14",
    )
    assert "entity_matching" not in usual["cases"][0]
    assert "greedy_entity_matchings" not in usual["summary"]

    monkeypatch.setattr(
        importlib.import_module("frame.evaluation.structuring._matching"), "SEARCH_LIMIT", 1
    )
    record = build_record(
        _replay_runs([case], _perfect_responses([case])),
        structurer=_STRUCTURER,
        run_at="2026-11-14",
    )
    assert record["cases"][0]["entity_matching"] == "greedy"
    assert record["summary"]["greedy_entity_matchings"] == ["ko-01-cafe-concept"]
    assert "차례로 고른 사례" in format_summary(record)


# ---- 커밋되는 기록에 경로·계정이 새지 않게 ------------------------------------------------
@pytest.mark.parametrize(
    "error",
    [
        LLMUnavailable(
            r"키 파일이 없습니다: C:\Users\someone\keys\openai.txt. 다른 방법도 있습니다"
        ),
        LLMUnavailable("키 파일이 없습니다: D:/secrets/key.txt"),
        OSError("[Errno 2] No such file or directory: '/home/someone/.config/frame/key'"),
        OSError(r"network share \\fileserver\team\key.txt is offline"),
    ],
)
def test_error_messages_in_records_hide_absolute_paths(cases, error):
    case = _case(cases, "ko-05-ramen-recipe")
    message = run_gold_set(FakeStructurer(error=error), [case])[0].score.error
    assert "<경로>" in message
    for leaked in ("someone", "secrets", "fileserver", "C:\\", "D:/"):
        assert leaked not in message


def test_long_error_messages_are_shortened(cases):
    case = _case(cases, "ko-05-ramen-recipe")
    message = run_gold_set(FakeStructurer(error=LLMUnavailable("x" * 2000)), [case])[0].score.error
    assert message.startswith("LLM_UNAVAILABLE: ") and len(message) <= 320


def test_record_drops_account_and_query_parts_of_urls():
    record = build_record(
        [],
        structurer={**_STRUCTURER, "base_url": "http://user:pw@localhost:8001/v1?token=abc#x"},
        run_at="2026-11-14",
    )
    assert record["structurer"]["base_url"] == "http://localhost:8001/v1"


# ---- 채점기의 결함과 모델의 실패를 가른다 ---------------------------------------------------
def test_scoring_errors_are_told_apart_from_structuring_failures(cases, monkeypatch, caplog):
    """채점기 자신의 결함은 SCORING_ERROR 로 남기고 트레이스백을 로그로 낸다.

    응답은 남아 채점기를 고친 뒤 다시 채점할 수 있다.
    """
    case = _case(cases, "ko-05-ramen-recipe")

    def broken_scoring(*args, **kwargs):
        raise ZeroDivisionError("division by zero")

    records_module = importlib.import_module("frame.evaluation.structuring._records")
    monkeypatch.setattr(records_module, "score_case", broken_scoring)
    with caplog.at_level(logging.ERROR, logger=records_module.__name__):
        run = _replay_runs([case], _perfect_responses([case]))[0]
    assert run.score.error == "SCORING_ERROR: ZeroDivisionError: division by zero"
    assert run.response and run.attempts == 1
    assert any(r.exc_info for r in caplog.records)


def test_unexpected_structurer_errors_are_logged_with_a_traceback(cases, caplog):
    class Broken(FakeStructurer):
        def structure(self, request):
            raise KeyError("scenes")

    with caplog.at_level(logging.ERROR, logger="frame.evaluation.structuring._records"):
        run = run_gold_set(Broken(), cases[:1])[0]
    assert run.score.error.startswith("UNEXPECTED_ERROR: KeyError")
    assert any(r.exc_info for r in caplog.records)
