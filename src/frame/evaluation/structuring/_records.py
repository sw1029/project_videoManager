"""골드셋 실행과 기록: 구조화기로 사례를 돌려 채점하고, 기록(JSON)을 만들고·읽고·다시 채점하고·견준다.

기록 하나 = 같은 골드셋을 한 구조화기(모델·프롬프트 버전)로 한 번 돌린 결과다 (record_version 1).
사례마다 모델의 원본 응답을 함께 남겨, 골드나 지표를 고친 뒤에도 모델을 다시 부르지 않고 다시 채점할 수 있다.
파일 이름은 `{프롬프트 버전}__{모델}__{실행 시각}.json` 이라 같은 프롬프트 버전의 기록끼리 모인다.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from frame.application.errors import AdapterError
from frame.application.ports.llm import JsonCompletion
from frame.application.ports.structurer import RequirementStructurer
from frame.domain.errors import DomainError

from ._gold import GoldCase, gold_fingerprint
from ._scoring import CaseScore, Tally, failed_case, score_case
from ._summary import (
    ENTITY_RECALL_TARGET,
    REQUIREMENT_F1_TARGET,
    SCENE_COUNT_MATCH_TARGET,
    MetricChange,
    compare_headlines,
    headline,
    summarize,
)

RECORD_VERSION = 1
_DIGITS = 4  # 기록에 남기는 비율의 소수 자릿수


# ---- 실행 ----------------------------------------------------------------------
@runtime_checkable
class StructuringRunDetails(Protocol):
    """실행 정보를 내주는 구조화기 (지금은 LLMStructurer).

    구조화기 포트에는 없는 속성이라, 기록이 읽는 이름을 여기 못박는다.
    이름이 어긋나면 테스트(isinstance)가 알린다.
    """

    attempts: int  # 마지막 structure() 의 시도 횟수
    last_completion: JsonCompletion | None  # 마지막으로 받은 원본 응답
    last_repair_notes: list[str]  # 마지막 시도에서 보정한 내용


@dataclass(frozen=True)
class CaseRun:
    case: GoldCase
    score: CaseScore
    response: str | None = None  # 모델의 마지막 원본 응답 (JSON 문자열)
    model: str | None = None  # 응답이 알려 준 모델 이름
    attempts: int | None = None
    # 마지막 시도의 토큰. 구조화기가 마지막 응답만 들고 있어 자기수정 재시도분은 빠진다 (attempts 로 가늠한다)
    input_tokens: int | None = None
    output_tokens: int | None = None
    repair_notes: tuple[str, ...] = ()
    elapsed_ms: int = 0


def run_gold_set(
    structurer: RequirementStructurer,
    cases: Iterable[GoldCase],
    *,
    clock: Callable[[], float] = time.perf_counter,
) -> tuple[CaseRun, ...]:
    """사례를 차례로 구조화해 채점한다. 구조화 실패는 그 사례의 실패로 기록하고 다음 사례로 넘어간다.

    StructuringRunDetails 를 따르는 구조화기면 시도 횟수·원본 응답·토큰·보정 메모도 남긴다.
    """
    details = structurer if isinstance(structurer, StructuringRunDetails) else None
    runs: list[CaseRun] = []
    for case in cases:
        before = details.last_completion if details else None
        started = clock()
        notes: tuple[str, ...] = ()
        try:
            ir = structurer.structure(case.request())
            score = score_case(case.case_id, case.spec, ir)
            notes = tuple(details.last_repair_notes) if details else ()
        except (AdapterError, DomainError) as exc:
            score = failed_case(case.case_id, case.spec, _describe(exc))
        except Exception as exc:  # noqa: BLE001
            # 구조화기 안의 결함(KeyError 등)도 그 사례의 실패로 남기고 다음 사례를 돈다.
            # 기록은 끝에 한 번 쓰므로, 여기서 멈추면 앞서 돈 들인 응답까지 잃는다.
            # Ctrl+C(BaseException)는 그대로 멈춘다.
            score = failed_case(
                case.case_id, case.spec, f"UNEXPECTED_ERROR: {type(exc).__name__}: {exc}"
            )
        elapsed_ms = round((clock() - started) * 1000)
        completion = details.last_completion if details else None
        if completion is before:  # 이번 사례에서 응답을 받지 못했다
            completion = None
        runs.append(
            CaseRun(
                case,
                score,
                response=completion.text if completion else None,
                model=completion.model if completion else None,
                attempts=details.attempts if details else None,
                input_tokens=completion.input_tokens if completion else None,
                output_tokens=completion.output_tokens if completion else None,
                repair_notes=notes,
                elapsed_ms=elapsed_ms,
            )
        )
    return tuple(runs)


def _describe(exc: Exception) -> str:
    code = getattr(exc, "code", None) or type(exc).__name__
    return f"{code}: {exc}"


# ---- 기록 만들기·읽기 ---------------------------------------------------------------
def build_record(
    runs: Sequence[CaseRun], *, structurer: Mapping[str, Any], run_at: str
) -> dict[str, Any]:
    """structurer: 이름·모델·프롬프트 버전 등 구조화기를 밝히는 값 (prompt_version·model 은 파일 이름에 쓰인다)."""
    scores = [r.score for r in runs]
    reported = sorted({r.model for r in runs if r.model})
    return {
        "record_version": RECORD_VERSION,
        "run_at": run_at,
        "structurer": {**structurer, "reported_models": reported},
        "gold": {
            "cases": len(runs),
            "case_ids": [r.case.case_id for r in runs],
            "fingerprint": gold_fingerprint(r.case for r in runs),
        },
        "summary": _summary_block(scores, runs),
        "by_language": {
            language: _summary_block([r.score for r in runs if r.case.language == language])
            for language in sorted({r.case.language for r in runs})
        },
        "cases": [_case_entry(r) for r in runs],
    }


def _summary_block(scores: Sequence[CaseScore], runs: Sequence[CaseRun] = ()) -> dict[str, Any]:
    evaluation = summarize(scores)
    summary: dict[str, Any] = {
        "cases": evaluation.cases,
        "failures": evaluation.failures,
        **{name: _round(value) for name, value in headline(evaluation).items()},
        "meets_target": evaluation.meets_target,
    }
    if runs:
        summary["targets"] = {
            "entity_recall": ENTITY_RECALL_TARGET,
            "requirement_f1": REQUIREMENT_F1_TARGET,
            "scene_count_match_rate": SCENE_COUNT_MATCH_TARGET,
        }
        summary["totals"] = {
            "entities": _tally(evaluation.entities),
            "requirements": _tally(evaluation.requirements),
            "appearances": _tally(evaluation.appearances),
        }
        summary["tokens"] = {
            "input": _sum_known(r.input_tokens for r in runs),
            "output": _sum_known(r.output_tokens for r in runs),
        }
    return summary


def _case_entry(run: CaseRun) -> dict[str, Any]:
    score = run.score
    return {
        "case_id": run.case.case_id,
        "language": run.case.language,
        "error": score.error,
        "entities": _tally(score.entities),
        "requirements": _tally(score.requirements),
        "appearances": _tally(score.appearances),
        "requirement_f1": _round(score.requirements.f1),
        "scenes": {
            "expected": score.expected_scene_count,
            "predicted": score.predicted_scene_count,
            "boundary_error_ms": score.boundary_error_ms,
            "duration_error_ms": score.duration_error_ms,
        },
        "missed_entities": list(score.missed_entities),
        "spurious_entities": list(score.spurious_entities),
        "missed_requirements": list(score.missed_requirements),
        "mismatched_requirements": [
            {"key": m.key, "expected": m.expected, "actual": m.actual}
            for m in score.mismatched_requirements
        ],
        "spurious_requirements": list(score.spurious_requirements),
        "attempts": run.attempts,
        "model": run.model,
        "tokens": {"input": run.input_tokens, "output": run.output_tokens},
        "elapsed_ms": run.elapsed_ms,
        "repair_notes": list(run.repair_notes),
        "response": run.response,
    }


def record_filename(record: Mapping[str, Any]) -> str:
    structurer = record.get("structurer", {})
    stamp = re.sub(r"[^0-9T]", "", str(record["run_at"])[:19])
    parts = (
        structurer.get("prompt_version") or "unknown",
        structurer.get("model") or "unknown",
        stamp,
    )
    return "__".join(re.sub(r"[^A-Za-z0-9._-]+", "-", str(p)) for p in parts) + ".json"


def write_record(record: Mapping[str, Any], directory: Path | str) -> Path:
    """폴더에 record_filename 이름으로 쓴다."""
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    return save_record(record, folder / record_filename(record))


def save_record(record: Mapping[str, Any], path: Path | str) -> Path:
    """정해진 파일에 쓴다 (다시 채점한 기록으로 원래 파일을 고쳐 쓸 때)."""
    target = Path(path)
    target.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return target


def read_record(path: Path | str) -> dict[str, Any]:
    record = json.loads(Path(path).read_text(encoding="utf-8"))
    if record.get("record_version") != RECORD_VERSION:
        raise ValueError(
            f"지원하지 않는 기록 형식입니다: {record.get('record_version')} (지원: {RECORD_VERSION})"
        )
    return record


def latest_record(
    directory: Path | str, *, exclude: Path | str | None = None
) -> dict[str, Any] | None:
    """폴더에서 실행 시각이 가장 늦은 기록. exclude 는 방금 쓴 기록처럼 빼고 볼 파일이다."""
    folder = Path(directory)
    if not folder.is_dir():
        return None
    skip = Path(exclude).resolve() if exclude else None
    records = [read_record(p) for p in folder.glob("*.json") if p.resolve() != skip]
    return max(records, key=lambda r: str(r["run_at"]), default=None)


# ---- 다시 채점 -------------------------------------------------------------------
def recorded_responses(record: Mapping[str, Any]) -> dict[str, str]:
    """사례 id → 원본 응답. ReplayLLMClient 로 다시 채점할 때 쓴다 (응답이 없던 사례는 빠진다)."""
    return {c["case_id"]: c["response"] for c in record["cases"] if c.get("response") is not None}


def rescore_record(
    record: Mapping[str, Any],
    cases: Iterable[GoldCase],
    structurer_for: Callable[[str], RequirementStructurer],
    *,
    rescored_at: str,
) -> dict[str, Any]:
    """기록에 남은 원본 응답을 지금 골드로 다시 채점한 새 기록. 모델을 부르지 않는다.

    structurer_for(응답) 은 그 응답 하나만 재생하는 구조화기를 만든다
    (예: LLMStructurer(ReplayLLMClient([응답]), max_attempts=1)).
    실행 시각·구조화기·모델·시도 횟수·토큰·시간·보정 메모는 원래 기록의 것을 그대로 둔다.
    기록에 없는 골드 사례는 건너뛰고, 응답 없이 실패했던 사례는 원래 오류로 다시 실패 처리한다.
    """
    entries = {c["case_id"]: c for c in record["cases"]}
    runs: list[CaseRun] = []
    for case in cases:
        entry = entries.get(case.case_id)
        if entry is None:
            continue
        response = entry.get("response")
        if response is None:
            score = failed_case(case.case_id, case.spec, entry.get("error") or "응답 없음")
        else:
            score = run_gold_set(structurer_for(response), [case])[0].score
        tokens = entry.get("tokens") or {}
        runs.append(
            CaseRun(
                case,
                score,
                response=response,
                model=entry.get("model"),
                attempts=entry.get("attempts"),
                input_tokens=tokens.get("input"),
                output_tokens=tokens.get("output"),
                repair_notes=tuple(entry.get("repair_notes", ())),
                elapsed_ms=int(entry.get("elapsed_ms", 0)),
            )
        )
    structurer = {k: v for k, v in record["structurer"].items() if k != "reported_models"}
    rescored = build_record(runs, structurer=structurer, run_at=str(record["run_at"]))
    rescored["rescored"] = {
        "at": rescored_at,
        "gold_fingerprint_before": record.get("gold", {}).get("fingerprint"),
    }
    return rescored


# ---- 기록 비교 -------------------------------------------------------------------
@dataclass(frozen=True)
class CaseChange:
    case_id: str
    baseline_f1: float | None
    candidate_f1: float | None
    newly_missed: tuple[str, ...]  # 비교 쪽에서 새로 놓치거나 값이 틀린 골드 조건
    newly_found: tuple[str, ...]  # 비교 쪽에서 새로 맞춘 골드 조건


@dataclass(frozen=True)
class RecordComparison:
    baseline: str
    candidate: str
    metrics: tuple[MetricChange, ...]
    cases: tuple[CaseChange, ...]  # 결과가 달라진 사례만
    same_gold: bool = True  # 두 기록이 같은 골드셋으로 잰 것인가

    @property
    def regressed(self) -> bool:
        return any(m.regressed for m in self.metrics)


def compare_records(baseline: Mapping[str, Any], candidate: Mapping[str, Any]) -> RecordComparison:
    names = list(headline(summarize([])))
    metrics = compare_headlines(
        {n: baseline["summary"].get(n) for n in names},
        {n: candidate["summary"].get(n) for n in names},
    )
    before = {c["case_id"]: c for c in baseline["cases"]}
    changes: list[CaseChange] = []
    for case in candidate["cases"]:
        old = before.get(case["case_id"])
        if old is None:
            continue
        lost_before, lost_now = _lost(old), _lost(case)
        newly_missed = tuple(k for k in lost_now if k not in lost_before)
        newly_found = tuple(k for k in lost_before if k not in lost_now)
        f1_before, f1_now = old.get("requirement_f1"), case.get("requirement_f1")
        if newly_missed or newly_found or f1_before != f1_now:
            changes.append(
                CaseChange(case["case_id"], f1_before, f1_now, newly_missed, newly_found)
            )
    same_gold = baseline["gold"].get("fingerprint") == candidate["gold"].get("fingerprint")
    return RecordComparison(
        record_label(baseline),
        record_label(candidate),
        metrics,
        tuple(changes),
        same_gold=same_gold,
    )


def record_label(record: Mapping[str, Any]) -> str:
    """기록을 사람이 알아보는 이름: 프롬프트 버전 / 모델 / 실행 시각."""
    structurer = record.get("structurer", {})
    return f"{structurer.get('prompt_version')} / {structurer.get('model')} / {record['run_at']}"


def _lost(case: Mapping[str, Any]) -> list[str]:
    return [*case["missed_requirements"], *(m["key"] for m in case["mismatched_requirements"])]


def _tally(tally: Tally) -> dict[str, int]:
    return {"tp": tally.tp, "fp": tally.fp, "fn": tally.fn}


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, _DIGITS)


def _sum_known(values: Iterable[int | None]) -> int | None:
    known = [v for v in values if v is not None]
    return sum(known) if known else None
