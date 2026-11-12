"""재시작 복구 (#2, ADR-0013): 지난 프로세스가 남긴 버전을 다시 디스패치하거나 명확한 오류로 끝낸다.

in-process 디스패처(inline·thread·recording)는 작업을 프로세스 메모리에만 둔다. 서버가 다시 시작되면
- QUEUED : 작업이 사라졌다 -> 다시 디스패치 (기동 때)
- STRUCTURING : 구조화 작업이 사라졌다 -> 다시 디스패치 (기동 때, 구조화는 멱등)
- PROCESSING : 워커가 사라졌다 -> 시도 횟수가 상한보다 작으면 QUEUED 로 되돌려 다시 실행, 상한이면 FAILED(WORKER_LOST)

기동 때는 이 DB 를 쓰는 서버 프로세스가 하나라는 전제(in-process 디스패처의 전제)로 PROCESSING 을 모두 회수한다.
실행 중에는 주기 검사가 리스(lease_seconds)를 넘긴 PROCESSING 만 회수한다 (멈춘 워커). QUEUED·STRUCTURING 은
이 프로세스의 대기열에서 차례를 기다리는 중일 수 있어 주기 검사에서는 건드리지 않는다.

다시 실행해도 이미 과금된 외부 작업은 생성 캐시·작업 원장으로 이어 받으므로 다시 제출하지 않는다.
상태 변경은 조건부 갱신(try_transition)이라 그 사이 워커가 끝낸 버전은 건드리지 않고, 작업은 커밋 뒤에 넘긴다.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime

from frame.domain.enums import VersionState
from frame.domain.models import VideoVersion

from ..errors import ConfigurationError
from ..jobs import RUN_GENERATION_PIPELINE, STRUCTURE_REQUIREMENTS
from ..ports.common import Clock
from ..ports.jobs import Job, JobDispatcher
from ..ports.unit_of_work import UnitOfWorkFactory

log = logging.getLogger(__name__)

WORKER_LOST = "WORKER_LOST"


@dataclass(frozen=True)
class RecoveryReport:
    redispatched: tuple[str, ...] = ()  # QUEUED: 생성 작업을 다시 넣음 (기동)
    requeued: tuple[str, ...] = ()  # 유실된 PROCESSING: QUEUED 로 되돌려 다시 실행
    failed: tuple[str, ...] = ()  # 시도 상한에 닿은 PROCESSING: FAILED(WORKER_LOST)
    restructured: tuple[str, ...] = ()  # STRUCTURING: 구조화 작업을 다시 넣음 (기동)

    @property
    def total(self) -> int:
        return (
            len(self.redispatched) + len(self.requeued) + len(self.failed) + len(self.restructured)
        )


class RecoverPendingVersions:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        dispatcher: JobDispatcher,
        clock: Clock,
        *,
        lease_seconds: float,
        max_attempts: int,
        reclaim_processing_on_startup: bool = True,
    ) -> None:
        if lease_seconds <= 0:
            raise ConfigurationError("FRAME_WORKER_LEASE_S 는 0 보다 커야 합니다")
        if max_attempts < 1:
            raise ConfigurationError("FRAME_WORKER_MAX_ATTEMPTS 는 1 이상이어야 합니다")
        self._uow_factory = uow_factory
        self._dispatcher = dispatcher
        self._clock = clock
        self.lease_seconds = float(lease_seconds)
        self.max_attempts = int(max_attempts)
        # 외부 큐(Celery 등)를 붙이면 다른 프로세스의 워커가 살아 있을 수 있으므로 False 로 두고 리스만 쓴다
        self._reclaim_on_startup = reclaim_processing_on_startup
        self._lock = threading.Lock()  # 기동 복구와 주기 검사가 겹치지 않게

    def execute(self, *, startup: bool = False) -> RecoveryReport:
        with self._lock:
            now = self._clock.now()
            requeued, failed = self._reclaim_lost_workers(now, startup)
            redispatched: tuple[str, ...] = ()
            restructured: tuple[str, ...] = ()
            if startup:
                redispatched = self._redispatch(
                    VersionState.QUEUED, RUN_GENERATION_PIPELINE, skip=set(requeued)
                )
                restructured = self._redispatch(VersionState.STRUCTURING, STRUCTURE_REQUIREMENTS)
        report = RecoveryReport(redispatched, requeued, failed, restructured)
        if report.total:
            log.warning(
                "%s 복구: QUEUED 다시 디스패치 %d, PROCESSING 재시도 %d, WORKER_LOST %d, 구조화 다시 시작 %d",
                "기동" if startup else "주기",
                len(report.redispatched),
                len(report.requeued),
                len(report.failed),
                len(report.restructured),
            )
        return report

    # ---- PROCESSING: 워커 유실 ----
    def _reclaim_lost_workers(
        self, now: datetime, startup: bool
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        with self._uow_factory() as uow:
            candidates = [v.version_id for v in uow.versions.list_by_state(VersionState.PROCESSING)]
        requeued: list[str] = []
        failed: list[str] = []
        for version_id in candidates:
            try:
                outcome = self._reclaim(version_id, now, startup)
            except Exception:  # noqa: BLE001 - 한 버전의 오류가 다른 버전의 복구를 막지 않게 한다
                log.exception("버전 %s 을 복구하지 못했습니다", version_id)
                continue
            if outcome is VersionState.QUEUED:
                requeued.append(version_id)
                self._dispatch(Job(RUN_GENERATION_PIPELINE, {"version_id": version_id}))
            elif outcome is VersionState.FAILED:
                failed.append(version_id)
        return tuple(requeued), tuple(failed)

    def _reclaim(self, version_id: str, now: datetime, startup: bool) -> VersionState | None:
        with self._uow_factory() as uow:
            version = uow.versions.get(version_id)
            if version is None or version.state is not VersionState.PROCESSING:
                return None
            if not (startup and self._reclaim_on_startup) and not self._lease_expired(version, now):
                return None
            if version.attempt >= self.max_attempts:
                version.mark_failed(WORKER_LOST, self._lost_message(version))
            else:
                version.transition_to(VersionState.QUEUED)
                version.record_queued(now)
            target = version.state
            if not uow.versions.try_transition(version_id, VersionState.PROCESSING, target):
                return None  # 그 사이 워커가 끝냈다
            uow.versions.save(version)
            uow.commit()
        log.warning(
            "버전 %s 의 워커가 사라졌습니다 (시도 %d/%d, 시작 %s): %s",
            version_id,
            version.attempt,
            self.max_attempts,
            version.started_at,
            "다시 실행" if target is VersionState.QUEUED else WORKER_LOST,
        )
        return target

    def _lease_expired(self, version: VideoVersion, now: datetime) -> bool:
        if version.started_at is None:  # 기능 이전에 시작된 행: 리스를 잴 수 없다
            return True
        return (_utc(now) - _utc(version.started_at)).total_seconds() > self.lease_seconds

    def _lost_message(self, version: VideoVersion) -> str:
        started = (
            f", 마지막 시작 {version.started_at:%Y-%m-%d %H:%M:%S}" if version.started_at else ""
        )
        return (
            f"워커가 실행 중에 사라졌습니다 (시도 {version.attempt}/{self.max_attempts}{started}). "
            "재시도 상한에 닿아 더 실행하지 않습니다. 이미 만든 장면은 생성 캐시에 남아 있어 "
            "같은 조건으로 다시 생성하면 재사용됩니다"
        )

    # ---- QUEUED·STRUCTURING: 사라진 작업 ----
    def _redispatch(
        self, state: VersionState, job_name: str, skip: set[str] | None = None
    ) -> tuple[str, ...]:
        with self._uow_factory() as uow:
            version_ids = [v.version_id for v in uow.versions.list_by_state(state)]
        done = []
        for version_id in version_ids:
            if skip and version_id in skip:
                continue
            if self._dispatch(Job(job_name, {"version_id": version_id})):
                done.append(version_id)
        return tuple(done)

    def _dispatch(self, job: Job) -> bool:
        try:
            self._dispatcher.dispatch(job)
        except Exception:  # noqa: BLE001 - 큐 오류 하나가 나머지 복구를 막지 않게 한다
            log.exception("복구 작업을 넘기지 못했습니다: %s %s", job.name, dict(job.payload))
            return False
        return True


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
