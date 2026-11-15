"""OpenRouter 작업 원장 (#2, ADR-0013): 제출한 작업 id 를 폴링 전에 남겨, 프로세스가 죽어도 새 인스턴스가 이어 받는다.

같은 생성 조건·같은 생성기 설정이면 POST(과금)는 정확히 한 번이어야 한다. 네트워크 없이 가짜 HTTP 전송으로 확인한다.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import replace

import pytest

from frame.adapters.generator import RetryingVideoGenerator
from frame.adapters.generator.records import (
    ArtifactJobLedger,
    InMemoryJobLedger,
    JobRecord,
    build_job_ledger,
    generation_key,
)
from frame.adapters.shared.http import HttpResponse, TransportError
from frame.adapters.storage import InMemoryArtifactStore, LocalFileArtifactStore
from frame.application.errors import GenerationError, GenerationTimeout, StorageError
from frame.application.ports.generator import RevisionDirective, SceneGenerationRequest
from frame.domain.enums import RevisionKind
from tests.support.openrouter import (
    JOB_ID,
    SIGNED_URL,
    TEST_KEY,
    FakeOpenRouterApi,
    FakeTime,
    completed,
    error_response,
    json_response,
    make_openrouter_generator,
    status,
    video_content,
)

STATUS_PATH = f"/api/v1/videos/{JOB_ID}"
CONTENT_PATH = f"/api/v1/videos/{JOB_ID}/content"
DAY = 24 * 3600


class ProcessDied(BaseException):
    """폴링 중 프로세스가 죽었다. Exception 이 아니므로 어떤 처리기도 잡지 않는다."""


def _die(_seconds):
    raise ProcessDied


@pytest.fixture
def store():
    return InMemoryArtifactStore()


def _request(ir, scene_key="intro", version_id="ver_1", seed=7):
    scene = ir.scene(scene_key)
    return SceneGenerationRequest(
        version_id=version_id,
        scene=scene,
        entities=tuple(ir.entity(k) for k in scene.entity_keys),
        seed=seed,
    )


def _crash_after_submit(store, api, ir, **overrides):
    with pytest.raises(ProcessDied):
        make_openrouter_generator(store, api, sleep=_die, **overrides).generate_scene(_request(ir))
    assert len(api.submits) == 1


def _record(store, generator, request) -> JobRecord | None:
    key = generation_key(request.fingerprint, generator.profile())
    return ArtifactJobLedger(store, "openrouter").get(key)


# ---- 제출 직후 기록 --------------------------------------------------------------------


def test_job_id_is_recorded_right_after_submitting_before_polling(store, ir):
    api = FakeOpenRouterApi()
    generator = make_openrouter_generator(store, api, sleep=_die)
    request = _request(ir)
    with pytest.raises(ProcessDied):
        generator.generate_scene(request)

    assert api.calls_to("GET", STATUS_PATH) == []  # 폴링은 시작도 못 했다
    record = _record(store, generator, request)
    assert record is not None
    assert (record.job_id, record.status) == (JOB_ID, "submitted")
    assert (record.version_id, record.scene_key) == ("ver_1", "intro")
    assert record.details["billed_seconds"] == 4


# ---- 재시작 뒤 이어 받기 -------------------------------------------------------------------


def test_new_instance_resumes_the_submitted_job_and_posts_exactly_once(tmp_path, ir):
    api = FakeOpenRouterApi()
    _crash_after_submit(LocalFileArtifactStore(tmp_path), api, ir)

    store = LocalFileArtifactStore(tmp_path)  # 재시작: 같은 산출물 폴더, 새 인스턴스
    clock = FakeTime()
    clip = make_openrouter_generator(store, api, clock=clock).generate_scene(_request(ir))

    assert len(api.submits) == 1
    assert clip.metadata["job_id"] == JOB_ID and clip.metadata["ledger"] == "resumed"
    assert clip.metadata["billed_seconds"] == 4  # 제출 때 남긴 값
    assert store.exists(clip.uri)
    assert clock.sleeps == [20.0]  # 이어 받은 작업은 바로 상태를 보고(진행 중), 한 번 기다린다


def test_completed_job_is_reused_without_any_request(store, ir):
    api = FakeOpenRouterApi()
    first = make_openrouter_generator(store, api).generate_scene(_request(ir, version_id="ver_1"))
    calls = len(api.calls)

    again = make_openrouter_generator(store, api).generate_scene(_request(ir, version_id="ver_2"))

    assert len(api.calls) == calls  # 모델 목록·상태·결과 어느 것도 다시 부르지 않는다
    assert again.uri == first.uri
    assert again.metadata["ledger"] == "reused" and again.metadata["job_id"] == JOB_ID
    assert again.fingerprint == first.fingerprint and again.duration_ms == 4000


def test_completed_job_whose_clip_was_deleted_is_downloaded_again_not_resubmitted(tmp_path, ir):
    api = FakeOpenRouterApi()
    store = LocalFileArtifactStore(tmp_path)
    first = make_openrouter_generator(store, api).generate_scene(_request(ir, version_id="ver_1"))
    store.local_path(first.uri).unlink()

    again = make_openrouter_generator(LocalFileArtifactStore(tmp_path), api).generate_scene(
        _request(ir, version_id="ver_2")
    )

    assert len(api.submits) == 1
    assert len(api.calls_to("GET", CONTENT_PATH)) == 2
    assert again.metadata["ledger"] == "resumed" and store.exists(again.uri)


def test_timed_out_job_is_resumed_by_the_next_request(store, ir):
    api = FakeOpenRouterApi()
    api.on(
        "GET",
        STATUS_PATH,
        status("in_progress"),
        status("in_progress"),
        status("in_progress"),
        completed(),
    )
    with pytest.raises(GenerationTimeout) as excinfo:
        make_openrouter_generator(
            store, api, timeout_seconds=40, poll_interval_seconds=20
        ).generate_scene(_request(ir))
    assert "이어 받" in str(excinfo.value)  # 다시 생성하면 같은 작업을 이어 받는다고 안내한다

    clip = make_openrouter_generator(store, api).generate_scene(_request(ir, version_id="ver_2"))
    assert len(api.submits) == 1 and clip.metadata["ledger"] == "resumed"


def test_authentication_error_while_polling_keeps_the_job_resumable(store, ir):
    api = FakeOpenRouterApi()
    api.on("GET", STATUS_PATH, error_response(401, "Missing Authentication header"))
    with pytest.raises(GenerationError, match="401"):
        make_openrouter_generator(store, api).generate_scene(_request(ir))

    api.on("GET", STATUS_PATH, completed())  # 키를 고친 뒤
    clip = make_openrouter_generator(store, api).generate_scene(_request(ir))
    assert len(api.submits) == 1 and clip.metadata["ledger"] == "resumed"


# ---- 이어 받지 않는 경우 ---------------------------------------------------------------------


@pytest.mark.parametrize("state", ["failed", "cancelled", "expired"])
def test_failed_job_is_not_resumed_and_the_next_request_submits_again(store, ir, state):
    api = FakeOpenRouterApi()
    api.on("GET", STATUS_PATH, status(state, error="moderation"))
    with pytest.raises(GenerationError) as excinfo:
        RetryingVideoGenerator(make_openrouter_generator(store, api), retries=2).generate_scene(
            _request(ir)
        )
    assert excinfo.value.retryable is False and len(api.submits) == 1

    api.on("GET", STATUS_PATH, status("in_progress"), completed())
    clip = make_openrouter_generator(store, api).generate_scene(_request(ir, version_id="ver_2"))
    assert len(api.submits) == 2 and clip.metadata["ledger"] == "new"


def test_job_unknown_to_openrouter_is_reported_once_then_submitted_again(store, ir):
    api = FakeOpenRouterApi()
    _crash_after_submit(store, api, ir)
    api.on("GET", STATUS_PATH, error_response(404, "Not found"))

    with pytest.raises(GenerationError) as excinfo:
        make_openrouter_generator(store, api).generate_scene(_request(ir))
    assert excinfo.value.retryable is False and JOB_ID in str(excinfo.value)
    assert len(api.submits) == 1

    api.on("GET", STATUS_PATH, completed())
    clip = make_openrouter_generator(store, api).generate_scene(_request(ir))
    assert len(api.submits) == 2 and clip.metadata["ledger"] == "new"


def test_entry_older_than_the_ttl_is_not_resumed(store, ir, caplog):
    api = FakeOpenRouterApi()
    _crash_after_submit(store, api, ir)

    next_day = FakeTime(wall=FakeTime().wall + DAY + 1)
    with caplog.at_level(logging.WARNING):
        make_openrouter_generator(store, api, clock=next_day).generate_scene(_request(ir))

    assert len(api.submits) == 2
    assert any("만료" in r.getMessage() and JOB_ID in r.getMessage() for r in caplog.records)


def test_ttl_is_configurable(store, ir):
    api = FakeOpenRouterApi()
    _crash_after_submit(store, api, ir, ledger_ttl_seconds=60)
    later = FakeTime(wall=FakeTime().wall + 61)
    make_openrouter_generator(store, api, clock=later, ledger_ttl_seconds=60).generate_scene(
        _request(ir)
    )
    assert len(api.submits) == 2


def test_a_completed_job_whose_clip_is_still_there_is_reused_after_the_ttl(store, ir):
    """기한은 제공자에게 다시 물어야 하는 기록에만 있다. 클립이 남아 있으면 며칠 뒤에도 다시 내지 않는다 (감사 A-32)."""
    api = FakeOpenRouterApi()
    first = make_openrouter_generator(store, api).generate_scene(_request(ir, version_id="ver_1"))
    calls = len(api.calls)

    next_week = FakeTime(wall=FakeTime().wall + 7 * DAY)
    again = make_openrouter_generator(store, api, clock=next_week).generate_scene(
        _request(ir, version_id="ver_2")
    )

    assert len(api.calls) == calls  # 제출도 조회도 없다
    assert again.uri == first.uri and again.metadata["ledger"] == "reused"


def test_a_completed_job_whose_clip_is_gone_is_submitted_again_after_the_ttl(tmp_path, ir, caplog):
    """클립이 없으면 제공자에게서 다시 받아야 한다. 기한이 지난 작업은 그쪽에 남아 있다고 볼 수 없다."""
    api = FakeOpenRouterApi()
    store = LocalFileArtifactStore(tmp_path)
    first = make_openrouter_generator(store, api).generate_scene(_request(ir, version_id="ver_1"))
    store.local_path(first.uri).unlink()

    next_week = FakeTime(wall=FakeTime().wall + 7 * DAY)
    with caplog.at_level(logging.WARNING):
        again = make_openrouter_generator(store, api, clock=next_week).generate_scene(
            _request(ir, version_id="ver_2")
        )

    assert len(api.submits) == 2 and again.metadata["ledger"] == "new"
    assert any("만료" in r.getMessage() for r in caplog.records)


def test_repairing_a_different_requirement_of_the_same_scene_is_a_new_job(store, ir):
    """같은 부모의 같은 장면이라도 고치려는 요구조건이 다르면 앞선 결과를 돌려주지 않는다 (감사 A-33)."""
    api = FakeOpenRouterApi()
    generator = make_openrouter_generator(store, api)
    base = _request(ir, seed=8)
    color = next(r for r in base.scene.requirements if r.predicate == "color")
    shirt = next(r for r in base.scene.requirements if r.predicate == "shirt_color")

    def repair(requirement, version_id):
        directive = RevisionDirective(RevisionKind.REPAIR, enforce=(requirement,))
        return generator.generate_scene(replace(base, version_id=version_id, directive=directive))

    repair(color, "ver_1")
    second = repair(shirt, "ver_2")
    again = repair(shirt, "ver_3")

    prompts = [call.json()["prompt"] for call in api.submits]
    assert len(prompts) == 2  # 같은 것을 고치는 세 번째 요청은 다시 내지 않는다
    assert "Must show: cup color: red" in prompts[0]
    assert "Must show: barista shirt color: white" in prompts[1]
    assert second.metadata["ledger"] == "new" and again.metadata["ledger"] == "reused"


def test_other_generator_settings_never_resume_each_others_jobs(store, ir):
    api = FakeOpenRouterApi()
    _crash_after_submit(store, api, ir, resolution="480p")
    make_openrouter_generator(store, api, resolution="720p").generate_scene(_request(ir))
    assert len(api.submits) == 2


# ---- 원장 자체 -------------------------------------------------------------------------


def test_ledger_holds_the_job_id_but_no_key_or_signed_url(tmp_path, ir):
    make_openrouter_generator(LocalFileArtifactStore(tmp_path)).generate_scene(_request(ir))
    (entry,) = (tmp_path / "ledger" / "openrouter").glob("*.json")
    text = entry.read_text(encoding="utf-8")
    assert JOB_ID in text and '"completed"' in text
    assert TEST_KEY not in text and SIGNED_URL not in text and "SIGNED-SECRET" not in text


def test_unreadable_ledger_entry_is_ignored_with_a_warning(tmp_path, ir, caplog):
    api = FakeOpenRouterApi()
    _crash_after_submit(LocalFileArtifactStore(tmp_path), api, ir)
    (entry,) = (tmp_path / "ledger" / "openrouter").glob("*.json")
    entry.write_text("{깨진", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        make_openrouter_generator(LocalFileArtifactStore(tmp_path), api).generate_scene(
            _request(ir)
        )
    assert len(api.submits) == 2
    assert any("작업 원장" in r.getMessage() for r in caplog.records)


def test_ledger_write_failure_does_not_throw_away_the_paid_job(ir, caplog):
    class ReadOnlyLedgerStore(InMemoryArtifactStore):
        def put(self, key, data, content_type=None):
            if key.startswith("ledger/"):
                raise StorageError("읽기 전용")
            return super().put(key, data, content_type)

    store = ReadOnlyLedgerStore()
    with caplog.at_level(logging.ERROR):
        clip = make_openrouter_generator(store).generate_scene(_request(ir))
    assert store.exists(clip.uri)
    assert any("작업 원장에 기록하지 못했습니다" in r.getMessage() for r in caplog.records)


def test_concurrent_requests_for_the_same_scene_submit_once(store, ir):
    api = FakeOpenRouterApi()
    clock = FakeTime()
    first_poll, release = threading.Event(), threading.Event()

    def sleep(seconds):
        if not first_poll.is_set():
            first_poll.set()
            release.wait(5)
        clock.sleep(seconds)

    generator = make_openrouter_generator(store, api, clock=clock, sleep=sleep)
    results = []

    def run(version_id):
        results.append(generator.generate_scene(_request(ir, version_id=version_id)))

    threads = [threading.Thread(target=run, args=(v,)) for v in ("ver_1", "ver_2")]
    threads[0].start()
    assert first_poll.wait(5)  # 첫 요청이 제출을 마치고 폴링을 기다리는 중
    threads[1].start()
    time.sleep(0.05)  # 두 번째 요청이 같은 장면의 잠금에서 기다리게 한다
    release.set()
    for thread in threads:
        thread.join(5)

    assert len(api.submits) == 1 and len(results) == 2
    assert sorted(r.metadata["ledger"] for r in results) == ["new", "reused"]


def test_ledger_lives_in_the_artifact_store_when_it_can_address_keys(tmp_path):
    assert isinstance(build_job_ledger(LocalFileArtifactStore(tmp_path)), ArtifactJobLedger)

    class OpaqueStore(InMemoryArtifactStore):
        def uri_for(self, key):
            return None

    assert isinstance(build_job_ledger(OpaqueStore()), InMemoryJobLedger)


# ---- 과금 뒤의 실패: 무엇을 이어 받게 남기고 무엇을 버리는가 (감사 T-04) ------------------------


@pytest.mark.parametrize(
    "failure",
    [error_response(503, "Service unavailable"), TransportError("connection reset")],
    ids=["503", "no-answer"],
)
def test_a_download_that_keeps_failing_leaves_the_job_resumable(store, ir, failure):
    """결과 내려받기가 끝까지 안 돼도 다시 제출하지 않는다. 다음 요청은 같은 작업의 결과만 다시 받는다."""
    api = FakeOpenRouterApi()
    api.on("GET", CONTENT_PATH, failure)
    generator = make_openrouter_generator(store, api)
    request = _request(ir)

    with pytest.raises(GenerationError, match="내려받지 못했습니다") as excinfo:
        RetryingVideoGenerator(generator, retries=2).generate_scene(request)

    assert excinfo.value.retryable is False and JOB_ID in str(excinfo.value)
    assert len(api.calls_to("GET", CONTENT_PATH)) == 3 and len(api.submits) == 1
    assert _record(store, generator, request).status == "submitted"

    api.on("GET", CONTENT_PATH, video_content())
    clip = make_openrouter_generator(store, api).generate_scene(_request(ir, version_id="ver_2"))
    assert len(api.submits) == 1 and clip.metadata["ledger"] == "resumed"


def test_a_result_that_is_gone_is_not_asked_for_again(store, ir):
    """결과가 없다는 답(404)은 기다려도 나아지지 않는다. 이번 요청은 끝내고, 다음 요청이 새로 제출한다."""
    api = FakeOpenRouterApi()
    api.on("GET", CONTENT_PATH, error_response(404, "Not found"))
    generator = make_openrouter_generator(store, api)
    request = _request(ir)

    with pytest.raises(GenerationError) as excinfo:
        RetryingVideoGenerator(generator, retries=2).generate_scene(request)

    assert excinfo.value.retryable is False and len(api.submits) == 1
    assert len(api.calls_to("GET", CONTENT_PATH)) == 1
    assert _record(store, generator, request).status == "failed"

    api.on("GET", CONTENT_PATH, video_content())
    clip = make_openrouter_generator(store, api).generate_scene(_request(ir, version_id="ver_2"))
    assert len(api.submits) == 2 and clip.metadata["ledger"] == "new"


def test_a_download_refused_for_authentication_keeps_the_job_resumable(store, ir):
    api = FakeOpenRouterApi()
    api.on("GET", CONTENT_PATH, error_response(401, "Missing Authentication header"))
    generator = make_openrouter_generator(store, api)
    request = _request(ir)

    with pytest.raises(GenerationError, match="401") as excinfo:
        RetryingVideoGenerator(generator, retries=2).generate_scene(request)

    assert excinfo.value.retryable is False and len(api.submits) == 1
    assert len(api.calls_to("GET", CONTENT_PATH)) == 1  # 키가 틀린 것은 다시 물어도 같다
    assert _record(store, generator, request).status == "submitted"

    api.on("GET", CONTENT_PATH, video_content())  # 키를 고친 뒤
    clip = make_openrouter_generator(store, api).generate_scene(_request(ir, version_id="ver_2"))
    assert len(api.submits) == 1 and clip.metadata["ledger"] == "resumed"


@pytest.mark.parametrize(
    "answer",
    [
        json_response(202, {"status": "pending"}),
        HttpResponse(202, b"accepted", {"content-type": "text/plain"}),
        HttpResponse(202, b"[]", {"content-type": "application/json"}),
    ],
    ids=["no-id", "not-json", "not-an-object"],
)
def test_an_accepted_answer_without_a_usable_job_id_is_never_sent_again(store, ir, answer):
    """접수는 됐는데 작업 id 를 모른다. 과금됐을 수 있으므로 재시도 데코레이터 아래에서도 한 번만 보낸다."""
    api = FakeOpenRouterApi()
    api.on("POST", "/api/v1/videos", answer)

    with pytest.raises(GenerationError) as excinfo:
        RetryingVideoGenerator(make_openrouter_generator(store, api), retries=2).generate_scene(
            _request(ir)
        )

    assert excinfo.value.retryable is False and len(api.submits) == 1
    assert api.calls_to("GET", STATUS_PATH) == []


@pytest.mark.parametrize("body", [b"<html>bad gateway</html>", b"[1, 2]"], ids=["html", "list"])
def test_a_status_answer_that_cannot_be_read_keeps_the_job_resumable(store, ir, body):
    api = FakeOpenRouterApi()
    api.on("GET", STATUS_PATH, HttpResponse(200, body, {"content-type": "application/json"}))
    generator = make_openrouter_generator(store, api)
    request = _request(ir)

    with pytest.raises(GenerationError) as excinfo:
        RetryingVideoGenerator(generator, retries=2).generate_scene(request)

    assert excinfo.value.retryable is False and len(api.submits) == 1
    assert _record(store, generator, request).status == "submitted"

    api.on("GET", STATUS_PATH, completed())
    clip = make_openrouter_generator(store, api).generate_scene(_request(ir, version_id="ver_2"))
    assert len(api.submits) == 1 and clip.metadata["ledger"] == "resumed"
