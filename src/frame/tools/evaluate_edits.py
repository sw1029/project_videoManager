"""지표 1 평가 CLI (T12, ADR-0022): 단일 속성 수정 케이스를 차례로 실행해 성공률·비대상 변경률과 목표 판정을 남긴다.

    python -m frame.tools.evaluate_edits --cases cases.json --out result.json
        [--preservation vlm_keep|ssim] [--plan-only] [--overwrite] [--timeout-s 3600] [--poll-s 5]

- 서버를 띄우지 않는다. 같은 설정(FRAME_*)으로 컨테이너를 조립해 유스케이스를 직접 부른다 (인프로세스).
- 부모 버전이 저장된 DB·산출물 폴더를 가리켜야 한다:
  FRAME_PERSISTENCE=sqlalchemy · FRAME_DB_URL · FRAME_ARTIFACT_STORE=local_fs · FRAME_ARTIFACT_DIR.
  같은 DB 를 쓰는 서버는 끄고 돌린다 (SQLite 쓰기 잠금, 서버의 재시작 복구가 처리 중인 버전을 건드리지 않게).
- 작업은 인라인으로 돈다 (FRAME_JOBS 는 무시한다). 케이스 하나가 끝나야 다음 케이스를 시작한다.
- 결과 파일은 케이스가 끝날 때마다 다시 쓴다. 중간에 끊겨도 끝난 케이스는 남고 complete 가 false 다.
  Ctrl+C 로 멈추면 하던 케이스와 그 케이스에서 이미 만든 수정 버전 ID 를 interrupted 에 남긴다.
- 케이스 하나의 오류(예상 밖 오류 포함)는 그 케이스에 기록하고 다음 케이스로 간다.
- --plan-only 는 변경 계획(다시 만들 장면, 편집 여부, 어림 비용)만 보고 수정 버전을 만들지 않는다.
  실제 실행 전에 비용을 확인하는 용도다.
- 결과 파일을 쓰지 못해도 실행은 계속한다 (다음 케이스 뒤에 다시 쓴다). 마지막까지 못 쓰면 같은 폴더의
  <이름>.fallback.json, 그다음 임시 폴더에 쓰고 종료 코드 3 으로 알린다.
- --out 이 이미 있으면 앞 결과(과금된 실행)를 지키려고 아무것도 돌리지 않는다 (종료 코드 2, 덮어쓰려면 --overwrite).
  Ctrl+C 로 멈추면 하던 케이스와 시작하지 않은 케이스만 담은 <이름>.remaining.json 을 남긴다.
  이어 돌릴 때 그 파일을 --cases 로, 다른 파일을 --out 으로 주면 끝난 케이스의 검수를 다시 과금하지 않는다.

종료 코드: 0 = 끝까지 돌고 결과를 남김 (목표 달성 여부는 결과에), 2 = 케이스 파일·설정 오류 (아무것도 실행하지 않음),
3 = 결과 파일을 --out 에 쓰지 못함 (쓴 곳을 알린다), 130 = Ctrl+C 로 멈춤 (그때까지의 결과를 남김).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from frame.application.errors import AdapterError, ConfigurationError
from frame.config import Container, Settings, build_container
from frame.domain.checks import PRESERVATION_BY_KEEP, PRESERVATION_BY_SSIM
from frame.domain.enums import VersionState
from frame.evaluation.edit_cases import CaseFileError, EditCaseSpec, load_cases
from frame.evaluation.edit_metrics import (
    NON_TARGET_CHANGE_TARGET,
    PRESERVATION_BASES,
    SUCCESS_RATE_TARGET,
)
from frame.evaluation.edit_report import build_report, judge, left_unfinished
from frame.evaluation.edit_runner import (
    DEFAULT_CASE_TIMEOUT_S,
    DEFAULT_POLL_S,
    CaseInterrupted,
    CaseRun,
    run_case,
)

# 측정 기준(detail.method)을 내는 보존 검수기 (FRAME_CHECKERS 의 키)
_BASIS_CHECKERS = {
    PRESERVATION_BY_KEEP: "vlm_preservation",
    PRESERVATION_BY_SSIM: "ssim_preservation",
}

# 종료 코드 (머리말의 설명과 같다)
EXIT_DONE = 0
EXIT_NOTHING_RUN = (
    2  # 케이스 파일 · 설정 오류이거나 결과 파일이 이미 있다: 아무것도 실행하지 않았다
)
EXIT_NOT_WRITTEN = 3  # 결과를 --out 에 쓰지 못해 다른 이름으로 썼다
EXIT_INTERRUPTED = 130  # Ctrl+C 로 멈췄다 (셸의 관례: 128 + SIGINT 의 번호 2)

# 결과에 남기는 설정 (재현용). 결과 파일은 저장소에 기록하므로(TDD_WORKFLOW §7) DB 주소(계정이 들어갈 수 있음)·
# 키 파일 경로·이 컴퓨터의 폴더(산출물 폴더는 종류 artifact_store 만, 케이스 파일은 이름만)는 남기지 않는다
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
)


def main(
    argv: Sequence[str] | None = None,
    *,
    container: Container | None = None,
    env: Mapping[str, str] | None = None,
) -> int:
    """container 를 주면 그것을 쓴다 (테스트). 아니면 env(기본 os.environ)의 FRAME_* 로 조립한다."""
    args = _parser().parse_args(argv)
    out = Path(args.out)
    if out.exists() and not args.overwrite:
        print(
            f"결과 파일이 이미 있습니다: {out}. 앞 결과를 잃지 않게 아무것도 돌리지 않습니다. 덮어쓰려면 "
            "--overwrite, 멈춘 실행을 이어 돌리려면 <이름>.remaining.json 을 --cases 로, 다른 파일을 --out 으로 주세요",
            file=sys.stderr,
        )
        return EXIT_NOTHING_RUN
    try:
        specs = load_cases(args.cases)
    except CaseFileError as exc:
        print(f"케이스 파일 오류: {exc}", file=sys.stderr)
        return EXIT_NOTHING_RUN
    if container is None:
        container = _build(env)
        if container is None:
            return EXIT_NOTHING_RUN

    clock = container.clock
    meta: dict[str, Any] = {
        "cases_file": Path(args.cases).name,
        "started_at": _iso(clock.now()),
        "settings": _settings(container.settings),
        "checkers": [type(checker).__name__ for checker in container.checkers],
    }
    mode = "변경 계획만" if args.plan_only else f"측정 기준 {args.preservation}"
    print(f"[evaluate_edits] 케이스 {len(specs)}건, {mode}", file=sys.stderr)
    runs: list[CaseRun] = []

    def report(*, complete: bool, interrupted: CaseRun | None = None) -> dict[str, Any]:
        return build_report(
            runs,
            basis=args.preservation,
            plan_only=args.plan_only,
            meta={**meta, "updated_at": _iso(clock.now())},
            complete=complete,
            interrupted=interrupted,
        )

    try:
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
            if index < len(specs):  # 마지막 케이스 뒤에는 아래에서 다른 이름까지 시도하며 쓴다
                _save(out, report(complete=False))
            print(f"[{index}/{len(specs)}] {_progress(run, args.plan_only)}", file=sys.stderr)
    except KeyboardInterrupt as exc:
        interrupted = exc.run if isinstance(exc, CaseInterrupted) else None
        # 마지막 케이스까지 끝난 뒤(진행 줄을 찍는 사이)에 온 Ctrl+C 면 결과는 완성돼 있다
        complete = len(runs) == len(specs)
        written = _save_final(out, report(complete=complete, interrupted=interrupted))
        remaining = _save_remaining(out, Path(args.cases).name, specs[len(runs) :])
        print(
            _interrupted_text(len(runs), len(specs), interrupted, written, remaining),
            file=sys.stderr,
        )
        return EXIT_INTERRUPTED

    final = report(complete=True)
    written = _save_final(out, final)
    print(_summary_text(final, written))
    return EXIT_DONE if written == out else EXIT_NOT_WRITTEN


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m frame.tools.evaluate_edits",
        # 설명문은 %(prog) 가 없으면 % 포맷을 거치지 않으므로 % 를 한 번만 쓴다
        description=(
            f"지표 1 평가: 단일 속성 수정의 성공률(목표 {SUCCESS_RATE_TARGET:.0%} 이상)과 "
            f"비대상 변경률(목표 {NON_TARGET_CHANGE_TARGET:.0%} 이하)"
        ),
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
        default=PRESERVATION_BY_KEEP,
        help="목표 판정에 쓰는 비대상 변경률의 기준 (두 기준 모두 결과에 남는다). "
        "vlm_keep: vlm_preservation 의 유지 요소 판정, ssim: ssim_preservation 의 부모 클립 대비 SSIM",
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="변경 계획(다시 만들 장면·편집 여부·어림 비용)만 보고 수정 버전은 만들지 않는다",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="결과 파일이 이미 있어도 덮어쓴다 (없으면 앞 결과를 지키려고 아무것도 돌리지 않는다)",
    )
    parser.add_argument(
        "--timeout-s",
        type=float,
        default=DEFAULT_CASE_TIMEOUT_S,
        help="케이스 하나가 끝나기를 기다리는 시간 (초)",
    )
    parser.add_argument(
        "--poll-s", type=float, default=DEFAULT_POLL_S, help="끝났는지 다시 읽는 간격 (초)"
    )
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


def _save(out: Path, report: Mapping[str, Any]) -> None:
    """케이스 사이의 저장. 못 쓰면 경고만 한다 (기록은 메모리에 있고 다음 케이스 뒤에 다시 쓴다)."""
    try:
        _write_json(out, report)
    except OSError as exc:
        print(
            f"주의: 결과 파일을 쓰지 못했습니다 ({type(exc).__name__}: {exc}). "
            "기록은 남아 있고 다음 케이스 뒤에 다시 씁니다",
            file=sys.stderr,
        )


def _save_final(out: Path, report: Mapping[str, Any]) -> Path | None:
    """마지막 저장. --out 에 못 쓰면 같은 폴더, 그다음 임시 폴더에 다른 이름으로 쓴다 (과금된 결과를 잃지 않게).

    쓴 곳을 돌려준다. 모두 실패하면 None.
    """
    fallback = f"{out.stem}.fallback{out.suffix}"
    for path in (out, out.with_name(fallback), Path(tempfile.gettempdir()) / fallback):
        try:
            _write_json(path, report)
        except OSError as exc:
            print(
                f"주의: 결과를 {path} 에 쓰지 못했습니다 ({type(exc).__name__}: {exc})",
                file=sys.stderr,
            )
            continue
        if path != out:
            print(f"주의: 결과를 {out} 대신 {path} 에 썼습니다", file=sys.stderr)
        return path
    print("오류: 결과를 어디에도 쓰지 못했습니다", file=sys.stderr)
    return None


def _write_json(path: Path, data: Mapping[str, Any]) -> None:
    """임시 파일에 쓴 뒤 바꿔 끼운다 (쓰는 도중에 끊겨도 앞의 파일이 깨지지 않게)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _save_remaining(out: Path, cases_file: str, remaining: Sequence[EditCaseSpec]) -> Path | None:
    """멈춘 뒤 남은 케이스(하던 케이스 포함)를 케이스 파일 형식으로 쓴다. 이어 돌릴 때 --cases 로 준다."""
    if not remaining:
        return None
    path = out.with_name(f"{out.stem}.remaining{out.suffix}")
    data = {
        "description": f"{cases_file} 의 실행({out.name})이 멈춘 뒤 남은 케이스",
        "cases": [spec.to_dict() for spec in remaining],
    }
    try:
        _write_json(path, data)
    except OSError as exc:
        print(
            f"주의: 남은 케이스 파일을 쓰지 못했습니다 ({type(exc).__name__}: {exc})",
            file=sys.stderr,
        )
        return None
    return path


