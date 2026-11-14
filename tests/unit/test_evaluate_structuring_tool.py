"""구조화 골드셋 기록을 견주고 다시 채점하는 도구 (frame.tools.evaluate_structuring). 모델은 부르지 않는다."""

import json
from pathlib import Path

from frame.adapters.llm import ReplayLLMClient
from frame.adapters.structurer import LLMStructurer
from frame.evaluation.structuring import (
    build_record,
    load_gold_cases,
    read_record,
    run_gold_set,
    write_record,
)
from frame.tools import evaluate_structuring

GOLD_DIR = Path(__file__).resolve().parents[1] / "evaluation" / "structuring_gold"


def _record(cases, responses, prompt_version, run_at):
    runs = []
    for case in cases:
        client = ReplayLLMClient([responses[case.case_id]], model="replay-model")
        runs.extend(run_gold_set(LLMStructurer(client, max_attempts=1), [case]))
    structurer = {"model": "replay", "prompt_version": prompt_version}
    return build_record(runs, structurer=structurer, run_at=run_at)


def _cases_and_responses():
    cases = load_gold_cases(GOLD_DIR)[:2]
    return cases, {c.case_id: json.dumps(c.document, ensure_ascii=False) for c in cases}


def test_compare_prints_the_table_and_exits_1_on_regression(tmp_path, capsys):
    cases, perfect = _cases_and_responses()
    base = write_record(_record(cases, perfect, "v1", "2026-11-14T10:00:00+09:00"), tmp_path)
    worse_responses = dict(perfect)
    document = json.loads(worse_responses[cases[0].case_id])
    document["scenes"][0]["requirements"] = []
    worse_responses[cases[0].case_id] = json.dumps(document, ensure_ascii=False)
    worse = write_record(
        _record(cases, worse_responses, "v2", "2026-11-15T10:00:00+09:00"), tmp_path
    )

    assert evaluate_structuring.main(["compare", str(base), str(worse)]) == 1
    assert "← 회귀" in capsys.readouterr().out
    assert evaluate_structuring.main(["compare", str(base), str(base)]) == 0


def test_rescore_shows_and_with_write_rewrites_the_record(tmp_path, capsys):
    cases, perfect = _cases_and_responses()
    record = _record(cases, perfect, "v1", "2026-11-14T10:00:00+09:00")
    record["gold"]["fingerprint"] = "옛 골드"  # 기록을 남긴 뒤 골드가 바뀐 상황
    path = tmp_path / "renamed-record.json"
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")

    assert evaluate_structuring.main(["rescore", str(path), "--gold", str(GOLD_DIR)]) == 0
    assert read_record(path)["gold"]["fingerprint"] == "옛 골드"  # --write 없이는 보여 주기만
    assert "골드셋이 다르다" in capsys.readouterr().out

    assert (
        evaluate_structuring.main(["rescore", str(path), "--gold", str(GOLD_DIR), "--write"]) == 0
    )
    rewritten = read_record(path)
    assert rewritten["gold"]["fingerprint"] != "옛 골드"
    assert rewritten["rescored"]["gold_fingerprint_before"] == "옛 골드"
    assert [c["case_id"] for c in rewritten["cases"]] == [c.case_id for c in cases]
    assert list(tmp_path.iterdir()) == [path]  # 받은 파일을 그 자리에서 고쳐 쓴다
