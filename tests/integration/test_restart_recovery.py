"""#2 재시작 복구 통합: SQLite 파일 DB + 로컬 산출물 폴더를 두 컨테이너가 차례로 쓴다 (프로세스 재시작 흉내).

- 디스패처를 멈춘 채 접수 -> 컨테이너 재조립 -> 복구 -> COMPLETED
- 생성 도중 프로세스가 죽음 -> 재조립 -> 복구 -> 이미 제출한 OpenRouter 작업은 이어 받고, 장면마다 POST 는 한 번
- 서버 기동(lifespan)이 요청을 받기 전에 복구하고, 주기 검사가 리스가 지난 PROCESSING 을 회수한다
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from frame.adapters.common import UuidIdGenerator
from frame.adapters.storage import LocalFileArtifactStore
from frame.application.jobs import RUN_GENERATION_PIPELINE
from frame.config import Settings
from frame.domain.enums import VersionState
from frame.presentation.app import create_app
from frame.presentation.recovery import RecoveryLoop
from tests.support.clock import ManualClock
from tests.support.factories import create_draft, dispatcher_of, make_container, version_of
from tests.support.openrouter import FakeOpenRouterApi, accepted, make_openrouter_generator

SCENE_JOBS = {"intro": "job-intro", "closeup": "job-closeup", "outro": "job-outro"}


class ProcessDied(BaseException):
    """작업 도중 프로세스가 죽었다 (어떤 처리기도 잡지 않는다)."""


def _die(_seconds):
    raise ProcessDied


def _settings(tmp_path, **overrides) -> Settings:
    options = {
        "jobs": "recording",
        "persistence": "sqlalchemy",
        "db_url": f"sqlite+pysqlite:///{(tmp_path / 'frame.db').as_posix()}",
        "artifact_store": "local_fs",
        "artifact_dir": str(tmp_path / "artifacts"),
    }
    options.update(overrides)
    return Settings(**options)


def _restarted(tmp_path, **overrides):
    """같은 DB·산출물 폴더로 새 컨테이너를 조립한다 (메모리에 남은 것이 없는 새 프로세스)."""
    overrides.setdefault("ids", UuidIdGenerator())
    return make_container(_settings(tmp_path), **overrides)


def test_queued_version_survives_a_restart_and_completes(tmp_path):
    before = make_container(_settings(tmp_path))
    draft = create_draft(before)
    before.accept_generation.execute(draft.version_id)
    assert dispatcher_of(before).pending  # 작업은 쌓였지만 실행되지 않은 채 프로세스가 끝난다

    after = _restarted(tmp_path)
    report = after.recover_pending_versions.execute(startup=True)
    assert report.redispatched == (draft.version_id,)
    dispatcher_of(after).run_all()

    version = version_of(after, draft.version_id)
    assert version.state is VersionState.COMPLETED
    assert version.attempt == 1 and version.queued_at is not None and version.started_at is not None
    assert set(version.clips) == {"intro", "closeup", "outro"}


def _fake_openrouter() -> FakeOpenRouterApi:
    api = FakeOpenRouterApi()
    api.on("POST", "/api/v1/videos", *(accepted(job) for job in SCENE_JOBS.values()))
    for job in SCENE_JOBS.values():
        api.add_job(job)
    return api


def test_crash_during_generation_resumes_the_paid_job_and_submits_each_scene_once(tmp_path):
    api = _fake_openrouter()
    store = LocalFileArtifactStore(tmp_path / "artifacts")
    dying = make_openrouter_generator(
        store, api, sleep=_die
    )  # intro 를 제출한 뒤 첫 폴링 전에 죽는다
    before = make_container(_settings(tmp_path), artifact_store=store, generator=dying)
    draft = create_draft(before)
    before.accept_generation.execute(draft.version_id)
    with pytest.raises(ProcessDied):
        dispatcher_of(before).run_all()
    assert len(api.submits) == 1
    assert version_of(before, draft.version_id).state is VersionState.PROCESSING

    store_after = LocalFileArtifactStore(tmp_path / "artifacts")
    after = _restarted(
        tmp_path,
        artifact_store=store_after,
        generator=make_openrouter_generator(store_after, api),
    )
    report = after.recover_pending_versions.execute(startup=True)
    assert report.requeued == (draft.version_id,)
    dispatcher_of(after).run_all()

    version = version_of(after, draft.version_id)
    assert version.state is VersionState.COMPLETED and version.attempt == 2
    assert len(api.submits) == 3  # 장면마다 정확히 한 번: intro 는 다시 제출하지 않고 이어 받았다
    assert api.calls_to("GET", "/api/v1/videos/job-intro/content")
    assert all(store_after.exists(clip.uri) for clip in version.clips.values())


def test_server_startup_recovers_before_serving_requests(tmp_path):
    before = make_container(_settings(tmp_path))
    draft = create_draft(before)
    before.accept_generation.execute(draft.version_id)

    after = make_container(_settings(tmp_path, recovery_interval_s=0), ids=UuidIdGenerator())
    with TestClient(create_app(after)) as client:
        # 첫 요청 전에 기동 훅이 이미 작업을 다시 넣었다
        assert [(j.name, j.payload) for j in dispatcher_of(after).pending] == [
            (RUN_GENERATION_PIPELINE, {"version_id": draft.version_id})
        ]
        dispatcher_of(after).run_all()
        body = client.get(f"/api/versions/{draft.version_id}").json()
    assert body["state"] == "COMPLETED"


def _wait_until(predicate, timeout=5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_periodic_check_reclaims_a_hung_processing_version_after_its_lease(tmp_path):
    clock = ManualClock()
    settings = _settings(tmp_path, worker_lease_s=60, recovery_interval_s=0.01)
    container = make_container(settings, clock=clock)
    draft = create_draft(container)

    with TestClient(create_app(container)):  # 기동 복구는 할 일이 없다 (DRAFT 뿐)
        container.accept_generation.execute(draft.version_id)
        dispatcher_of(container).pending.clear()
        with container.uow_factory() as uow:  # 이 프로세스의 워커가 시작한 뒤 응답이 없다
            uow.versions.try_transition(
                draft.version_id, VersionState.QUEUED, VersionState.PROCESSING
            )
            version = uow.versions.get(draft.version_id)
            version.record_started(clock.now())
            uow.versions.save(version)
            uow.commit()

        time.sleep(0.05)  # 리스 안에서는 주기 검사가 건드리지 않는다
        assert version_of(container, draft.version_id).state is VersionState.PROCESSING

        clock.advance(seconds=61)
        assert _wait_until(
            lambda: version_of(container, draft.version_id).state is VersionState.QUEUED
        )

    assert [(j.name, j.payload) for j in dispatcher_of(container).pending] == [
        (RUN_GENERATION_PIPELINE, {"version_id": draft.version_id})
    ]


def test_recovery_loop_runs_the_task_periodically_until_stopped():
    calls = []
    loop = RecoveryLoop(lambda: calls.append(time.monotonic()), interval_s=0.01)
    loop.start()
    deadline = time.monotonic() + 5
    while len(calls) < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    loop.stop()
    count = len(calls)
    time.sleep(0.05)
    assert count >= 3 and len(calls) == count  # 멈춘 뒤에는 더 부르지 않는다


def test_recovery_loop_survives_a_failing_task():
    calls = []

    def flaky():
        calls.append(1)
        raise RuntimeError("DB 일시 오류")

    loop = RecoveryLoop(flaky, interval_s=0.01)
    loop.start()
    deadline = time.monotonic() + 5
    while len(calls) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    loop.stop()
    assert len(calls) >= 2


def test_recovery_loop_with_zero_interval_never_starts():
    loop = RecoveryLoop(lambda: pytest.fail("부르면 안 된다"), interval_s=0)
    loop.start()
    loop.stop()