def _iso(value: datetime) -> str:
    return value.isoformat()


def _percent(value: float | None) -> str:
    return "미판정" if value is None else f"{value * 100:.1f}%"


def _progress(run: CaseRun, plan_only: bool) -> str:
    head = f"{run.spec.case_id}:"
    plan = run.plan
    # run_case 는 계획 없이 끝난 케이스를 늘 오류로 남긴다. plan 조건은 아래에서 계획을 쓰기 위한 것이다
    if run.error_code is not None or plan is None:
        line = f"{head} 오류 {run.error_code}: {run.error_message} ({run.elapsed_s:.1f}s)"
        if left_unfinished(run):
            line += f" · {_unfinished_text(run)}"
        return line
    if plan_only:
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


def _interrupted_text(
    finished: int,
    total: int,
    interrupted: CaseRun | None,
    written: Path | None,
    remaining: Path | None,
) -> str:
    if written is None:
        lines = [f"중단: 끝난 케이스 {finished}/{total}건의 결과를 쓰지 못했습니다 (위 오류 참고)"]
    else:
        lines = [f"중단: 끝난 케이스 {finished}/{total}건을 결과에 남겼습니다 — {written}"]
    if interrupted is not None:
        lines.append(_interrupted_case_text(interrupted))
    if remaining is not None:
        lines.append(
            f"이어 돌리려면 --cases {remaining} --out <다른 결과 파일> 로 실행하세요 "
            "(끝난 케이스는 다시 돌지 않아 그 검수를 다시 과금하지 않습니다)"
        )
    return "\n".join(lines)


