"""구조화 골드셋 실행과 결과 기록 (US-06 AC-11, T2·T6). 지표 계산은 structuring_metrics 가 한다.

골드 사례 = 브리프 + 목표 길이 + 주석이 붙은 기대 IR (tests/evaluation/structuring_gold/*.json).

기록(JSON) 하나 = 같은 골드셋을 한 구조화기(모델·프롬프트 버전)로 한 번 돌린 결과다.
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
from typing import Any

from frame.application.errors import AdapterError
from frame.application.ports.structurer import RequirementStructurer, StructuringRequest
from frame.domain.errors import DomainError
from frame.domain.hashing import canonical_hash

from .structuring_metrics import (
    ENTITY_RECALL_TARGET,
    REQUIREMENT_F1_TARGET,
    SCENE_COUNT_MATCH_TARGET,
    CaseScore,
    GoldSpec,
    MetricChange,
    Tally,
    compare_headlines,
    failed_case,
    headline,
    score_case,
    summarize,
)

RECORD_VERSION = 1
_DIGITS = 4  # 기록에 남기는 비율의 소수 자릿수


@dataclass(frozen=True)
class GoldCase:
    case_id: str
    language: str
    brief: str
    target_duration_ms: int
    document: Mapping[str, Any]  # 주석이 붙은 기대 IR
    spec: GoldSpec
    covers: tuple[str, ...] = ()
    notes: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> GoldCase:
        document = data["expected"]
        return cls(
            case_id=str(data["case_id"]),
            language=str(data["language"]),
            brief=str(data["brief"]),
            target_duration_ms=int(data["target_duration_ms"]),
            document=document,
            spec=GoldSpec.from_document(document),
            covers=tuple(data.get("covers", ())),
            notes=str(data.get("notes", "")),
        )

    def request(self) -> StructuringRequest:
        return StructuringRequest(self.brief, target_duration_ms=self.target_duration_ms)


def load_gold_cases(directory: Path | str) -> tuple[GoldCase, ...]:
    """폴더의 *.json 을 파일 이름 순서로 읽는다. 기대 IR 이 IR 규칙을 어기면 ValidationError."""
    paths = sorted(Path(directory).glob("*.json"))
    return tuple(GoldCase.from_dict(json.loads(p.read_text(encoding="utf-8"))) for p in paths)


def gold_fingerprint(cases: Iterable[GoldCase]) -> str:
    """골드셋 내용의 해시. 지문이 다른 기록끼리는 같은 잣대로 잰 것이 아니다."""
    return canonical_hash(
        [
            {
                "case_id": c.case_id,
                "brief": c.brief,
                "target_duration_ms": c.target_duration_ms,
                "expected": c.document,
            }
            for c in cases
        ]
    )


# ---- 실행 ----------------------------------------------------------------------
@dataclass(frozen=True)
class CaseRun:
    case: GoldCase
    score: CaseScore
    response: str | None = None  # 모델의 마지막 원본 응답 (JSON 문자열)
    model: str | None = None  # 응답이 알려 준 모델 이름
    attempts: int | None = None
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

    LLMStructurer 처럼 attempts·last_completion·last_repair_notes 를 가진 구조화기면 그 값도 남긴다.
    """
    runs: list[CaseRun] = []
    for case in cases:
        before = getattr(structurer, "last_completion", None)
        started = clock()
        notes: tuple[str, ...] = ()
        try:
            ir = structurer.structure(case.request())
            score = score_case(case.case_id, case.spec, ir)
            notes = tuple(getattr(structurer, "last_repair_notes", ()))
        except (AdapterError, DomainError) as exc:
            score = failed_case(case.case_id, case.spec, _describe(exc))
        elapsed_ms = round((clock() - started) * 1000)
        completion = getattr(structurer, "last_completion", None)
        if completion is before:  # 이번 사례에서 응답을 받지 못했다
            completion = None
        runs.append(
            CaseRun(
                case,
                score,
                response=getattr(completion, "text", None),
                model=getattr(completion, "model", None),
                attempts=getattr(structurer, "attempts", None),
                input_tokens=getattr(completion, "input_tokens", None),
                output_tokens=getattr(completion, "output_tokens", None),
                repair_notes=notes,
                elapsed_ms=elapsed_ms,
            )
        )
    return tuple(runs)


def _describe(exc: Exception) -> str:
    code = getattr(exc, "code", None) or type(exc).__name__
    return f"{code}: {exc}"


# ---- 기록 ----------------------------------------------------------------------
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
        "summary": _summary(scores, runs),
        "by_language": {
            language: _summary([r.score for r in runs if r.case.language == language])
            for language in sorted({r.case.language for r in runs})
        },
        "cases": [_case_entry(r) for r in runs],
    }


