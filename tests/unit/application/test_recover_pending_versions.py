"""재시작 복구 (#2, ADR-0013): 지난 프로세스가 남긴 버전을 다시 디스패치하거나 명확한 오류로 끝낸다.

- 기동: QUEUED·STRUCTURING 은 작업을 다시 넣고, PROCESSING 은 (in-process 디스패처라) 모두 워커 유실로 본다.
- 주기 검사: PROCESSING 이 FRAME_WORKER_LEASE_S 를 넘기면 워커 유실. QUEUED·STRUCTURING 은 건드리지 않는다.
- 워커 유실: 시도 횟수가 상한보다 작으면 QUEUED 로 되돌려 다시 실행, 상한이면 FAILED(WORKER_LOST).
"""

import pytest

from frame.application.dto import CreateProjectCommand
from frame.application.errors import ConfigurationError
from frame.application.jobs import RUN_GENERATION_PIPELINE, STRUCTURE_REQUIREMENTS
from frame.application.ports.jobs import Job, JobDispatcher
from frame.application.use_cases import RecoverPendingVersions
from frame.config import Settings
from frame.domain.enums import VersionState
from tests.support.clock import ManualClock
from tests.support.factories import (
    CAFE_PROMPT,
    create_draft,
    dispatcher_of,
    make_container,
    version_of,
)

LEASE_S = 600


@pytest.fixture
def clock():
    return ManualClock()


@pytest.fixture
def container(clock):
    settings = Settings(
        jobs="recording", persistence="memory", worker_lease_s=LEASE_S, worker_max_attempts=2
    )
    return make_container(settings, clock=clock)


def _accepted(container) -> str:
    """생성을 접수했지만 프로세스가 죽어 디스패처의 작업이 사라진 버전 (QUEUED)."""
    version_id = create_draft(container).version_id
    container.accept_generation.execute(version_id)
    dispatcher_of(container).pending.clear()
    return version_id


def _orphaned_while_processing(container, clock, attempts: int = 1) -> str:
    """워커가 실행을 시작한 뒤 프로세스가 죽은 버전 (PROCESSING, 시도 attempts 번)."""
    version_id = _accepted(container)
    with container.uow_factory() as uow:
        assert uow.versions.try_transition(version_id, VersionState.QUEUED, VersionState.PROCESSING)
        version = uow.versions.get(version_id)
        for _ in range(attempts):
            version.record_started(clock.now())
        uow.versions.save(version)
        uow.commit()
    return version_id


def _pending(container) -> list[tuple[str, str]]:
    return [(j.name, j.payload["version_id"]) for j in dispatcher_of(container).pending]


# ---- 기동 ------------------------------------------------------------------------------


def test_startup_redispatches_queued_versions_whose_jobs_were_lost(container):
    version_id = _accepted(container)

    report = container.recover_pending_versions.execute(startup=True)

    assert report.redispatched == (version_id,)
    assert _pending(container) == [(RUN_GENERATION_PIPELINE, version_id)]
    dispatcher_of(container).run_all()
    version = version_of(container, version_id)
    assert (version.state, version.attempt) == (VersionState.COMPLETED, 1)


def test_startup_reclaims_processing_versions_without_waiting_for_the_lease(container, clock):
    version_id = _orphaned_while_processing(container, clock)
    requeued_at = clock.advance(seconds=5)  # 리스(600초)보다 훨씬 짧다

    report = container.recover_pending_versions.execute(startup=True)

    assert report.requeued == (version_id,) and report.redispatched == ()
    version = version_of(container, version_id)
    assert (version.state, version.queued_at, version.attempt) == (
        VersionState.QUEUED,
        requeued_at,
        1,
    )
    assert _pending(container) == [(RUN_GENERATION_PIPELINE, version_id)]  # 한 번만 넣는다

    dispatcher_of(container).run_all()
    version = version_of(container, version_id)
    assert (version.state, version.attempt) == (VersionState.COMPLETED, 2)