def _interrupted_case_text(run: CaseRun) -> str:
    case_id, revision, version = run.spec.case_id, run.revision_version_id, run.version
    if revision is None:
        return f"케이스 {case_id} 는 수정 버전을 만들기 전에 멈췄습니다"
    if left_unfinished(run):
        return f"케이스 {case_id}: {_unfinished_text(run)}"
    if version is None:  # 다시 읽지 못함
        return (
            f"케이스 {case_id} 의 수정 버전 {revision} 의 상태를 읽지 못했습니다. 생성 접수까지 갔다면 같은 DB 로 "
            "서버를 켤 때 재시작 복구가 이어서 실행합니다 (이미 과금된 작업은 다시 제출하지 않습니다)"
        )
    if version.state is VersionState.DRAFT:
        return f"케이스 {case_id} 의 수정 버전 {revision} 은 생성 접수 전에 멈춰 서버가 다시 실행하지 않습니다"
    return f"케이스 {case_id} 의 수정 버전 {revision} 은 {version.state} 로 끝났습니다"


def _unfinished_text(run: CaseRun) -> str:
    """접수된 채 끝나지 않은 수정 버전 안내 (left_unfinished 인 케이스)."""
    state = run.version.state if run.version is not None else "-"
    return (
        f"수정 버전 {run.revision_version_id} 이 {state} 로 남았습니다. 같은 DB 로 서버를 켜면 재시작 복구가 "
        "이어서 실행합니다 (남은 생성·검수가 과금될 수 있습니다)"
    )