def _summary(scores: Sequence[CaseScore], runs: Sequence[CaseRun] = ()) -> dict[str, Any]:
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
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / record_filename(record)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return path


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


def recorded_responses(record: Mapping[str, Any]) -> dict[str, str]:
    """사례 id → 원본 응답. ReplayLLMClient 로 다시 채점할 때 쓴다 (응답이 없던 사례는 빠진다)."""
    return {c["case_id"]: c["response"] for c in record["cases"] if c.get("response") is not None}


# ---- 비교 ----------------------------------------------------------------------
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
        _label(baseline), _label(candidate), metrics, tuple(changes), same_gold=same_gold
    )


def _lost(case: Mapping[str, Any]) -> list[str]:
    return [*case["missed_requirements"], *(m["key"] for m in case["mismatched_requirements"])]


def _label(record: Mapping[str, Any]) -> str:
    structurer = record.get("structurer", {})
    return f"{structurer.get('prompt_version')} / {structurer.get('model')} / {record['run_at']}"


# ---- 출력 ----------------------------------------------------------------------
_LABELS = {
    "entity_recall": "객체 재현율",
    "entity_precision": "객체 정밀도",
    "requirement_precision": "요구조건 정밀도",
    "requirement_recall": "요구조건 재현율",
    "requirement_f1": "요구조건 F1",
    "appearance_f1": "등장 장면 F1",
    "scene_count_match_rate": "장면 수 일치",
    "mean_boundary_error_ms": "경계 오차(ms)",
    "mean_duration_error_ms": "길이 오차(ms)",
}


def format_summary(record: Mapping[str, Any]) -> str:
    summary = record["summary"]
    targets = summary.get("targets", {})
    lines = [
        f"구조화 골드셋 — {_label(record)} ({summary['cases']}건, 실패 {summary['failures']})",
    ]
    for name, label in _LABELS.items():
        target = f" (목표 ≥ {targets[name]})" if name in targets else ""
        lines.append(f"  {label:<12} {_fmt(summary.get(name))}{target}")
    lines.append(f"  목표 충족: {'예' if summary['meets_target'] else '아니오'}")
    lines.append("  사례 (요구조건 F1 | 놓침 | 값 틀림 | 골드에 없음 | 객체 놓침·남음):")
    for case in record["cases"]:
        if case["error"]:
            lines.append(f"    {case['case_id']}: 실패 {case['error']}")
            continue
        mismatched = [f"{m['key']}={m['actual']!r}" for m in case["mismatched_requirements"]]
        entities = case["missed_entities"] + [f"+{k}" for k in case["spurious_entities"]]
        lines.append(
            f"    {case['case_id']} {_fmt(case['requirement_f1'])} | "
            f"{_join(case['missed_requirements'])} | {_join(mismatched)} | "
            f"{_join(case['spurious_requirements'])} | {_join(entities)}"
        )
    return "\n".join(lines)


def format_comparison(comparison: RecordComparison) -> str:
    lines = [f"기준 {comparison.baseline}", f"비교 {comparison.candidate}"]
    if not comparison.same_gold:
        lines.append(
            "  ※ 골드셋이 다르다. 기준 기록의 원본 응답을 지금 골드로 다시 채점한 뒤 견줄 것 "
            "(recorded_responses + ReplayLLMClient)"
        )
    for change in comparison.metrics:
        mark = "  ← 회귀" if change.regressed else ""
        delta = "" if change.delta is None else f"{change.delta:+.4f}"
        lines.append(
            f"  {change.name:<24} {_fmt(change.baseline):>9} → {_fmt(change.candidate):>9} "
            f"{delta:>9}{mark}"
        )
    if comparison.cases:
        lines.append("  달라진 사례:")
    for case in comparison.cases:
        lines.append(
            f"    {case.case_id} F1 {_fmt(case.baseline_f1)} → {_fmt(case.candidate_f1)}"
            f" | 새로 놓침: {_join(case.newly_missed)} | 새로 맞춤: {_join(case.newly_found)}"
        )
    return "\n".join(lines)


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".") if value % 1 else f"{value:.0f}"
    return str(value)


def _join(items: Sequence[str]) -> str:
    return ", ".join(items) if items else "-"


def _tally(tally: Tally) -> dict[str, int]:
    return {"tp": tally.tp, "fp": tally.fp, "fn": tally.fn}


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, _DIGITS)


def _sum_known(values: Iterable[int | None]) -> int | None:
    known = [v for v in values if v is not None]
    return sum(known) if known else None