def test_attempt_cap_ends_the_version_with_worker_lost(container, clock):
    version_id = _orphaned_while_processing(container, clock, attempts=2)  # 상한 2

    report = container.recover_pending_versions.execute(startup=True)

    assert report.failed == (version_id,) and report.requeued == ()
    version = version_of(container, version_id)
    assert (version.state, version.error_code) == (VersionState.FAILED, "WORKER_LOST")
    assert "2/2" in version.error_message
    assert _pending(container) == []


def test_startup_restructures_versions_left_structuring(container):
    created = container.create_project.execute(CreateProjectCommand("카페", CAFE_PROMPT))
    dispatcher_of(container).pending.clear()  # 구조화 작업이 프로세스와 함께 사라졌다

    report = container.recover_pending_versions.execute(startup=True)

    assert report.restructured == (created.version_id,)
    assert _pending(container) == [(STRUCTURE_REQUIREMENTS, created.version_id)]
    dispatcher_of(container).run_all()
    assert version_of(container, created.version_id).state is VersionState.DRAFT


def test_nothing_to_recover_is_a_quiet_no_op(container):
    create_draft(container)
    report = container.recover_pending_versions.execute(startup=True)
    assert report.total == 0 and _pending(container) == []


# ---- 주기 검사 (리스) --------------------------------------------------------------------


def test_periodic_check_reclaims_processing_only_after_the_lease(container, clock):
    version_id = _orphaned_while_processing(container, clock)

    clock.advance(seconds=LEASE_S - 1)
    assert container.recover_pending_versions.execute().total == 0
    assert version_of(container, version_id).state is VersionState.PROCESSING

    clock.advance(seconds=2)
    report = container.recover_pending_versions.execute()
    assert report.requeued == (version_id,)
    assert _pending(container) == [(RUN_GENERATION_PIPELINE, version_id)]


def test_periodic_check_leaves_queued_and_structuring_versions_alone(container, clock):
    """주기 검사 때 QUEUED·STRUCTURING 은 이 프로세스의 대기열에서 차례를 기다리는 중일 수 있다."""
    _accepted(container)
    container.create_project.execute(CreateProjectCommand("카페", CAFE_PROMPT))
    dispatcher_of(container).pending.clear()
    clock.advance(hours=1)

    assert container.recover_pending_versions.execute().total == 0
    assert _pending(container) == []


def test_processing_row_without_a_start_time_counts_as_lost(container):
    """기능 이전에 PROCESSING 이 된 행(시작 시각 없음)은 리스를 잴 수 없어 유실로 본다."""
    version_id = _accepted(container)
    with container.uow_factory() as uow:
        uow.versions.try_transition(version_id, VersionState.QUEUED, VersionState.PROCESSING)
        uow.commit()

    assert container.recover_pending_versions.execute().requeued == (version_id,)


# ---- 견고성 -----------------------------------------------------------------------------


def test_a_failing_dispatch_does_not_stop_the_other_recoveries(container, clock):
    first = _accepted(container)
    second = _accepted(container)

    class FlakyDispatcher(JobDispatcher):
        def __init__(self):
            self.jobs = []

        def dispatch(self, job: Job) -> None:
            if job.payload["version_id"] == first:
                raise RuntimeError("큐 연결 끊김")
            self.jobs.append(job)

    flaky = FlakyDispatcher()
    recover = RecoverPendingVersions(
        container.uow_factory, flaky, clock, lease_seconds=LEASE_S, max_attempts=2
    )
    report = recover.execute(startup=True)

    assert report.redispatched == (second,)
    assert [j.payload["version_id"] for j in flaky.jobs] == [second]


@pytest.mark.parametrize(
    ("lease", "attempts"),
    [(0, 3), (-1, 3), (60, 0)],
)
def test_invalid_recovery_settings_fail_at_startup(lease, attempts):
    with pytest.raises(ConfigurationError):
        make_container(
            Settings(jobs="recording", worker_lease_s=lease, worker_max_attempts=attempts)
        )