def _summary_text(report: Mapping[str, Any], written: Path | None) -> str:
    summary = report["summary"]
    if report["plan_only"]:
        cost = summary["estimated_cost_usd"]
        seconds = summary["estimated_seconds"]
        lines = [
            (
                f"변경 계획 {summary['planned']}/{summary['cases']}건 (오류 {summary['errors']}): "
                f"다시 만들 장면 {summary['regenerate_scenes']} (편집 {summary['edit_scenes']})"
            ),
            "어림 비용 "
            + ("알 수 없음" if cost is None else f"${cost:.3f}")
            + (" · 어림 시간 " + ("알 수 없음" if seconds is None else f"{seconds:.0f}초"))
            + f" (장면 {summary['estimated_scenes']}개 기준, 검수 VLM 비용 제외)",
        ]
    else:
        targets = report["targets"]
        verdict = "목표 달성" if summary["meets_target"] else "목표 미달"
        lines = [
            (
                f"지표 1 평가 {summary['cases']}건 (완료 {summary['completed']} · 실행 실패 "
                f"{summary['failed']} · 오류 {summary['errors']})"
            ),
            (
                f"기준 {summary['basis']}: 성공률 {_percent(summary['success_rate'])} "
                f"(목표 {targets['success_rate'] * 100:.0f}% 이상) · 평균 비대상 변경률 "
                f"{_percent(summary['mean_non_target_change_rate'])} "
                f"(목표 {targets['non_target_change_rate'] * 100:.0f}% 이하) → {verdict}"
            ),
        ]
        for basis, item in summary["by_basis"].items():
            lines.append(
                f"  {basis}: 성공률 {_percent(item['success_rate'])} · 비대상 변경률 "
                f"{_percent(item['mean_non_target_change_rate'])} "
                f"(잰 케이스 {item['measured_cases']}) · {'달성' if item['meets_target'] else '미달'}"
            )
        measured = summary["by_basis"][summary["basis"]]["measured_cases"]
        # 검수 결과를 읽은 케이스가 있는데도 그 기준의 보존 결과가 없을 때만 (읽지 못한 것은 오류로 따로 보인다)
        checks_read = any(case["checks"] for case in report["cases"])
        if summary["completed"] and checks_read and not measured:
            checker = _BASIS_CHECKERS[summary["basis"]]
            lines.append(
                f"주의: 기준 {summary['basis']} 의 보존 결과가 하나도 없습니다. "
                f"FRAME_CHECKERS 에 {checker} 가 있는지 확인하세요"
            )
        unfinished = summary["unfinished_versions"]
        if unfinished:
            ids = ", ".join(f"{u['revision_version_id']}({u['state']})" for u in unfinished)
            lines.append(
                f"주의: 오류로 끝난 케이스의 수정 버전 {len(unfinished)}개가 접수된 채 남았습니다: {ids}. "
                "같은 DB 로 서버를 켜면 재시작 복구가 이어서 실행합니다 (남은 생성·검수가 과금될 수 있습니다)"
            )
    lines.append(
        f"결과: {written}" if written is not None else "결과 파일을 쓰지 못했습니다 (위 오류 참고)"
    )
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
