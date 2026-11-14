"""골드셋 실행과 기록: 구조화기로 사례를 돌려 채점하고, 기록(JSON)을 만들고·읽고·다시 채점하고·견준다.

기록 하나 = 같은 골드셋을 한 구조화기(모델·프롬프트 버전)로 한 번 돌린 결과다 (record_version 1).
사례마다 모델의 원본 응답을 함께 남겨, 골드나 지표를 고친 뒤에도 모델을 다시 부르지 않고 다시 채점할 수 있다.
파일 이름은 `{프롬프트 버전}__{모델}__{실행 시각}.json` 이라 같은 프롬프트 버전의 기록끼리 모인다.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit, urlunsplit

from frame.application.errors import AdapterError
from frame.application.ports.llm import JsonCompletion
from frame.application.ports.structurer import RequirementStructurer
from frame.domain.errors import DomainError

from ._gold import GoldCase, gold_fingerprint
from ._matching import EXHAUSTIVE
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

log = logging.getLogger(__name__)

RECORD_VERSION = 1
_DIGITS = 4  # 기록에 남기는 비율의 소수 자릿수
# 기록은 저장소에 커밋하는 파일이라, 오류 문구의 절대 경로(키 파일 위치·사용자 폴더)를 가리고 길이를 줄인다
_ABSOLUTE_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\|~[\\/]|(?<![\w/:.~-])/(?=[^\s/]))[^\s'\"<>|]*")
_MESSAGE_LIMIT = 300
SCORING_ERROR = "SCORING_ERROR"  # 채점기 자신의 결함 (모델·구조화의 실패가 아니다)


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
    """사례를 차례로 구조화해 채점한다. 실패는 그 사례의 실패로 기록하고 다음 사례로 넘어간다.

    - 구조화 실패: 어댑터·도메인 오류는 그 코드로, 구조화기 안의 결함은 UNEXPECTED_ERROR 로 남긴다
    - 채점 실패(채점기 자신의 결함): SCORING_ERROR 로 남겨 모델의 실패와 가른다. 응답은 남아 고친 뒤 다시 채점할 수 있다
    - 예상 밖 오류는 트레이스백을 로그로 낸다. 기록은 끝에 한 번 쓰므로, 여기서 멈추면 앞서 돈 들인 응답까지 잃는다.
      Ctrl+C(BaseException)는 그대로 멈춘다
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
        except (AdapterError, DomainError) as exc:
            score = failed_case(case.case_id, case.spec, _describe(exc))
        except Exception as exc:  # noqa: BLE001
            log.exception("골드 사례 %s 를 구조화하다 예상 밖 오류가 났다", case.case_id)
            score = failed_case(case.case_id, case.spec, _unexpected("UNEXPECTED_ERROR", exc))
        else:
            notes = tuple(details.last_repair_notes) if details else ()
            try:
                score = score_case(case.case_id, case.spec, ir)
            except Exception as exc:  # noqa: BLE001
                log.exception("골드 사례 %s 를 채점하다 오류가 났다 (채점기의 결함)", case.case_id)
                score = failed_case(case.case_id, case.spec, _unexpected(SCORING_ERROR, exc))
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
    return f"{code}: {public_message(str(exc))}"


def _unexpected(code: str, exc: Exception) -> str:
    return f"{code}: {type(exc).__name__}: {public_message(str(exc))}"


def _structuring_failed(error: str | None) -> bool:
    """구조화가 실패한 사례의 오류인가 (채점기의 결함 SCORING_ERROR 는 아니다)."""
    return error is not None and not error.startswith(f"{SCORING_ERROR}:")


def public_message(text: str) -> str:
    """커밋되는 기록에 남길 문구: 절대 경로는 <경로> 로 가리고, 길면 자른다."""
    masked = _ABSOLUTE_PATH.sub("<경로>", text)
    return masked if len(masked) <= _MESSAGE_LIMIT else masked[: _MESSAGE_LIMIT - 1] + "…"


def _public_url(value: Any) -> Any:
    """URL 이면 계정(user:pw@)·질의·조각을 뺀 주소, 아니면 그대로 (기록에 접속 정보가 새지 않게)."""
    if not isinstance(value, str):
        return value
    parts = urlsplit(value)
    if not (parts.scheme and parts.netloc):
        return value
    host = parts.hostname or ""
    try:
        netloc = f"{host}:{parts.port}" if parts.port else host
    except ValueError:  # 포트가 숫자가 아니다
        netloc = host
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


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
        "structurer": {
            **{key: _public_url(value) for key, value in structurer.items()},
            "reported_models": reported,
        },
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
        greedy = [r.case.case_id for r in runs if r.score.entity_matching != EXHAUSTIVE]
        if greedy:  # 보통은 없어서 칸을 두지 않는다 (옛 기록과 같은 모양)
            summary["greedy_entity_matchings"] = greedy
    return summary


def _case_entry(run: CaseRun) -> dict[str, Any]:
    score = run.score
    entry: dict[str, Any] = {
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
    }
    if score.entity_matching != EXHAUSTIVE:  # 상한을 넘어 객체 짝을 차례로 골랐다
        entry["entity_matching"] = score.entity_matching
    return entry | {
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
    기록에 없는 골드 사례(나중에 더한 사례)는 건너뛰고, 응답 없이 실패했던 사례는 원래 오류로 다시 실패 처리한다.
    응답이 남은 실패 사례를 재생해도 구조화가 실패하면 실행 때의 오류를 남긴다.
    재생은 1회 시도라 문구가 달라지기 때문이다 (예: 재시도 끝의 시간 초과가 '1회 시도 후 실패' 로 바뀐다).
    지금 코드가 그 응답을 받아들이면 채점하고, 채점기가 깨지면(SCORING_ERROR) 지금 오류를 남긴다.
    기록에는 있는데 골드에 없는 사례가 있으면 ValueError 다. 돈 들여 받은 응답을 조용히 지우지 않으려는 것이다.
    """
    cases = tuple(cases)
    entries = {c["case_id"]: c for c in record["cases"]}
    known = {case.case_id for case in cases}
    missing = [case_id for case_id in entries if case_id not in known]
    if missing:
        raise ValueError(
            "골드에 없는 사례가 기록에 있어 다시 채점하지 않는다 (기록의 사례를 지우지 않으려고): "
            + ", ".join(missing)
        )
    runs: list[CaseRun] = []
    for case in cases:
        entry = entries.get(case.case_id)
        if entry is None:
            continue
        response = entry.get("response")
        if response is None:
            error = public_message(entry.get("error") or "응답 없음")
            score = failed_case(case.case_id, case.spec, error)
        else:
            score = run_gold_set(structurer_for(response), [case])[0].score
            if _structuring_failed(score.error) and _structuring_failed(entry.get("error")):
                score = replace(score, error=public_message(entry["error"]))
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
