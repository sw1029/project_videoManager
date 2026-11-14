"""지표 1 평가 실행기 (T12, ADR-0022): 케이스 하나를 실행하고 그 기록(CaseRun)을 남긴다.

케이스마다: 변경 계획 → 수정 버전 생성 → 생성 접수 → 완료 대기 → 검수 결과 수집.
- 화면이 하는 순서를 그대로 밟는다 (연구 문서 §4·F1). 계획을 먼저 보고, 계획이 정한 종류·변경·유지 요소로
  수정 버전을 만든다. 같은 프로세스에서 계획을 보므로 생성기가 계획 때 받아 둔 부모 주소로 편집할 수 있다 (ADR-0020).
- 케이스 하나의 실패(부모 없음, 검증 오류, 생성 실패, 시간 초과, 예상 밖 오류)는 그 케이스에 기록하고
  다음 케이스로 간다. 예상 밖 오류(DB 잠금 등)는 UNEXPECTED_ERROR 로 남기고 트레이스백은 로그로 낸다.
  Ctrl+C 는 막지 않는다. 그때까지의 기록을 담은 CaseInterrupted(KeyboardInterrupt 하위)로 올려 보낸다.
- 수정 버전을 만든 뒤 오류·Ctrl+C 로 끝나면 그 버전을 한 번 더 읽어 마지막 상태를 남긴다
  (접수된 채 끝나지 않은 버전은 서버를 켜면 다시 실행되므로 찾을 수 있어야 한다).
- 기록을 판정하고 결과 파일의 내용을 만드는 일은 edit_report 가 한다.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from frame.application.dto import CreateRevisionCommand
from frame.application.errors import AdapterError
from frame.application.use_cases import (
    AcceptGeneration,
    CreateRevision,
    PlanRevision,
    PlanRevisionCommand,
    ProjectQueries,
    RevisionPlan,
)
from frame.domain.checks import CheckResult
from frame.domain.errors import DomainError
from frame.domain.models import VideoVersion
from frame.domain.state_machine import TERMINAL_STATES

from .edit_cases import EditCaseSpec

log = logging.getLogger(__name__)


class EditApp(Protocol):
    """평가가 부르는 유스케이스. config.Container 가 그대로 맞는다.

    evaluation 은 domain·application 만 import 한다. 컨테이너(config) 조립은 부르는 쪽(tools)이 한다.
    """

    plan_revision: PlanRevision
    create_revision: CreateRevision
    accept_generation: AcceptGeneration
    queries: ProjectQueries


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


class CaseInterrupted(KeyboardInterrupt):
    """케이스를 실행하던 중의 Ctrl+C. 그때까지의 기록(이미 만든 수정 버전 ID 등)을 run 에 담아 올라간다.

    KeyboardInterrupt 의 하위 클래스라, 이것을 모르는 쪽에서도 그대로 Ctrl+C 로 잡힌다.
    """

    def __init__(self, run: CaseRun) -> None:
        super().__init__(run.spec.case_id)
        self.run = run


def wait_for_version(
    queries: ProjectQueries,
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
        # 계약 위반. 두 실행(--plan-only 와 일반)이 같은 길로 이 케이스의 오류가 되게 여기서 거른다
        if run.plan is None:
            raise RuntimeError("변경 계획을 돌려받지 못했습니다 (PlanRevision 계약 위반)")
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
    except KeyboardInterrupt as exc:
        raise CaseInterrupted(run) from exc
    except WaitTimeout as exc:
        run.version = exc.version
        run.error_code, run.error_message = exc.code, str(exc)
    except (DomainError, AdapterError) as exc:
        run.error_code, run.error_message = exc.code, str(exc)
    except Exception as exc:  # noqa: BLE001 - 한 케이스의 예상 밖 오류가 남은 유료 케이스를 막지 않게 한다
        log.exception("케이스 %s 실행 중 예상하지 못한 오류", spec.case_id)
        # 코드 이름은 생성 파이프라인이 같은 경우에 남기는 것과 맞춘다 (application/pipeline/runner.py)
        run.error_code, run.error_message = "UNEXPECTED_ERROR", f"{type(exc).__name__}: {exc}"
    finally:
        if run.revision_version_id is not None and run.version is None:
            run.version = _read_again(app.queries, run.revision_version_id)
        run.elapsed_s = round(monotonic() - started, 3)
    return run


def _read_again(queries: ProjectQueries, version_id: str) -> VideoVersion | None:
    """오류(또는 Ctrl+C)로 끝난 케이스의 수정 버전을 한 번 더 읽는다.

    상태·클립·비용을 결과에 남기고, 접수된 채 끝나지 않은 버전(같은 DB 로 서버를 켜면 재시작 복구가
    이어서 실행한다, ADR-0013)을 알리기 위해서다. 읽지 못하면 None 이고 케이스의 원래 오류는 그대로 둔다.
    """
    try:
        return queries.get_version(version_id).version
    except Exception:  # noqa: BLE001 - 기록을 보태려는 읽기라, 실패해도 원래 오류를 덮지 않는다
        log.warning("수정 버전 %s 을 다시 읽지 못했습니다", version_id, exc_info=True)
        return None
