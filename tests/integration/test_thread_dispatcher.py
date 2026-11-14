"""스레드 디스패처로 도는 실제 서버 구성 (감사 T-02, A-41, A-07).

.env.example · Dockerfile · README 가 권하는 실행 방식(FRAME_JOBS=thread)인데 테스트는 recording·inline 만 돌았다.
요청 스레드, 작업 스레드, 화면의 조회가 같은 저장소를 동시에 쓴다. 저장소 구현마다 그 조합이 끝까지 가는지 본다.
"""

from __future__ import annotations

import logging
import threading
import time

import pytest

from frame.adapters.common import UuidIdGenerator
from frame.adapters.jobs import ThreadPoolJobDispatcher
from frame.application.dto import CreateProjectCommand
from frame.application.ports.jobs import Job, JobHandlerRegistry
from frame.config import Settings, build_container
from frame.domain.enums import VersionState
from frame.domain.errors import DomainError
from tests.support.factories import CAFE_PROMPT

WAIT = 30.0  # 테스트가 멈춰 버리지 않게 하는 안전 상한
ROUNDS = 6  # 버전을 여러 개 만들어 스레드가 겹치는 순간을 늘린다
BACKENDS = ["memory", "sqlite-file", "sqlite-in-memory"]


def _settings(backend: str, tmp_path) -> Settings:
    if backend == "memory":
        return Settings(jobs="thread", persistence="memory")
    if backend == "sqlite-file":
        url = f"sqlite+pysqlite:///{(tmp_path / 'frame.db').as_posix()}"
        return Settings(jobs="thread", persistence="sqlalchemy", db_url=url)
    # 주소를 정하지 않은 SQLAlchemy 구성 (Dockerfile 만으로 띄운 이미지가 이 조합이다)
    return Settings(jobs="thread", persistence="sqlalchemy")


@pytest.fixture(params=BACKENDS)
def container(request, tmp_path):
    built = build_container(
        _settings(request.param, tmp_path), overrides={"ids": UuidIdGenerator()}
    )
    yield built
    built.dispatcher.shutdown()


def _wait(container) -> None:
    container.dispatcher.wait()


def test_versions_reach_completed_while_the_screen_keeps_polling(container):
    """프로젝트 생성 → 구조화 → 생성 접수 → 파이프라인. 그동안 다른 스레드가 화면처럼 버전을 계속 조회한다."""
    version_ids: list[str] = []
    stop = threading.Event()
    failures: list[BaseException] = []

    def poll() -> None:
        while not stop.is_set():
            for version_id in version_ids[:]:  # 다른 스레드가 더하는 목록의 지금 모습
                try:
                    container.queries.get_version(version_id)
                except BaseException as exc:  # noqa: BLE001 - 스레드의 실패를 단언으로 옮긴다
                    failures.append(exc)
            time.sleep(0.001)

    poller = threading.Thread(target=poll, daemon=True)
    poller.start()
    try:
        for index in range(ROUNDS):
            created = container.create_project.execute(
                CreateProjectCommand(title=f"카페 {index}", prompt=CAFE_PROMPT)
            )
            version_ids.append(created.version_id)
            _wait(container)  # 구조화
            container.accept_generation.execute(created.version_id)
            _wait(container)  # 생성·추적·검수
    finally:
        stop.set()
        poller.join(WAIT)

    states = [container.queries.get_version(v).version.state for v in version_ids]
    assert states == [VersionState.COMPLETED] * ROUNDS
    assert failures == []


def test_two_requests_to_generate_the_same_version_start_one_run(container):
    """같은 버전의 생성 접수가 동시에 둘 오면 하나만 받는다 (두 번 생성하면 두 번 과금된다)."""
    created = container.create_project.execute(
        CreateProjectCommand(title="카페", prompt=CAFE_PROMPT)
    )
    _wait(container)
    together = threading.Barrier(2)
    accepted: list[str] = []
    refused: list[BaseException] = []

    def accept() -> None:
        together.wait(WAIT)
        try:
            container.accept_generation.execute(created.version_id)
            accepted.append("ok")
        except DomainError as exc:
            refused.append(exc)

    threads = [threading.Thread(target=accept) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(WAIT)
    _wait(container)

    assert (len(accepted), len(refused)) == (1, 1)
    assert container.queries.get_version(created.version_id).version.state is VersionState.COMPLETED


# ---- 디스패처 자체 ----------------------------------------------------------------------


def _job(name: str = "work", **payload: object) -> Job:
    return Job(name, payload)


def test_a_failing_handler_does_not_take_the_worker_down(caplog):
    handlers = JobHandlerRegistry()
    done: list[object] = []

    def handle(job: Job) -> None:
        if job.payload.get("fail"):
            raise RuntimeError("handler exploded")
        done.append(job.payload["n"])

    handlers.register("work", handle)
    dispatcher = ThreadPoolJobDispatcher(handlers, max_workers=1)
    try:
        with caplog.at_level(logging.ERROR, logger="frame.adapters.jobs.thread"):
            dispatcher.dispatch(_job(fail=True))
            dispatcher.dispatch(_job(n=1))
            dispatcher.dispatch(_job(n=2))
            dispatcher.wait()  # 실패한 작업 때문에 기다리는 쪽이 예외를 받지 않는다
    finally:
        dispatcher.shutdown()

    assert done == [1, 2]
    assert "handler exploded" in caplog.text


def test_wait_covers_the_jobs_that_are_still_running():
    handlers = JobHandlerRegistry()
    release = threading.Event()
    finished: list[int] = []

    def handle(job: Job) -> None:
        release.wait(WAIT)
        finished.append(job.payload["n"])

    handlers.register("work", handle)
    dispatcher = ThreadPoolJobDispatcher(handlers, max_workers=3)
    try:
        for n in range(3):
            dispatcher.dispatch(_job(n=n))
        threading.Timer(0.05, release.set).start()
        dispatcher.wait()
        assert sorted(finished) == [0, 1, 2]
    finally:
        release.set()
        dispatcher.shutdown()


def test_the_number_of_workers_can_be_chosen():
    handlers = JobHandlerRegistry()
    running = threading.Semaphore(0)
    release = threading.Event()

    def handle(job: Job) -> None:
        running.release()
        release.wait(WAIT)

    handlers.register("work", handle)
    dispatcher = ThreadPoolJobDispatcher(handlers, max_workers=4)
    try:
        for _ in range(4):
            dispatcher.dispatch(_job())
        # 네 작업이 동시에 돌기 시작한다 (워커가 둘뿐이면 둘은 기다린다)
        assert all(running.acquire(timeout=WAIT) for _ in range(4))
    finally:
        release.set()
        dispatcher.wait()
        dispatcher.shutdown()
