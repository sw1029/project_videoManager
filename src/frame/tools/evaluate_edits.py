"""지표 1 평가 CLI (T12, ADR-0022): 단일 속성 수정 케이스를 차례로 실행해 성공률·비대상 변경률과 목표 판정을 남긴다.

    python -m frame.tools.evaluate_edits --cases cases.json --out result.json
        [--preservation vlm_keep|ssim] [--plan-only] [--timeout-s 3600] [--poll-s 5]

- 서버를 띄우지 않는다. 같은 설정(FRAME_*)으로 컨테이너를 조립해 유스케이스를 직접 부른다 (인프로세스).
- 부모 버전이 저장된 DB·산출물 폴더를 가리켜야 한다:
  FRAME_PERSISTENCE=sqlalchemy · FRAME_DB_URL · FRAME_ARTIFACT_STORE=local_fs · FRAME_ARTIFACT_DIR.
  같은 DB 를 쓰는 서버는 끄고 돌린다 (SQLite 쓰기 잠금, 서버의 재시작 복구가 처리 중인 버전을 건드리지 않게).
- 작업은 인라인으로 돈다 (FRAME_JOBS 는 무시한다). 케이스 하나가 끝나야 다음 케이스를 시작한다.
- 결과 파일은 케이스가 끝날 때마다 다시 쓴다. 중간에 끊겨도 끝난 케이스는 남고 complete 가 false 다.
- --plan-only 는 변경 계획(다시 만들 장면, 편집 여부, 어림 비용)만 보고 수정 버전을 만들지 않는다.
  실제 실행 전에 비용을 확인하는 용도다.

종료 코드: 0 = 끝까지 돌고 결과를 남김 (목표 달성 여부는 결과에), 2 = 케이스 파일·설정 오류 (아무것도 실행하지 않음).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from frame.application.errors import AdapterError, ConfigurationError
from frame.config import Container, Settings, build_container
from frame.evaluation.edit_cases import CaseFileError, load_cases
from frame.evaluation.edit_metrics import PRESERVATION_BASES
from frame.evaluation.edit_runner import CaseRun, build_report, judge, run_case

# 측정 기준(detail.method)을 내는 보존 검수기 (FRAME_CHECKERS 의 키)
_BASIS_CHECKERS = {"vlm_keep": "vlm_preservation", "ssim": "ssim_preservation"}

# 결과에 남기는 설정 (재현용). DB 주소(계정이 들어갈 수 있음)와 키 파일 경로는 남기지 않는다
_RECORDED_SETTINGS = (
    "structurer",
    "change_interpreter",
    "generator",
    "assembler",
    "tracker",
    "checkers",
    "identity",
    "llm_provider",
    "llm_model",
    "vlm_model",
    "video_model",
    "video_resolution",
    "video_aspect_ratio",
    "video_edit_mode",
    "video_edit_model",
    "video_edit_parent_wait_s",
    "generator_cache",
    "generator_retries",
    "requirement_frames",
    "requirement_threshold",
    "preservation_frames",
    "preservation_threshold",
    "consistency_frames",
    "consistency_threshold",
    "persistence",
    "artifact_store",
    "artifact_dir",
)


def main(
    argv: Sequence[str] | None = None,
    *,
    container: Container | None = None,
    env: Mapping[str, str] | None = None,
) -> int:
    """container 를 주면 그것을 쓴다 (테스트). 아니면 env(기본 os.environ)의 FRAME_* 로 조립한다."""
    args = _parser().parse_args(argv)
    try:
        specs = load_cases(args.cases)
    except CaseFileError as exc:
        print(f"케이스 파일 오류: {exc}", file=sys.stderr)
        return 2
    if container is None:
        container = _build(env)
        if container is None:
            return 2

    out = Path(args.out)
    meta: dict[str, Any] = {
        "cases_file": str(args.cases),
        "started_at": _iso(container.clock.now()),
        "settings": _settings(container.settings),
        "checkers": [type(checker).__name__ for checker in container.checkers],
    }
    mode = "변경 계획만" if args.plan_only else f"측정 기준 {args.preservation}"
    print(f"[evaluate_edits] 케이스 {len(specs)}건, {mode}", file=sys.stderr)
    runs: list[CaseRun] = []
    for index, spec in enumerate(specs, start=1):
        print(f"[{index}/{len(specs)}] {spec.case_id}: 실행 중 …", file=sys.stderr)
        run = run_case(
            container,
            spec,
            plan_only=args.plan_only,
            timeout_s=args.timeout_s,
            poll_s=args.poll_s,
        )
        runs.append(run)
        report = build_report(
            runs,
            basis=args.preservation,
            plan_only=args.plan_only,
            meta={**meta, "updated_at": _iso(container.clock.now())},
            complete=index == len(specs),
        )
        _write(out, report)
        print(f"[{index}/{len(specs)}] {_progress(run, args.plan_only)}", file=sys.stderr)

    print(_summary_text(report, out))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m frame.tools.evaluate_edits",
        description="지표 1 평가: 단일 속성 수정의 성공률(목표 80%% 이상)과 비대상 변경률(목표 10%% 이하)",
    )
    parser.add_argument(
        "--cases",
        required=True,
        help="케이스 파일 (JSON). 예시: docs/evaluation/edit_cases.example.json",
    )
    parser.add_argument("--out", required=True, help="결과 파일 (JSON). 폴더가 없으면 만든다")
    parser.add_argument(
        "--preservation",
        choices=PRESERVATION_BASES,
        default="vlm_keep",
        help="목표 판정에 쓰는 비대상 변경률의 기준 (두 기준 모두 결과에 남는다). "
        "vlm_keep: vlm_preservation 의 유지 요소 판정, ssim: ssim_preservation 의 부모 클립 대비 SSIM",
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="변경 계획(다시 만들 장면·편집 여부·어림 비용)만 보고 수정 버전은 만들지 않는다",
    )
    parser.add_argument(
        "--timeout-s", type=float, default=3600.0, help="케이스 하나가 끝나기를 기다리는 시간 (초)"
    )
    parser.add_argument("--poll-s", type=float, default=5.0, help="끝났는지 다시 읽는 간격 (초)")
    return parser


def _build(env: Mapping[str, str] | None) -> Container | None:
    settings = replace(Settings.from_env(env), jobs="inline")
    if settings.persistence == "memory":
        print(
            "설정 오류: FRAME_PERSISTENCE=memory 로는 부모 버전을 찾을 수 없습니다. 원본을 만든 서버와 같은 "
            "FRAME_PERSISTENCE=sqlalchemy · FRAME_DB_URL · FRAME_ARTIFACT_STORE · FRAME_ARTIFACT_DIR 로 실행하세요",
            file=sys.stderr,
        )
        return None
    if settings.artifact_store == "memory":
        print(
            "주의: FRAME_ARTIFACT_STORE=memory 면 부모 클립을 읽지 못해 실제 보존 검수가 미판정이 됩니다 "
            "(서버와 같은 local_fs·FRAME_ARTIFACT_DIR 를 쓰세요)",
            file=sys.stderr,
        )
    try:
        return build_container(settings)
    except (AdapterError, ConfigurationError) as exc:
        print(f"설정 오류: {exc}", file=sys.stderr)
        return None


def _settings(settings: Settings) -> dict[str, Any]:
    recorded = {}
    for name in _RECORDED_SETTINGS:
        value = getattr(settings, name)
        recorded[name] = list(value) if isinstance(value, tuple) else value
    return recorded


def _write(path: Path, report: Mapping[str, Any]) -> None:
    """임시 파일에 쓴 뒤 바꿔 끼운다 (쓰는 도중에 끊겨도 앞의 결과 파일이 깨지지 않게)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _percent(value: float | None) -> str:
    return "미판정" if value is None else f"{value * 100:.1f}%"


