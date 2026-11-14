"""지표 1 평가 실행기 (T12, ADR-0022).

케이스마다: 변경 계획 → 수정 버전 생성 → 생성 접수 → 완료 대기 → 검수 결과 수집. 그 뒤 판정과 보고서를 만든다.
- 화면이 하는 순서를 그대로 밟는다 (연구 문서 §4·F1). 계획을 먼저 보고, 계획이 정한 종류·변경·유지 요소로
  수정 버전을 만든다. 같은 프로세스에서 계획을 보므로 생성기가 계획 때 받아 둔 부모 주소로 편집할 수 있다 (ADR-0020).
- 판정할 요구조건은 수정 버전 IR 에서 키로 고른다 (바뀐 장면 × 대상 객체 × 술어). 결과가 없거나 미판정이면 실패다.
- 비대상 변경률은 측정 기준(detail.method)마다 따로 잰다 (vlm_keep · ssim). 목표 판정에 쓸 기준은 부르는 쪽이 고르고,
  보고서에는 두 기준을 모두 남긴다 (연구 문서 §7 결정 2 가 정해지지 않았다).
- 케이스 하나의 실패(부모 없음, 검증 오류, 생성 실패, 시간 초과)는 그 케이스에 기록하고 다음 케이스로 간다.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from frame.application.dto import CreateRevisionCommand
from frame.application.errors import AdapterError
from frame.application.ports.generator import ProductionPreview
from frame.application.use_cases.plan_revision import PlanRevisionCommand, RevisionPlan
from frame.domain.checks import CheckResult
from frame.domain.enums import VersionState
from frame.domain.errors import DomainError
from frame.domain.models import VideoVersion
from frame.domain.state_machine import TERMINAL_STATES

from .edit_cases import EditCaseSpec
from .edit_metrics import (
    PRESERVATION_BASES,
    EditCase,
    EditCaseOutcome,
    EditEvaluation,
    evaluate_case,
    evaluate_cases,
    select_preservation,
)

TOOL_NAME = "frame.tools.evaluate_edits"
REPORT_VERSION = 1
TARGETS = {
    "success_rate": EditEvaluation.success_target,
    "non_target_change_rate": EditEvaluation.change_target,
}


class EditApp(Protocol):
    """평가가 부르는 유스케이스. config.Container 가 그대로 맞는다."""

    plan_revision: Any
    create_revision: Any
    accept_generation: Any
    queries: Any


class WaitTimeout(Exception):
    code = "EVALUATION_WAIT_TIMEOUT"

    def __init__(self, message: str, version: VideoVersion) -> None:
        super().__init__(message)
        self.version = version  # 마지막으로 본 상태


@dataclass
class CaseRun:
    """케이스 하나를 실행한 기록 (판정 전)."""

    spec: EditCaseSpec
    parent: VideoVersion | None = None
    plan: RevisionPlan | None = None
    revision_version_id: str | None = None
    changed_scene_keys: tuple[str, ...] = ()
    version: VideoVersion | None = None  # 끝난(또는 마지막으로 본) 수정 버전
    results: tuple[CheckResult, ...] = ()
    error_code: str | None = None
    error_message: str | None = None
    elapsed_s: float = 0.0


@dataclass(frozen=True)
class CaseJudgement:
    target_requirement_keys: frozenset[str]
    cases: Mapping[str, EditCase]  # 측정 기준 -> 그 기준의 보존 결과만 남긴 케이스
    outcomes: Mapping[str, EditCaseOutcome]


# ---- 실행 ----


def wait_for_version(
    queries: Any,
    version_id: str,
    *,
    timeout_s: float,
    poll_s: float,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> VideoVersion:
    """버전이 끝날(COMPLETED·FAILED) 때까지 상태를 다시 읽는다. 인라인 디스패처면 처음 읽을 때 이미 끝나 있다."""
    deadline = monotonic() + timeout_s
    while True:
        version = queries.get_version(version_id).version
        if version.state in TERMINAL_STATES:
            return version
        if monotonic() >= deadline:
            raise WaitTimeout(
                f"{timeout_s:g}초 안에 끝나지 않았습니다 (마지막 상태 {version.state})", version
            )
        sleep(poll_s)


def run_case(
    app: EditApp,
    spec: EditCaseSpec,
    *,
    plan_only: bool = False,
    timeout_s: float = 3600.0,
    poll_s: float = 5.0,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> CaseRun:
    started = monotonic()
    run = CaseRun(spec)
    try:
        run.parent = app.queries.get_version(spec.parent_version_id).version
        run.plan = app.plan_revision.execute(
            PlanRevisionCommand(
                version_id=spec.parent_version_id,
                change=spec.change,
                kind=spec.kind,
                keep=spec.keep,
            )
        )
        if not plan_only:
            revision = app.create_revision.execute(
                CreateRevisionCommand(
                    spec.parent_version_id,
                    run.plan.change,
                    kind=run.plan.kind,
                    keep=run.plan.keep,
                )
            )
            run.revision_version_id = revision.version_id
            run.changed_scene_keys = tuple(revision.changed_scene_keys)
            app.accept_generation.execute(revision.version_id)
            run.version = wait_for_version(
                app.queries,
                revision.version_id,
                timeout_s=timeout_s,
                poll_s=poll_s,
                sleep=sleep,
                monotonic=monotonic,
            )
            run.results = tuple(app.queries.get_checks(revision.version_id).results)
    except WaitTimeout as exc:
        run.version = exc.version
        run.error_code, run.error_message = exc.code, str(exc)
    except (DomainError, AdapterError) as exc:
        run.error_code, run.error_message = exc.code, str(exc)
    finally:
        run.elapsed_s = round(monotonic() - started, 3)
    return run


# ---- 판정 ----


def target_requirement_keys(run: CaseRun) -> frozenset[str]:
    """판정할 요구조건: 수정 버전 IR 에서 다시 만든 장면의 (대상 객체, 술어) 요구조건."""
    version = run.version
    if version is None or version.ir is None:
        return frozenset()
    change = run.plan.change if run.plan is not None else run.spec.change
    changed = set(run.changed_scene_keys)
    return frozenset(
        requirement.key
        for scene in version.ir.scenes
        if scene.scene_key in changed
        for requirement in scene.requirements
        if requirement.entity_key == change.entity_key and requirement.predicate == change.predicate
    )


def judge(run: CaseRun) -> CaseJudgement:
    keys = target_requirement_keys(run)
    change = run.spec.change
    cases = {
        basis: EditCase(
            run.spec.case_id,
            change.entity_key,
            change.predicate,
            frozenset(run.changed_scene_keys),
            select_preservation(run.results, basis),
            target_requirement_keys=keys,
        )
        for basis in PRESERVATION_BASES
    }
    return CaseJudgement(keys, cases, {basis: evaluate_case(c) for basis, c in cases.items()})


# ---- 보고서 ----


def build_report(
    runs: Sequence[CaseRun],
    *,
    basis: str,
    plan_only: bool,
    meta: Mapping[str, Any],
    complete: bool,
) -> dict[str, Any]:
    """결과 파일의 내용. 케이스마다 입력·계획·수정 버전·클립 URI·검수 결과(detail 포함)와 판정을 남긴다."""
    if basis not in PRESERVATION_BASES:
        raise ValueError(f"알 수 없는 측정 기준: {basis}")
    if plan_only:
        cases = [_planned_case(run) for run in runs]
        summary = _plan_summary(runs, basis)
    else:
        judgements = [judge(run) for run in runs]
        cases = [_case(run, j, basis) for run, j in zip(runs, judgements, strict=True)]
        summary = _summary(runs, judgements, basis)
    return {
        "tool": TOOL_NAME,
        "report_version": REPORT_VERSION,
        **meta,
        "plan_only": plan_only,
        "basis": basis,
        "targets": dict(TARGETS),
        "complete": complete,
        "summary": summary,
        "cases": cases,
    }


def _summary(
    runs: Sequence[CaseRun], judgements: Sequence[CaseJudgement], basis: str
) -> dict[str, Any]:
    by_basis = {}
    for name in PRESERVATION_BASES:
        evaluation = evaluate_cases([j.cases[name] for j in judgements])
        measured = sum(1 for j in judgements if j.outcomes[name].non_target_change_rate is not None)
        by_basis[name] = _evaluation(evaluation, measured)
    head = by_basis[basis]
    return {
        "cases": len(runs),
        "completed": sum(1 for r in runs if _state(r) is VersionState.COMPLETED),
        "failed": sum(1 for r in runs if _state(r) is VersionState.FAILED),
        "errors": sum(1 for r in runs if r.error_code is not None),
        "target_met": sum(1 for j in judgements if j.outcomes[basis].target_met),
        "basis": basis,
        "success_rate": head["success_rate"],
        "mean_non_target_change_rate": head["mean_non_target_change_rate"],
        "meets_target": head["meets_target"],
        "by_basis": by_basis,
        "recorded_cost_usd": _sum(_recorded_cost(r) for r in runs),
        "vlm_tokens": _add_tokens(_tokens(r.results) for r in runs),
        "elapsed_s": round(sum(r.elapsed_s for r in runs), 3),
    }


def _evaluation(evaluation: EditEvaluation, measured: int) -> dict[str, Any]:
    rate = evaluation.mean_non_target_change_rate
    return {
        "success_rate": round(evaluation.success_rate, 6),
        "mean_non_target_change_rate": None if rate is None else round(rate, 6),
        "meets_target": evaluation.meets_target,
        "measured_cases": measured,  # 비대상 변경률을 잴 수 있었던 케이스 (나머지는 미판정)
    }


def _case(run: CaseRun, judgement: CaseJudgement, basis: str) -> dict[str, Any]:
    version = run.version
    return {
        "case_id": run.spec.case_id,
        "input": run.spec.to_dict(),
        "plan": _plan(run.plan),
        "revision_version_id": run.revision_version_id,
        "state": str(version.state) if version is not None else None,
        "error": _error(run),
        "changed_scene_keys": list(run.changed_scene_keys),
        "target_requirement_keys": sorted(judgement.target_requirement_keys),
        "outcome": _outcome(judgement.outcomes[basis]),
        "by_basis": {name: _outcome(o) for name, o in judgement.outcomes.items()},
        "video_uri": version.video_uri if version is not None else None,
        "clips": _clips(run),
        "checks": [_check(r) for r in run.results],
        "recorded_cost_usd": _recorded_cost(run),
        "vlm_tokens": _tokens(run.results),
        "elapsed_s": run.elapsed_s,
    }


def _planned_case(run: CaseRun) -> dict[str, Any]:
    return {
        "case_id": run.spec.case_id,
        "input": run.spec.to_dict(),
        "plan": _plan(run.plan),
        "revision_version_id": None,
        "state": None,
        "error": _error(run),
        "elapsed_s": run.elapsed_s,
    }


def _plan_summary(runs: Sequence[CaseRun], basis: str) -> dict[str, Any]:
    plans = [r.plan for r in runs if r.plan is not None]
    regenerate = sum(len(p.target_scene_keys) for p in plans)
    edit = sum(p.edit_scene_count for p in plans)
    return {
        "cases": len(runs),
        "planned": len(plans),
        "errors": sum(1 for r in runs if r.error_code is not None),
        "regenerate_scenes": regenerate,
        "edit_scenes": edit,
        # 어림값은 생성기가 알려 준 장면(또는 지난 기록이 있는 장면)만 더한다. estimated_scenes 가 그 수다
        "estimated_cost_usd": _sum(p.estimated_cost_usd for p in plans),
        "estimated_seconds": _sum(p.estimated_seconds for p in plans),
        "estimated_scenes": sum(p.estimated_scene_count for p in plans),
        "basis": basis,
        "meets_target": None,
    }


def _plan(plan: RevisionPlan | None) -> dict[str, Any] | None:
    if plan is None:
        return None
    change = plan.change
    return {
        "kind": str(plan.kind),
        "change": {
            "entity_key": change.entity_key,
            "predicate": change.predicate,
            "new_value": change.new_value,
            "scope": str(change.scope),
            "scene_keys": list(change.scene_keys),
            "current_value": change.current_value,
        },
        "keep": [item.to_dict() for item in plan.keep],
        "previous_value": plan.previous_value,
        "target_scene_keys": list(plan.target_scene_keys),
        "estimated_cost_usd": plan.estimated_cost_usd,
        "estimated_seconds": plan.estimated_seconds,
        "edit_scene_count": plan.edit_scene_count,
        "scenes": [  # 다시 만들 장면: 지금 값·판정과, 지금 만들면 어떻게 만들어질지
            {
                "scene_key": s.scene_key,
                "current_value": s.current_value,
                "verdict": str(s.verdict) if s.verdict is not None else None,
                "production": _preview(s.production),
            }
            for s in plan.scenes
            if s.will_regenerate
        ],
    }


def _preview(preview: ProductionPreview | None) -> dict[str, Any] | None:
    if preview is None:
        return None
    return {
        "method": str(preview.method),
        "model": preview.model,
        "cost_usd": preview.cost_usd,
        "seconds": preview.seconds,
        "edit_unavailable_reason": preview.edit_unavailable_reason,
    }


def _outcome(outcome: EditCaseOutcome) -> dict[str, Any]:
    return {
        "success": outcome.success,
        "target_met": outcome.target_met,
        "non_target_change_rate": outcome.non_target_change_rate,
    }


def _error(run: CaseRun) -> dict[str, Any] | None:
    if run.error_code is not None:
        return {"code": run.error_code, "message": run.error_message}
    if run.version is not None and run.version.state is VersionState.FAILED:
        return {"code": run.version.error_code, "message": run.version.error_message}
    return None


def _state(run: CaseRun) -> VersionState | None:
    return run.version.state if run.version is not None else None


def _clips(run: CaseRun) -> list[dict[str, Any]]:
    """장면마다 수정 버전의 클립과 부모의 클립 (나란히 보며 판정을 확인할 수 있게)."""
    version = run.version
    if version is None or version.ir is None:
        return []
    parent_clips = run.parent.clips if run.parent is not None else {}
    changed = set(run.changed_scene_keys)
    clips = []
    for scene in version.ir.scenes:
        key = scene.scene_key
        clip, parent = version.clips.get(key), parent_clips.get(key)
        clips.append(
            {
                "scene_key": key,
                "uri": clip.uri if clip is not None else None,
                "parent_uri": parent.uri if parent is not None else None,
                "target": key in changed,  # 이 수정이 다시 만든 장면
                "reused": clip is not None and parent is not None and clip.uri == parent.uri,
                "production": _production(clip.metadata) if clip is not None else None,
            }
        )
    return clips


def _production(metadata: Mapping[str, Any]) -> dict[str, Any] | None:
    """생성기가 클립에 남긴 공통 기록 (화면의 clip_production 과 같은 규칙, presentation/mappers.py)."""
    if not metadata:
        return None
    edit = metadata.get("edit")
    edit = edit if isinstance(edit, Mapping) else {}
    applied = edit.get("applied") is True
    model, reason = metadata.get("model"), edit.get("fallback_reason")
    return {
        "method": "EDITED" if applied else "GENERATED",
        "model": model if isinstance(model, str) else None,
        "cost_usd": _number(metadata.get("cost_usd")),
        "seconds": _number(metadata.get("generation_seconds")),
        "edit_fallback_reason": reason if isinstance(reason, str) and not applied else None,
        "provisional": metadata.get("provisional") is True,
    }


def _recorded_cost(run: CaseRun) -> float | None:
    """이 수정이 다시 만든 클립에 생성기가 남긴 비용의 합. 캐시에서 다시 쓴 클립은 처음 만들 때의 값이다."""
    costs = [
        clip["production"]["cost_usd"]
        for clip in _clips(run)
        if clip["target"]
        and not clip["reused"]
        and clip["production"] is not None
        and clip["production"]["cost_usd"] is not None
    ]
    return round(sum(costs), 6) if costs else None


def _check(result: CheckResult) -> dict[str, Any]:
    return {
        "check_type": str(result.check_type),
        "verdict": str(result.verdict),
        "score": result.score,
        "requirement_key": result.requirement_key,
        "entity_key": result.entity_key,
        "scene_key": result.scene_key,
        "warnings": [
            {
                "scene_key": w.scene_key,
                "start_ms": w.start_ms,
                "end_ms": w.end_ms,
                "message": w.message,
            }
            for w in result.warnings
        ],
        "detail": dict(result.detail),
    }


def _tokens(results: Iterable[CheckResult]) -> dict[str, int]:
    """검수 VLM 호출의 토큰 수 (검수기는 호출마다 첫 결과에만 usage 를 남긴다)."""
    total = {"calls": 0, "input": 0, "output": 0}
    for result in results:
        usage = result.detail.get("usage")
        if not isinstance(usage, Mapping):
            continue
        total["calls"] += 1
        for field, name in (("input", "input_tokens"), ("output", "output_tokens")):
            value = usage.get(name)
            if isinstance(value, int) and not isinstance(value, bool):
                total[field] += value
    return total


def _add_tokens(items: Iterable[Mapping[str, int]]) -> dict[str, int]:
    total = {"calls": 0, "input": 0, "output": 0}
    for item in items:
        for field in total:
            total[field] += item[field]
    return total


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _sum(values: Iterable[float | None]) -> float | None:
    known = [v for v in values if v is not None]
    return round(sum(known), 6) if known else None