def _progress(run: CaseRun, plan_only: bool) -> str:
    head = f"{run.spec.case_id}:"
    if run.error_code is not None:
        return f"{head} 오류 {run.error_code}: {run.error_message} ({run.elapsed_s:.1f}s)"
    if plan_only:
        plan = run.plan
        assert plan is not None
        cost = "-" if plan.estimated_cost_usd is None else f"${plan.estimated_cost_usd:.3f}"
        return (
            f"{head} 계획 {plan.kind} · 다시 만들 장면 {len(plan.target_scene_keys)} "
            f"(편집 {plan.edit_scene_count}) · 어림 비용 {cost}"
        )
    judgement = judge(run)
    state = run.version.state if run.version is not None else "-"
    rates = " · ".join(
        f"{basis} {_percent(outcome.non_target_change_rate)}"
        for basis, outcome in judgement.outcomes.items()
    )
    met = "대상 반영" if any(o.target_met for o in judgement.outcomes.values()) else "대상 미반영"
    return f"{head} {state} · {met} · 비대상 변경률 {rates} ({run.elapsed_s:.1f}s)"


def _summary_text(report: Mapping[str, Any], out: Path) -> str:
    summary = report["summary"]
    if report["plan_only"]:
        cost = summary["estimated_cost_usd"]
        seconds = summary["estimated_seconds"]
        lines = [
            f"변경 계획 {summary['planned']}/{summary['cases']}건 (오류 {summary['errors']}): "
            f"다시 만들 장면 {summary['regenerate_scenes']} (편집 {summary['edit_scenes']})",
            "어림 비용 "
            + ("알 수 없음" if cost is None else f"${cost:.3f}")
            + (" · 어림 시간 " + ("알 수 없음" if seconds is None else f"{seconds:.0f}초"))
            + f" (장면 {summary['estimated_scenes']}개 기준, 검수 VLM 비용 제외)",
        ]
    else:
        targets = report["targets"]
        verdict = "목표 달성" if summary["meets_target"] else "목표 미달"
        lines = [
            f"지표 1 평가 {summary['cases']}건 (완료 {summary['completed']} · 실행 실패 "
            f"{summary['failed']} · 오류 {summary['errors']})",
            f"기준 {summary['basis']}: 성공률 {_percent(summary['success_rate'])} "
            f"(목표 {targets['success_rate'] * 100:.0f}% 이상) · 평균 비대상 변경률 "
            f"{_percent(summary['mean_non_target_change_rate'])} "
            f"(목표 {targets['non_target_change_rate'] * 100:.0f}% 이하) → {verdict}",
        ]
        for basis, item in summary["by_basis"].items():
            lines.append(
                f"  {basis}: 성공률 {_percent(item['success_rate'])} · 비대상 변경률 "
                f"{_percent(item['mean_non_target_change_rate'])} "
                f"(잰 케이스 {item['measured_cases']}) · {'달성' if item['meets_target'] else '미달'}"
            )
        if summary["completed"] and not summary["by_basis"][summary["basis"]]["measured_cases"]:
            checker = _BASIS_CHECKERS[summary["basis"]]
            lines.append(
                f"주의: 기준 {summary['basis']} 의 보존 결과가 하나도 없습니다. "
                f"FRAME_CHECKERS 에 {checker} 가 있는지 확인하세요"
            )
    lines.append(f"결과: {out}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
