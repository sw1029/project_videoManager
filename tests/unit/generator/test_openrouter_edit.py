"""OpenRouter 어댑터의 부모 클립 편집 (ADR-0016). 네트워크와 ffmpeg 없이 돈다.

- previous_job: 부모 클립을 만든 작업을 previous_job_id 로 이어서 편집 (같은 모델·엔드포인트).
- video_reference: 부모 클립을 input_references 의 video_url 로 편집 모델에 보낸다. 영상 참조는 HTTPS 주소만
  받으므로(data URI 는 거절된다) 부모 작업의 저장본 서명 URL 을 그 자리에서 받아 넘긴다.
- 제출이 접수 전에 거절되면(400 등) 과금 없이 지금처럼 새로 생성한다.
"""

from __future__ import annotations

import json
import threading
from dataclasses import replace

import pytest

from frame.adapters.generator import CachingVideoGenerator
from frame.adapters.generator.cache import generation_key
from frame.adapters.generator.ledger import ArtifactJobLedger
from frame.adapters.generator.openrouter import (
    EDIT_KEEP_LINE,
    VideoModelInfo,
    compile_edit_prompt,
)
from frame.adapters.shared.http import TransportError
from frame.adapters.storage import InMemoryArtifactStore, LocalFileArtifactStore
from frame.application.errors import ConfigurationError, GenerationError
from frame.application.ports.generator import (
    GeneratedClip,
    ParentClip,
    ProductionMethod,
    RevisionDirective,
    SceneGenerationRequest,
)
from frame.config import Settings, build_container
from frame.domain.enums import KeepAspect, RevisionKind
from frame.domain.ir import RequirementSpec
from frame.domain.revision import AttributeChange, DefaultPropagationPolicy, KeepItem
from tests.support.openrouter import (
    JOB_ID,
    SIGNED_URL,
    WAN_30,
    FakeOpenRouterApi,
    FakeTime,
    accepted,
    completed,
    error_response,
    make_openrouter_generator,
    status,
)

PARENT_JOB = "gen-vid-1794447860-lGgNR4uyOp895PnVYmFg"
PARENT_BYTES = b"PARENT-MP4-BYTES"
FLUX_EDIT = {
    "id": "black-forest-labs/flux-video-edit",
    "supported_resolutions": None,
    "supported_aspect_ratios": None,
    "supported_durations": None,
    "generate_audio": False,
    "seed": False,
    "pricing_skus": {"cents_per_second_output": "3"},
}
policy = DefaultPropagationPolicy()


@pytest.fixture
def store():
    return InMemoryArtifactStore()


def _parent(ir, store, scene_key="intro", **metadata):
    scene = ir.scene(scene_key)
    uri = store.put(f"clips/ver_0/{scene_key}.mp4", PARENT_BYTES, "video/mp4")
    base = {"generator": "openrouter", "model": "alibaba/wan-3.0", "job_id": PARENT_JOB}
    base.update(metadata)
    clip = GeneratedClip(scene_key, uri, scene.duration_ms, "parent-fp", base)
    return ParentClip("ver_0", clip, scene, tuple(ir.entity(k) for k in scene.entity_keys))


def _request(ir, store, change=("cup", "color", "teal"), scene_key="intro", **metadata):
    """부모(ir) 에서 change 하나만 바뀐 장면의 재생성 요청."""
    revised = policy.apply(ir, AttributeChange(*change)).ir
    scene = revised.scene(scene_key)
    return SceneGenerationRequest(
        version_id="ver_1",
        scene=scene,
        entities=tuple(revised.entity(k) for k in scene.entity_keys),
        seed=7,
        parent=_parent(ir, store, scene_key, **metadata),
    )


def _api(*post_responses, parent_status=None):
    """부모 작업(PARENT_JOB)은 끝났고 저장본의 서명 URL 이 있다 (parent_status 로 바꿀 수 있다)."""
    api = FakeOpenRouterApi(models=(WAN_30, FLUX_EDIT))
    api.on("GET", f"/api/v1/videos/{PARENT_JOB}", parent_status or completed(PARENT_JOB))
    if post_responses:
        api.on("POST", "/api/v1/videos", *post_responses)
    return api


# ---- 편집 지시문 -----------------------------------------------------------------------


def test_edit_prompt_lists_only_what_changed(ir, store):
    prompt = compile_edit_prompt(_request(ir, store))

    assert prompt.splitlines() == [
        "Edit the input video. Apply only these changes:",
        "- cup: color changes from red to teal",
        EDIT_KEEP_LINE,
    ]


def test_edit_prompt_covers_added_removed_and_relation_changes(ir, store):
    request = _request(ir, store)
    scene = request.scene
    requirements = tuple(
        r for r in scene.requirements if r.predicate not in {"shirt_color", "holds"}
    ) + (
        RequirementSpec("intro", "material", "glass", "cup"),
        RequirementSpec("intro", "looks_at", "cup", "barista", "cup"),
        RequirementSpec("intro", "mood", "calm"),
    )
    request = replace(request, scene=replace(scene, requirements=requirements))

    lines = compile_edit_prompt(request).splitlines()

    assert "- cup: color changes from red to teal" in lines
    assert "- cup: material is now glass" in lines
    assert "- barista: now looks at cup" in lines
    assert "- barista: no longer holds cup" in lines
    assert "- barista: shirt color (white) is no longer required" in lines
    assert "- the scene: mood is now calm" in lines


def test_edit_prompt_mentions_changed_descriptions(ir, store):
    request = _request(ir, store)
    request = replace(
        request,
        scene=replace(request.scene, description="바리스타가 컵을 높이 든다"),
        entities=tuple(
            replace(e, description="유리 머그컵") if e.entity_key == "cup" else e
            for e in request.entities
        ),
    )

    lines = compile_edit_prompt(request).splitlines()

    assert "- cup is now: 유리 머그컵" in lines
    assert "- the scene is now: 바리스타가 컵을 높이 든다" in lines


def test_there_is_no_edit_prompt_without_a_parent_or_without_changes(ir, store):
    request = _request(ir, store)
    assert compile_edit_prompt(replace(request, parent=None)) is None
    unchanged = replace(request, scene=request.parent.scene, entities=request.parent.entities)
    assert compile_edit_prompt(unchanged) is None


# ---- previous_job_id ------------------------------------------------------------------


def test_previous_job_mode_continues_the_job_that_made_the_parent_clip(ir, store):
    api = _api()
    generator = make_openrouter_generator(store, api, edit_mode="previous_job")
    request = _request(ir, store)

    clip = generator.generate_scene(request)

    body = api.submits[0].json()
    assert body["previous_job_id"] == PARENT_JOB
    assert body["model"] == "alibaba/wan-3.0"  # 같은 모델·엔드포인트에서만 이어 갈 수 있다
    assert body["prompt"].startswith("Edit the input video.")
    assert "input_references" not in body
    assert (body["duration"], body["resolution"], body["aspect_ratio"]) == (4, "480p", "9:16")
    assert body["generate_audio"] is False and body["seed"] == 7
    assert clip.metadata["edit"] == {
        "mode": "previous_job",
        "model": "alibaba/wan-3.0",
        "applied": True,
        "parent_version_id": "ver_0",
        "parent_clip_uri": "mem://clips/ver_0/intro.mp4",
        "previous_job_id": PARENT_JOB,
    }
    assert clip.fingerprint == request.fingerprint and clip.duration_ms == 4000


@pytest.mark.parametrize(
    "metadata",
    [
        {"job_id": None},
        {"job_id": "job-abc123"},  # 형식이 다른 id (다른 서버의 작업 등)
        {"model": "bytedance/seedance-2.0"},  # 다른 모델이 만든 클립
    ],
)
def test_previous_job_mode_regenerates_when_the_parent_job_cannot_be_continued(ir, store, metadata):
    api = _api()
    generator = make_openrouter_generator(store, api, edit_mode="previous_job")

    clip = generator.generate_scene(_request(ir, store, **metadata))

    body = api.submits[0].json()
    assert "previous_job_id" not in body and body["prompt"].startswith("Scene:")
    assert "edit" not in clip.metadata


# ---- video_url 참조 ---------------------------------------------------------------------


def test_video_reference_mode_sends_the_parent_clip_to_the_edit_model(ir, store):
    api = _api()
    generator = make_openrouter_generator(
        store, api, edit_mode="video_reference", edit_model="black-forest-labs/flux-video-edit"
    )

    clip = generator.generate_scene(_request(ir, store))

    # 편집 전용 모델은 길이·해상도·화면비·seed·오디오 옵션을 받지 않는다: 목록에 없는 값은 보내지 않는다.
    # 영상 참조는 부모 작업 저장본의 서명 URL 이다 (data URI 는 "Only HTTPS URLs are allowed" 로 거절된다)
    assert api.submits[0].json() == {
        "model": "black-forest-labs/flux-video-edit",
        "prompt": compile_edit_prompt(_request(ir, store)),
        "input_references": [{"type": "video_url", "video_url": {"url": SIGNED_URL}}],
    }
    assert api.calls_to("GET", f"/api/v1/videos/{PARENT_JOB}")  # 그 자리에서 받아 온다
    assert clip.metadata["edit"]["parent_job_id"] == PARENT_JOB
    assert clip.metadata["model"] == "black-forest-labs/flux-video-edit"
    assert clip.metadata["edit"]["mode"] == "video_reference"
    assert clip.metadata["edit"]["applied"] is True
    assert clip.metadata["estimated_cost_usd"] == pytest.approx(0.12)  # 4초 x $0.03
    assert (clip.metadata["output"]["width"], clip.metadata["output"]["height"]) == (480, 854)


def test_video_reference_with_the_generation_model_keeps_its_usual_parameters(ir, store):
    api = _api()
    generator = make_openrouter_generator(store, api, edit_mode="video_reference")

    generator.generate_scene(_request(ir, store))

    body = api.submits[0].json()
    assert body["model"] == "alibaba/wan-3.0"
    assert (body["duration"], body["resolution"], body["aspect_ratio"]) == (4, "480p", "9:16")
    assert body["generate_audio"] is False and body["seed"] == 7
    assert body["input_references"][0]["type"] == "video_url"


def test_scenes_whose_shape_changed_are_regenerated_not_edited(ir, store):
    api = _api()
    generator = make_openrouter_generator(store, api, edit_mode="video_reference")
    request = _request(ir, store)
    longer = replace(request, scene=replace(request.scene, planned_end_ms=6000))
    recast = replace(
        request, scene=replace(request.scene, entity_keys=("cup", "cafe"), requirements=())
    )

    for changed in (longer, recast):
        assert generator.result_key(changed) == changed.generation_fingerprint
    generator.generate_scene(longer)
    assert api.submits[0].json()["prompt"].startswith("Scene:")


def test_signed_parent_url_is_never_recorded(ir, store):
    api = _api()
    generator = make_openrouter_generator(
        store, api, edit_mode="video_reference", edit_model="black-forest-labs/flux-video-edit"
    )

    clip = generator.generate_scene(_request(ir, store))

    assert "SIGNED-SECRET" not in json.dumps(clip.metadata, default=str)
    ledger = [store.get(uri) for uri in store._blobs if "/ledger/" in uri]
    assert ledger and all(b"SIGNED-SECRET" not in blob for blob in ledger)


NO_URLS = status("completed", PARENT_JOB, usage={"cost": 0.2})  # 끝났지만 응답에 저장본 주소가 없다


@pytest.mark.parametrize(
    ("parent_status", "lookups"),
    [
        (NO_URLS, 6),  # 주소가 끝내 오지 않는다: 기본 10초 동안 0·2·4·6·8·10초에 묻는다
        (status("expired", PARENT_JOB), 1),  # 없어진 작업은 다시 묻지 않는다
        (error_response(404, "Resource not found"), 1),
    ],
)
def test_parent_job_without_a_stored_copy_means_regeneration(ir, store, parent_status, lookups):
    api = _api(parent_status=parent_status)
    generator = make_openrouter_generator(store, api, edit_mode="video_reference")

    clip = generator.generate_scene(_request(ir, store))

    (submit,) = api.submits  # 편집 요청은 보내지 않고 새로 만든다
    assert "input_references" not in submit.json() and submit.json()["prompt"].startswith("Scene:")
    edit = clip.metadata["edit"]
    assert edit["applied"] is False
    assert "저장본" in edit["fallback_reason"] and f"{lookups}회" in edit["fallback_reason"]
    assert edit["parent_url_lookups"] == lookups
    assert len(api.calls_to("GET", f"/api/v1/videos/{PARENT_JOB}")) == lookups


def test_parent_url_missing_from_a_response_is_asked_again(ir, store):
    """끝난 작업의 상태 응답에 저장본 주소가 빠져 올 때가 있다 (2차 실호출: 같은 작업을 10번 물으면 2~3번)."""
    api = _api()
    api.on("GET", f"/api/v1/videos/{PARENT_JOB}", NO_URLS, NO_URLS, completed(PARENT_JOB))
    clock = FakeTime()
    generator = make_openrouter_generator(
        store,
        api,
        clock=clock,
        edit_mode="video_reference",
        edit_model="black-forest-labs/flux-video-edit",
    )

    clip = generator.generate_scene(_request(ir, store))

    assert len(api.calls_to("GET", f"/api/v1/videos/{PARENT_JOB}")) == 3
    reference = api.submits[0].json()["input_references"][0]
    assert reference["video_url"]["url"] == SIGNED_URL
    assert clip.metadata["edit"]["applied"] is True
    assert clip.metadata["edit"]["parent_url_lookups"] == 3
    assert clock.sleeps[:2] == [2.0, 2.0]  # 그 뒤의 sleep 은 편집 작업의 폴링이다


def test_lookup_failures_that_may_pass_are_asked_again(ir, store):
    api = _api()
    api.on(
        "GET",
        f"/api/v1/videos/{PARENT_JOB}",
        TransportError("connection reset"),
        error_response(503, "upstream busy"),
        error_response(429, "slow down"),
        completed(PARENT_JOB),
    )
    generator = make_openrouter_generator(store, api, edit_mode="video_reference")

    clip = generator.generate_scene(_request(ir, store))

    assert clip.metadata["edit"]["applied"] is True
    assert clip.metadata["edit"]["parent_url_lookups"] == 4


@pytest.mark.parametrize("code", [408, 409, 425, 429, 500, 524])
def test_the_parent_lookup_treats_passing_statuses_like_polling_does(ir, store, code):
    """부모 작업 조회도 상태 조회·내려받기와 같은 기준으로 '곧 나아질 응답' 을 가린다 (감사 A-37)."""
    api = _api()
    api.on(
        "GET",
        f"/api/v1/videos/{PARENT_JOB}",
        error_response(code, "try later"),
        completed(PARENT_JOB),
    )
    generator = make_openrouter_generator(store, api, edit_mode="video_reference")

    clip = generator.generate_scene(_request(ir, store))

    assert clip.metadata["edit"]["applied"] is True
    assert clip.metadata["edit"]["parent_url_lookups"] == 2


@pytest.mark.parametrize("code", [400, 401, 403])
def test_the_parent_lookup_does_not_repeat_a_refusal(ir, store, code):
    api = _api(parent_status=error_response(code, "no"))
    generator = make_openrouter_generator(store, api, edit_mode="video_reference")

    clip = generator.generate_scene(_request(ir, store))

    assert clip.metadata["edit"]["applied"] is False
    assert len(api.calls_to("GET", f"/api/v1/videos/{PARENT_JOB}")) == 1


def test_a_longer_wait_slows_down_to_the_polling_interval(ir, store):
    """오래 기다리게 설정하면 처음 여섯 번만 촘촘히 묻고 그 뒤로는 폴링 간격으로 묻는다."""
    api = _api(parent_status=NO_URLS)
    clock = FakeTime()
    generator = make_openrouter_generator(
        store, api, clock=clock, edit_mode="video_reference", edit_parent_wait_seconds=60
    )

    clip = generator.generate_scene(_request(ir, store))

    assert clock.sleeps[:8] == [2.0, 2.0, 2.0, 2.0, 2.0, 20.0, 20.0, 10.0]
    assert clip.metadata["edit"]["parent_url_lookups"] == 9
    assert clip.metadata["edit"]["applied"] is False


def test_a_wait_of_zero_asks_once(ir, store):
    api = _api(parent_status=NO_URLS)
    generator = make_openrouter_generator(
        store, api, edit_mode="video_reference", edit_parent_wait_seconds=0
    )

    clip = generator.generate_scene(_request(ir, store))

    assert len(api.calls_to("GET", f"/api/v1/videos/{PARENT_JOB}")) == 1
    assert clip.metadata["edit"]["parent_url_lookups"] == 1


def test_a_parent_clip_with_its_own_address_needs_no_lookup(ir, store):
    api = _api()
    generator = make_openrouter_generator(store, api, edit_mode="video_reference")
    request = _request(ir, store)
    remote = replace(
        request,
        parent=replace(
            request.parent,
            clip=replace(request.parent.clip, uri="https://cdn.example/clips/intro.mp4"),
        ),
    )

    clip = generator.generate_scene(remote)

    assert not api.calls_to("GET", f"/api/v1/videos/{PARENT_JOB}")
    assert "parent_url_lookups" not in clip.metadata["edit"]


def test_a_negative_wait_is_a_configuration_error(store):
    with pytest.raises(ConfigurationError, match="FRAME_VIDEO_EDIT_PARENT_WAIT_S"):
        make_openrouter_generator(store, edit_parent_wait_seconds=-1)


def test_parent_clip_that_is_not_an_openrouter_job_is_regenerated(ir, store):
    """부모 클립에 작업 id 가 없고 원격 주소도 아니면 영상 참조로 보낼 방법이 없다."""
    api = _api()
    generator = make_openrouter_generator(store, api, edit_mode="video_reference")
    request = _request(ir, store, job_id=None)

    assert generator.result_key(request) == request.generation_fingerprint
    clip = generator.generate_scene(request)
    assert "input_references" not in api.submits[0].json() and "edit" not in clip.metadata


def test_parent_clip_already_at_an_https_address_is_sent_as_is(ir, store):
    api = _api()
    generator = make_openrouter_generator(store, api, edit_mode="video_reference")
    request = _request(ir, store, job_id=None)
    remote = replace(
        request,
        parent=replace(
            request.parent,
            clip=replace(request.parent.clip, uri="https://cdn.example/clips/intro.mp4"),
        ),
    )

    generator.generate_scene(remote)

    reference = api.submits[0].json()["input_references"][0]
    assert reference == {
        "type": "video_url",
        "video_url": {"url": "https://cdn.example/clips/intro.mp4"},
    }


# ---- 거절되면 새로 생성 -----------------------------------------------------------------


def test_rejected_edit_falls_back_to_regeneration_without_double_billing(ir, store):
    api = _api(error_response(400, "previous_job_id is not supported by this model"), accepted())
    generator = make_openrouter_generator(store, api, edit_mode="previous_job")
    request = _request(ir, store)

    clip = generator.generate_scene(request)

    first, second = (call.json() for call in api.submits)
    assert first["previous_job_id"] == PARENT_JOB
    assert "previous_job_id" not in second and second["prompt"].startswith("Scene:")
    assert clip.metadata["job_id"] == JOB_ID
    edit = clip.metadata["edit"]
    assert edit["applied"] is False and edit["mode"] == "previous_job"
    assert "400" in edit["fallback_reason"] and "not supported" in edit["fallback_reason"]
    # 같은 요청을 다시 하면 원장의 결과를 쓴다 (편집을 또 시도하지 않는다)
    again = generator.generate_scene(replace(request, version_id="ver_2"))
    assert len(api.submits) == 2 and again.metadata["ledger"] == "reused"


def test_rejected_edit_reuses_an_earlier_regeneration_of_the_same_conditions(ir, store):
    """편집이 거절됐는데 같은 조건을 이미 새로 생성해 둔 적이 있으면 또 생성(과금)하지 않는다."""
    api = _api(accepted(), error_response(400, "video input is not supported"))
    generator = make_openrouter_generator(store, api, edit_mode="video_reference")
    request = _request(ir, store)
    earlier = generator.generate_scene(replace(request, parent=None))  # 편집 없이 만든 결과

    clip = generator.generate_scene(replace(request, version_id="ver_2"))

    assert len(api.submits) == 2  # 처음 생성 + 거절된 편집 시도. 세 번째 제출은 없다
    assert clip.uri == earlier.uri and clip.metadata["ledger"] == "reused"
    assert clip.metadata["edit"]["applied"] is False
    again = generator.generate_scene(replace(request, version_id="ver_3"))
    assert len(api.submits) == 2 and again.uri == earlier.uri  # 편집을 다시 시도하지도 않는다


def test_regeneration_after_a_rejected_edit_is_shared_across_edit_settings(ir, store):
    """거절 뒤 새로 생성한 결과는 편집 설정과 무관한 키(조건 + 정체성 기준)로도 남긴다.

    실호출에서 드러난 빈틈: video_reference 편집이 거절돼 새로 만든 장면을, 편집 모드만 previous_job 으로 바꾼
    실행이 찾지 못해 같은 장면을 또 생성(과금)했다.
    """
    api = _api(
        error_response(400, "Only HTTPS URLs are allowed"),  # video_reference 편집 거절
        accepted(),  # 새로 생성
        error_response(400, "does not support previous_job_id"),  # previous_job 편집 거절
    )
    request = _request(ir, store)
    regenerated = make_openrouter_generator(store, api, edit_mode="video_reference").generate_scene(
        request
    )

    other_mode = make_openrouter_generator(store, api, edit_mode="previous_job")
    clip = other_mode.generate_scene(replace(request, version_id="ver_2"))

    assert len(api.submits) == 3  # 거절 2번 + 생성 1번. 두 번째 생성은 없다
    assert clip.uri == regenerated.uri and clip.metadata["ledger"] == "reused"
    assert clip.metadata["edit"]["mode"] == "previous_job"
    assert clip.metadata["edit"]["applied"] is False
    # 편집을 끈 설정도 그 결과를 쓴다. 남의 편집 시도 기록은 따라오지 않는다
    plain = make_openrouter_generator(store, api)
    again = plain.generate_scene(replace(request, version_id="ver_3"))
    assert len(api.submits) == 3 and again.uri == regenerated.uri
    assert "edit" not in again.metadata


def test_sharing_a_regeneration_does_not_replace_an_unedited_job_in_flight(ir, store):
    """같은 조건을 편집 없이 만드는 작업이 진행 중이면 그 원장 항목을 덮어쓰지 않는다 (이어 받을 수 있어야 한다)."""
    in_flight = "gen-vid-1794450000-" + "P" * 20
    api = _api(
        accepted(in_flight),  # 편집을 끈 설정이 제출한 작업 (아직 생성 중)
        error_response(400, "does not support previous_job_id"),
        accepted(),
    )
    api.add_job(in_flight, status("in_progress", in_flight))
    request = _request(ir, store)
    plain = make_openrouter_generator(store, api, timeout_seconds=1, poll_interval_seconds=1)
    with pytest.raises(GenerationError):
        plain.generate_scene(replace(request, parent=None))  # 기다리다 시간 초과. 원장에는 남는다

    editing = make_openrouter_generator(store, api, edit_mode="previous_job")
    clip = editing.generate_scene(request)

    assert clip.metadata["job_id"] == JOB_ID and len(api.submits) == 3
    api.add_job(in_flight, completed(in_flight))
    resumed = plain.generate_scene(replace(request, parent=None, version_id="ver_2"))
    assert resumed.metadata["job_id"] == in_flight and resumed.metadata["ledger"] == "resumed"
    assert len(api.submits) == 3


def test_sharing_a_regeneration_does_not_write_over_a_job_being_submitted_right_now(ir, store):
    """조건 키를 다른 요청이 지금 만들고 있으면 (잠금을 쥐고 제출하는 중) 그 키에 쓰지 않는다 (감사 A-09).

    잠금 없이 읽고 쓰면, 상대가 접수 기록을 남기기 전에 '없음' 으로 읽고 그 키에 이쪽 결과를 쓴다. 상대의 접수
    기록과 서로 덮어쓰게 되고, 그 사이에 끊기면 상대의 과금된 작업을 이어 받을 길이 없다.
    """
    in_flight = "gen-vid-1794450000-" + "P" * 20
    submitting, release = threading.Event(), threading.Event()

    class SlowSubmitForThePlainRequest(FakeOpenRouterApi):
        def request(self, method, url, **kwargs):
            if method == "POST" and threading.current_thread().name == "plain":
                submitting.set()  # 조건 키의 잠금을 쥔 채 제출 응답을 기다린다
                release.wait(5)
            return super().request(method, url, **kwargs)

    api = SlowSubmitForThePlainRequest(models=(WAN_30, FLUX_EDIT))
    api.on(
        "POST",
        "/api/v1/videos",
        error_response(400, "does not support previous_job_id"),  # 편집 요청은 거절된다
        accepted(),  # 그래서 새로 생성한다
        accepted(in_flight),  # 편집 없이 같은 조건을 만드는 요청 (늦게 접수된다)
    )
    api.add_job(in_flight)
    generator = make_openrouter_generator(store, api, edit_mode="previous_job")
    request = _request(ir, store)
    plain_results = []
    plain = threading.Thread(
        name="plain",
        target=lambda: plain_results.append(
            generator.generate_scene(replace(request, parent=None))
        ),
    )
    plain.start()
    try:
        assert submitting.wait(5)

        clip = generator.generate_scene(replace(request, version_id="ver_2"))

        assert clip.metadata["job_id"] == JOB_ID and clip.metadata["edit"]["applied"] is False
        # 상대가 아직 접수 기록을 남기지 못한 조건 키에는 아무것도 쓰지 않았다
        assert _ledger_status(store, generator, request.generation_fingerprint) is None
    finally:
        release.set()
        plain.join(5)

    assert len(api.submits) == 3
    assert plain_results[0].metadata["job_id"] == in_flight
    assert _ledger_status(store, generator, request.generation_fingerprint) == "completed"
    assert _ledger_status(store, generator, generator.result_key(request)) == "completed"


# 편집 모델이 제출 전에 이 요청을 받을 수 없다고 드러나는 경우 (감사 A-36)
SHORT_EDIT = {
    "id": "vendor/short-edit",
    "supported_resolutions": None,
    "supported_aspect_ratios": None,
    "supported_durations": [2],  # intro 장면은 4초다
    "generate_audio": False,
    "seed": False,
    "pricing_skus": {},
}


def test_an_edit_model_that_is_not_offered_means_regeneration(ir, store):
    """편집 모델이 모델 목록에 없다 (설정 오타, 목록에서 빠짐). 제출 전이라 과금 없이 새로 생성한다.

    ADR-0016: 편집 모델이 요청을 받지 않아도 수정은 실패하지 않는다.
    """
    api = _api()
    generator = make_openrouter_generator(
        store, api, edit_mode="video_reference", edit_model="vendor/not-listed"
    )

    clip = generator.generate_scene(_request(ir, store))

    (submit,) = api.submits
    assert submit.json()["model"] == "alibaba/wan-3.0" and "input_references" not in submit.json()
    edit = clip.metadata["edit"]
    assert edit["applied"] is False and edit["model"] == "vendor/not-listed"
    assert "FRAME_VIDEO_EDIT_MODEL" in edit["fallback_reason"]  # 어느 설정을 볼지 알려 준다
    assert "provisional" not in clip.metadata  # 기다린다고 나아지는 사정이 아니다


def test_a_scene_too_long_for_the_edit_model_is_regenerated(ir, store):
    api = FakeOpenRouterApi(models=(WAN_30, SHORT_EDIT))
    api.on("GET", f"/api/v1/videos/{PARENT_JOB}", completed(PARENT_JOB))
    generator = make_openrouter_generator(
        store, api, edit_mode="video_reference", edit_model="vendor/short-edit"
    )

    clip = generator.generate_scene(_request(ir, store))

    (submit,) = api.submits
    assert submit.json()["model"] == "alibaba/wan-3.0"
    edit = clip.metadata["edit"]
    assert edit["applied"] is False and "최대 길이" in edit["fallback_reason"]


def test_a_regeneration_forced_by_the_edit_model_is_not_made_twice(ir, store):
    """편집 모델 때문에 새로 생성한 결과도 편집 요청의 키로 남는다. 같은 수정을 다시 실행해도 또 내지 않는다."""
    api = _api()
    generator = make_openrouter_generator(
        store, api, edit_mode="video_reference", edit_model="vendor/not-listed"
    )
    request = _request(ir, store)
    first = generator.generate_scene(request)

    again = generator.generate_scene(replace(request, version_id="ver_2"))

    assert len(api.submits) == 1
    assert again.uri == first.uri and again.metadata["ledger"] == "reused"


def test_a_model_list_that_cannot_be_read_is_not_mistaken_for_an_unusable_edit_model(ir, store):
    """모델 목록을 못 받은 것은 일시 오류다. 편집을 포기할 일이 아니라 다시 시도할 일이다."""
    api = _api()
    api.on("GET", "/api/v1/videos/models", error_response(503, "Service unavailable"))
    generator = make_openrouter_generator(
        store, api, edit_mode="video_reference", edit_model="vendor/not-listed"
    )

    with pytest.raises(GenerationError, match="503") as excinfo:
        generator.generate_scene(_request(ir, store))

    assert excinfo.value.retryable is True and api.submits == []


@pytest.mark.parametrize("code", [401, 402, 403, 500])
def test_edit_rejections_that_regeneration_would_also_hit_are_not_retried(ir, store, code):
    api = _api(error_response(code, "nope"))
    generator = make_openrouter_generator(store, api, edit_mode="previous_job")

    with pytest.raises(GenerationError) as excinfo:
        generator.generate_scene(_request(ir, store))

    assert len(api.submits) == 1 and excinfo.value.retryable is False


# ---- 결과 키 --------------------------------------------------------------------------


def test_result_key_includes_the_parent_only_when_the_request_will_be_edited(ir, store):
    request = _request(ir, store)
    plain = make_openrouter_generator(store, _api())
    editing = make_openrouter_generator(store, _api(), edit_mode="video_reference")
    other_job = {**request.parent.clip.metadata, "job_id": "gen-vid-1794450000-" + "Z" * 20}
    other_parent = replace(
        request,
        parent=replace(
            request.parent,
            clip=replace(
                request.parent.clip,
                uri=store.put("clips/ver_9/intro.mp4", b"OTHER", "video/mp4"),
                metadata=other_job,
            ),
        ),
    )

    assert plain.result_key(request) == request.generation_fingerprint
    assert editing.result_key(request) != request.generation_fingerprint
    assert editing.result_key(request) != editing.result_key(other_parent)
    assert editing.result_key(replace(request, parent=None)) == request.generation_fingerprint


def test_editing_the_same_parent_twice_reuses_the_ledger_entry(ir, store):
    api = _api()
    generator = make_openrouter_generator(store, api, edit_mode="video_reference")

    generator.generate_scene(_request(ir, store))
    again = generator.generate_scene(replace(_request(ir, store), version_id="ver_2"))

    assert len(api.submits) == 1 and again.metadata["ledger"] == "reused"


# ---- 모델 메타데이터·설정 ----------------------------------------------------------------


def test_per_second_price_is_read_from_cent_skus_too():
    info = VideoModelInfo.from_json(FLUX_EDIT)
    assert info.price_per_second("480p") == pytest.approx(0.03)
    assert info.audio is False and info.durations == ()
    assert VideoModelInfo.from_json(WAN_30).audio is True


def test_unknown_edit_mode_is_a_configuration_error(store):
    with pytest.raises(ConfigurationError, match="FRAME_VIDEO_EDIT_MODE"):
        make_openrouter_generator(store, edit_mode="magic")


def test_edit_settings_reach_the_adapter(tmp_path):
    key_file = tmp_path / "open.txt"
    key_file.write_text("sk-or-v1-" + "f00d" * 16, encoding="utf-8")
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffmpeg.write_bytes(b"")
    env = {
        "FRAME_GENERATOR": "external_api",
        "FRAME_GENERATOR_CACHE": "false",
        "FRAME_VIDEO_KEY_FILE": str(key_file),
        "FRAME_VIDEO_FFMPEG": str(ffmpeg),
        "FRAME_VIDEO_EDIT_MODE": "video_reference",
        "FRAME_VIDEO_EDIT_MODEL": "black-forest-labs/flux-video-edit",
    }
    settings = Settings.from_env(env)

    generator = build_container(settings).generator

    assert (generator.edit_mode, generator.edit_model) == (
        "video_reference",
        "black-forest-labs/flux-video-edit",
    )
    assert generator.edit_parent_wait_seconds == 10.0
    defaults = Settings.from_env({})
    assert (defaults.video_edit_mode, defaults.video_edit_model) == ("off", None)
    assert defaults.video_edit_parent_wait_s == 10.0

    waiting = Settings.from_env({**env, "FRAME_VIDEO_EDIT_PARENT_WAIT_S": "300"})
    assert build_container(waiting).generator.edit_parent_wait_seconds == 300.0


# ---- 수정 지시: 유지할 요소와 REPAIR (ADR-0017) ------------------------------------------


def _cup_color(scene):
    return tuple(r for r in scene.requirements if (r.entity_key, r.predicate) == ("cup", "color"))


def _repair_request(ir, store, keep=(), scene_key="intro"):
    """요구조건은 부모와 같고(차이 없음) seed 만 다른 재생성 요청: 컵 색이 요구(red)와 어긋났던 장면."""
    scene = ir.scene(scene_key)
    return SceneGenerationRequest(
        version_id="ver_1",
        scene=scene,
        entities=tuple(ir.entity(k) for k in scene.entity_keys),
        seed=8,
        parent=_parent(ir, store, scene_key),
        directive=RevisionDirective(RevisionKind.REPAIR, enforce=_cup_color(scene), keep=keep),
    )


def test_edit_prompt_names_what_must_not_change(ir, store):
    """컨셉 화면의 '유지할 요소' 체크: 인물·동작 / 배경·조명 / 컵 형태."""
    request = _request(ir, store)
    keep = (
        KeepItem(KeepAspect.ACTION, "barista"),
        KeepItem(KeepAspect.APPEARANCE, "barista"),
        KeepItem(KeepAspect.SHAPE, "cup"),
        KeepItem(KeepAspect.LIGHTING),
        KeepItem(KeepAspect.BACKGROUND),
    )
    directive = RevisionDirective(
        RevisionKind.CHANGE, enforce=_cup_color(request.scene), previous_value="red", keep=keep
    )

    prompt = compile_edit_prompt(replace(request, directive=directive))

    assert prompt.splitlines() == [
        "Edit the input video. Apply only these changes:",
        "- cup: color changes from red to teal",
        (
            "Do not change: barista (action and pose, appearance); cup (shape and size); "
            "the lighting; the background."
        ),
        EDIT_KEEP_LINE,
    ]


def test_repair_edit_prompt_states_the_requirement_that_was_missed(ir, store):
    """요구조건은 그대로라 부모와의 차이가 없다. 지켜지지 않았던 값을 지시문으로 적어 부모 클립을 고친다."""
    prompt = compile_edit_prompt(_repair_request(ir, store))

    assert prompt.splitlines() == [
        "Edit the input video. Apply only these changes:",
        "- cup: color must be red",
        EDIT_KEEP_LINE,
    ]
    assert compile_edit_prompt(replace(_repair_request(ir, store), directive=None)) is None


def test_repair_is_sent_to_the_edit_model_with_the_parent_clip(ir, store):
    api = _api(accepted())
    generator = make_openrouter_generator(
        store, api, edit_mode="video_reference", edit_model=FLUX_EDIT["id"]
    )
    keep = (KeepItem(KeepAspect.ACTION, "barista"),)

    clip = generator.generate_scene(_repair_request(ir, store, keep=keep))

    (body,) = (call.json() for call in api.submits)
    assert body["model"] == FLUX_EDIT["id"]
    assert "- cup: color must be red" in body["prompt"]
    assert "Do not change: barista (action and pose)." in body["prompt"]
    assert clip.metadata["edit"]["applied"] is True


def test_keep_items_separate_the_results_of_otherwise_equal_edits(ir, store):
    """같은 수정이라도 그대로 둘 요소가 다르면 다른 결과다 (캐시·원장 키가 갈린다)."""
    generator = make_openrouter_generator(store, _api(), edit_mode="video_reference")
    request = _request(ir, store)
    keeping = replace(
        request,
        directive=RevisionDirective(
            RevisionKind.CHANGE,
            enforce=_cup_color(request.scene),
            keep=(KeepItem(KeepAspect.LIGHTING),),
        ),
    )

    assert generator.result_key(keeping) != generator.result_key(request)


# ---- 만들기 전 미리보기와 부모 주소 보관 (ADR-0020) ---------------------------------------

EDITING = {"edit_mode": "video_reference", "edit_model": "black-forest-labs/flux-video-edit"}
PARENT_STATUS = f"/api/v1/videos/{PARENT_JOB}"


def test_preview_says_the_scene_will_be_edited_and_what_it_costs(ir, store):
    api = _api()
    generator = make_openrouter_generator(store, api, **EDITING)

    preview = generator.preview(_request(ir, store))

    assert (preview.method, preview.model) == (
        ProductionMethod.EDIT,
        "black-forest-labs/flux-video-edit",
    )
    assert preview.cost_usd == pytest.approx(0.12)  # 4초 x $0.03
    assert preview.edit_unavailable_reason is None
    assert not api.submits  # 아무것도 제출하지 않는다
    assert not [uri for uri in store._blobs if "/ledger/" in uri]


def test_the_address_found_by_the_preview_is_used_when_the_scene_is_made(ir, store):
    """계획을 확인하는 사이에 상태 응답에서 주소가 빠져도, 미리 받아 둔 주소로 편집한다.

    2차 실호출: 주소는 보인 뒤에도 몇 분씩 응답에서 빠졌다. 주소 자체는 작업이 만료될 때까지 유효하다.
    """
    api = _api()
    api.on("GET", PARENT_STATUS, completed(PARENT_JOB), NO_URLS)  # 처음 한 번만 주소가 있다
    generator = make_openrouter_generator(store, api, **EDITING)

    generator.preview(_request(ir, store))
    clip = generator.generate_scene(_request(ir, store))

    assert len(api.calls_to("GET", PARENT_STATUS)) == 1  # 만들 때 다시 묻지 않았다
    reference = api.submits[0].json()["input_references"][0]
    assert reference["video_url"]["url"] == SIGNED_URL
    edit = clip.metadata["edit"]
    assert edit["applied"] is True and edit["parent_url_held"] is True
    assert "parent_url_lookups" not in edit
    # 들고 있던 주소도 기록하지 않는다
    assert "SIGNED-SECRET" not in json.dumps(clip.metadata, default=str)
    ledger = [store.get(uri) for uri in store._blobs if "/ledger/" in uri]
    assert ledger and all(b"SIGNED-SECRET" not in blob for blob in ledger)


def test_a_held_address_is_dropped_after_a_while(ir, store):
    api = _api()
    clock = FakeTime()
    generator = make_openrouter_generator(
        store, api, clock=clock, parent_url_hold_seconds=600, **EDITING
    )

    generator.preview(_request(ir, store))
    clock.sleep(601)
    clip = generator.generate_scene(_request(ir, store))

    assert len(api.calls_to("GET", PARENT_STATUS)) == 2  # 다시 물었다
    assert clip.metadata["edit"]["parent_url_lookups"] == 1
    assert "parent_url_held" not in clip.metadata["edit"]


def test_a_held_address_is_not_used_once_the_stored_copy_is_about_to_expire(ir, store):
    clock = FakeTime()
    expiring = status(
        "completed",
        PARENT_JOB,
        urls=[SIGNED_URL],
        expires_at=int(clock.time()) + 100,  # 100초 뒤 만료: 여유 60초를 빼고 40초만 들고 있는다
    )
    api = _api(parent_status=expiring)
    generator = make_openrouter_generator(store, api, clock=clock, **EDITING)

    generator.preview(_request(ir, store))
    clock.sleep(41)
    generator.generate_scene(_request(ir, store))

    assert len(api.calls_to("GET", PARENT_STATUS)) == 2


def test_preview_says_why_the_scene_cannot_be_edited_right_now(ir, store):
    """방금 만든 영상: 끝난 작업인데 저장본 주소가 아직 없다. 새로 생성될 것이고, 잠시 뒤에는 편집할 수 있다."""
    api = _api(parent_status=NO_URLS)
    clock = FakeTime()
    generator = make_openrouter_generator(store, api, clock=clock, **EDITING)

    preview = generator.preview(_request(ir, store))

    assert (preview.method, preview.model) == (ProductionMethod.GENERATE, "alibaba/wan-3.0")
    assert preview.cost_usd == pytest.approx(0.2)  # 4초 x $0.05
    assert "저장본" in preview.edit_unavailable_reason
    assert "다시" in preview.edit_unavailable_reason  # 잠시 뒤 다시 확인하라고 알려 준다
    assert clock.sleeps == [2.0, 1.0]  # 미리보기는 3초만 다시 묻는다 (0·2·3초)
    assert len(api.calls_to("GET", PARENT_STATUS)) == 3 and not api.submits


@pytest.mark.parametrize(
    "parent_status",
    [status("expired", PARENT_JOB), error_response(404, "Resource not found")],
)
def test_preview_does_not_suggest_waiting_for_a_copy_that_is_gone(ir, store, parent_status):
    api = _api(parent_status=parent_status)
    generator = make_openrouter_generator(store, api, **EDITING)

    preview = generator.preview(_request(ir, store))

    assert preview.method is ProductionMethod.GENERATE
    assert "만료" in preview.edit_unavailable_reason
    assert "다시 확인" not in preview.edit_unavailable_reason
    assert len(api.calls_to("GET", PARENT_STATUS)) == 1


def test_preview_does_not_wait_longer_than_making_the_scene_would(ir, store):
    api = _api(parent_status=NO_URLS)
    clock = FakeTime()
    generator = make_openrouter_generator(
        store, api, clock=clock, edit_parent_wait_seconds=0, **EDITING
    )

    preview = generator.preview(_request(ir, store))

    assert preview.method is ProductionMethod.GENERATE
    assert len(api.calls_to("GET", PARENT_STATUS)) == 1 and clock.sleeps == []


def test_preview_without_editing_is_plain_generation(ir, store):
    api = _api()
    generator = make_openrouter_generator(store, api)  # 편집을 켜지 않았다
    recorded = _request(ir, store, generation_seconds=163.7)

    preview = generator.preview(recorded)

    assert (preview.method, preview.model, preview.edit_unavailable_reason) == (
        ProductionMethod.GENERATE,
        "alibaba/wan-3.0",
        None,
    )
    assert preview.cost_usd == pytest.approx(0.2)
    assert preview.seconds == 163.7  # 이 장면의 부모 클립을 같은 모델로 만들 때 걸린 시간
    assert not api.calls_to("GET", PARENT_STATUS) and not api.submits


def test_preview_estimates_the_edit_time_from_edits_this_process_has_made(ir, store):
    """편집에 걸리는 시간은 그 모델로 만든 기록이 있을 때만 어림한다 (부모 클립은 생성 모델로 만든 것이다)."""
    api = _api()
    generator = make_openrouter_generator(store, api, **EDITING)
    assert generator.preview(_request(ir, store)).seconds is None

    clip = generator.generate_scene(_request(ir, store))

    preview = generator.preview(_request(ir, store, scene_key="closeup"))
    assert preview.method is ProductionMethod.EDIT
    assert preview.seconds == pytest.approx(clip.metadata["generation_seconds"], abs=0.1)


def test_preview_says_generate_when_the_edit_model_cannot_make_the_scene(ir, store):
    """계획이 '편집' 이라고 답한 장면이 실행에서는 새로 생성되면 계획과 실행이 어긋난다 (감사 A-36)."""
    api = _api()
    generator = make_openrouter_generator(
        store, api, edit_mode="video_reference", edit_model="vendor/not-listed"
    )

    preview = generator.preview(_request(ir, store))

    assert (preview.method, preview.model) == (ProductionMethod.GENERATE, "alibaba/wan-3.0")
    assert preview.cost_usd == pytest.approx(0.2)  # 새로 생성하는 값: 4초 x $0.05 (480p)
    assert "vendor/not-listed" in preview.edit_unavailable_reason
    assert "FRAME_VIDEO_EDIT_MODEL" in preview.edit_unavailable_reason
    assert not api.submits


def test_preview_keeps_saying_edit_when_only_the_price_list_is_unreadable(ir, store):
    """목록을 못 받은 것은 편집할 수 없다는 뜻이 아니다. 방식은 그대로 알리고 어림 비용만 비운다."""
    api = _api()
    api.on("GET", "/api/v1/videos/models", error_response(503, "upstream busy"))
    generator = make_openrouter_generator(store, api, **EDITING)

    preview = generator.preview(_request(ir, store))

    assert (preview.method, preview.cost_usd) == (ProductionMethod.EDIT, None)
    assert preview.edit_unavailable_reason is None


def test_preview_still_answers_when_the_price_list_cannot_be_read(ir, store):
    api = _api()
    api.on("GET", "/api/v1/videos/models", error_response(503, "upstream busy"))
    generator = make_openrouter_generator(store, api)

    preview = generator.preview(_request(ir, store))

    assert (preview.method, preview.cost_usd) == (ProductionMethod.GENERATE, None)


def test_negative_preview_settings_are_configuration_errors(store):
    with pytest.raises(ConfigurationError, match="0 이상"):
        make_openrouter_generator(store, edit_preview_wait_seconds=-1)
    with pytest.raises(ConfigurationError, match="0 이상"):
        make_openrouter_generator(store, parent_url_hold_seconds=-1)


def test_a_held_address_is_dropped_when_the_edit_made_with_it_fails(ir, store):
    """들고 있던 주소로 접수된 편집 작업이 실패하면 그 주소를 버린다. 다음에는 부모 작업을 다시 조회한다."""
    api = _api()
    api.on(
        "GET",
        f"/api/v1/videos/{JOB_ID}",
        status("failed", JOB_ID, error="could not fetch the input video"),
    )
    generator = make_openrouter_generator(store, api, **EDITING)
    generator.preview(_request(ir, store))

    with pytest.raises(GenerationError):
        generator.generate_scene(_request(ir, store))
    again = generator.preview(_request(ir, store))

    assert again.method is ProductionMethod.EDIT
    assert (
        len(api.calls_to("GET", PARENT_STATUS)) == 2
    )  # 미리보기 -> (들고 있던 주소로 편집) -> 다시 조회


def test_a_held_address_is_dropped_when_the_edit_request_is_rejected(ir, store):
    api = _api(
        error_response(400, "Invalid reference URL: input_references[0].video_url.url"), accepted()
    )
    generator = make_openrouter_generator(store, api, **EDITING)
    generator.preview(_request(ir, store))

    clip = generator.generate_scene(_request(ir, store))  # 거절 -> 새로 생성
    generator.preview(_request(ir, store, scene_key="closeup"))

    assert clip.metadata["edit"]["applied"] is False
    assert len(api.calls_to("GET", PARENT_STATUS)) == 2  # 버린 주소를 다시 쓰지 않고 조회했다


def test_preview_requests_give_up_sooner_than_generation_requests(ir, store):
    """변경 계획 응답이 위쪽 서버 때문에 몇 분씩 멈추지 않게, 미리보기의 조회는 10초 안에 포기한다."""
    api = _api()
    generator = make_openrouter_generator(store, api, **EDITING)

    generator.preview(_request(ir, store))

    assert api.calls and all(call.timeout <= 10 for call in api.calls)
    api.calls.clear()
    generator.generate_scene(_request(ir, store, job_id="gen-vid-1794447999-zYxWvUtSrQpOnMlKjIhG"))
    lookups = api.calls_to("GET", "/api/v1/videos/gen-vid-1794447999-zYxWvUtSrQpOnMlKjIhG")
    assert not lookups or all(call.timeout == 60 for call in lookups)


# ---- 일시적인 사정으로 새로 생성한 결과는 편집 요청의 키에 남기지 않는다 (ADR-0021) ---------------

EDIT_JOB = "gen-vid-1794448000-eDiTeDiTeDiTeDiTeDiT"


def _ledger_status(store, generator, result_key):
    record = ArtifactJobLedger(store, "openrouter").get(
        generation_key(result_key, generator.profile())
    )
    return record.status if record is not None else None


def _not_yet_then_available(*post_responses):
    """부모 작업의 저장본 주소가 처음 한 번은 없고, 그 뒤로는 있다."""
    api = _api(*post_responses)
    api.on("GET", PARENT_STATUS, NO_URLS, completed(PARENT_JOB))
    api.add_job(EDIT_JOB)
    return api


def test_a_scene_regenerated_because_the_address_was_not_there_yet_is_edited_next_time(ir, store):
    """2차 실호출: 방금 만든 영상을 바로 수정하면 주소가 없어 새로 생성된다. 그 결과가 편집 요청의 키에 남으면,
    나중에 계획이 '편집' 이라고 답해도 같은 요청은 그 결과를 다시 쓴다. 조건 키로만 남기고 편집 키는 비운다.
    """
    api = _not_yet_then_available(accepted(), accepted(EDIT_JOB))
    generator = make_openrouter_generator(store, api, edit_parent_wait_seconds=0, **EDITING)
    request = _request(ir, store)

    first = generator.generate_scene(request)
    second = generator.generate_scene(replace(request, version_id="ver_2"))

    generated, edited = (call.json() for call in api.submits)
    assert generated["prompt"].startswith("Scene:") and "input_references" not in generated
    assert edited["input_references"][0]["video_url"]["url"] == SIGNED_URL
    assert first.metadata["edit"]["applied"] is False and first.metadata["provisional"] is True
    assert "다시 요청" in first.metadata["edit"]["fallback_reason"]
    assert second.metadata["edit"]["applied"] is True and "provisional" not in second.metadata
    assert second.metadata["model"] == "black-forest-labs/flux-video-edit"
    # 원장: 새로 생성한 결과는 조건 키에, 편집한 결과는 편집 요청의 키에 있다
    assert _ledger_status(store, generator, request.generation_fingerprint) == "completed"
    assert _ledger_status(store, generator, generator.result_key(request)) == "completed"


def test_the_edit_key_is_released_after_a_provisional_regeneration(ir, store):
    api = _api(parent_status=NO_URLS)
    generator = make_openrouter_generator(store, api, edit_parent_wait_seconds=0, **EDITING)
    request = _request(ir, store)

    generator.generate_scene(request)

    assert _ledger_status(store, generator, generator.result_key(request)) == "released"
    assert _ledger_status(store, generator, request.generation_fingerprint) == "completed"


def test_a_provisional_regeneration_is_reused_while_the_address_is_still_missing(ir, store):
    """주소가 계속 없으면 또 생성(과금)하지 않고 조건 키의 결과를 다시 쓴다. 그때도 편집 키에는 남기지 않는다."""
    api = _api(parent_status=NO_URLS)
    generator = make_openrouter_generator(store, api, edit_parent_wait_seconds=0, **EDITING)
    request = _request(ir, store)
    first = generator.generate_scene(request)

    again = generator.generate_scene(replace(request, version_id="ver_2"))

    assert len(api.submits) == 1
    assert again.uri == first.uri and again.metadata["ledger"] == "reused"
    assert again.metadata["edit"]["applied"] is False and again.metadata["provisional"] is True
    assert _ledger_status(store, generator, generator.result_key(request)) == "released"


def test_a_provisional_regeneration_is_still_reused_days_later(ir, store):
    """조건 키에만 남은 완료 결과는 생성 캐시에 없다. 클립이 남아 있으면 기한이 지나도 다시 내지 않는다 (감사 A-32)."""
    api = _api(parent_status=NO_URLS)
    request = _request(ir, store)
    first = make_openrouter_generator(
        store, api, edit_parent_wait_seconds=0, **EDITING
    ).generate_scene(request)

    later = FakeTime(wall=FakeTime().wall + 25 * 3600)
    again = make_openrouter_generator(
        store, api, clock=later, edit_parent_wait_seconds=0, **EDITING
    ).generate_scene(replace(request, version_id="ver_2"))

    assert len(api.submits) == 1
    assert again.uri == first.uri and again.metadata["ledger"] == "reused"


def test_a_provisional_regeneration_serves_requests_that_do_not_edit(ir, store):
    """편집을 끈 설정이 같은 조건을 요청하면 그 결과를 쓴다. 남의 편집 시도 기록과 임시 표시는 따라오지 않는다."""
    api = _api(parent_status=NO_URLS)
    request = _request(ir, store)
    editing = make_openrouter_generator(store, api, edit_parent_wait_seconds=0, **EDITING)
    regenerated = editing.generate_scene(request)

    plain = make_openrouter_generator(store, api)
    clip = plain.generate_scene(replace(request, version_id="ver_2"))

    assert len(api.submits) == 1 and clip.uri == regenerated.uri
    assert "edit" not in clip.metadata and "provisional" not in clip.metadata


def test_an_interrupted_provisional_regeneration_is_resumed_not_submitted_again(ir, store):
    """새로 생성하는 작업이 접수된 뒤 끊겼다. 다음 요청은 주소가 나와 있어도 그 작업을 이어 받는다.

    두 번 내지 않는다 (편집을 새로 제출하지 않는다).
    """
    api = _not_yet_then_available(accepted(), accepted(EDIT_JOB))
    api.on("GET", f"/api/v1/videos/{JOB_ID}", status("in_progress", JOB_ID))
    request = _request(ir, store)
    with pytest.raises(GenerationError):
        make_openrouter_generator(
            store,
            api,
            edit_parent_wait_seconds=0,
            timeout_seconds=1,
            poll_interval_seconds=1,
            **EDITING,
        ).generate_scene(request)
    api.on("GET", f"/api/v1/videos/{JOB_ID}", completed(JOB_ID))  # 그 사이 작업이 끝났다

    generator = make_openrouter_generator(store, api, edit_parent_wait_seconds=0, **EDITING)
    resumed = generator.generate_scene(replace(request, version_id="ver_2"))

    assert len(api.submits) == 1  # 다시 제출하지 않았다
    assert resumed.metadata["job_id"] == JOB_ID and resumed.metadata["ledger"] == "resumed"
    assert resumed.metadata["edit"]["applied"] is False and resumed.metadata["provisional"] is True
    # 그 뒤의 같은 요청은 편집을 시도한다
    edited = generator.generate_scene(replace(request, version_id="ver_3"))
    assert len(api.submits) == 2 and edited.metadata["edit"]["applied"] is True


@pytest.mark.parametrize(
    "parent_status",
    [status("expired", PARENT_JOB), error_response(404, "Resource not found")],
)
def test_a_scene_regenerated_because_the_copy_is_gone_keeps_the_edit_key(ir, store, parent_status):
    """저장본이 없거나 만료된 것은 나아지지 않는다. 지금까지처럼 편집 요청의 키에 남겨 다시 시도하지 않는다."""
    api = _api(parent_status=parent_status)
    generator = make_openrouter_generator(store, api, **EDITING)
    request = _request(ir, store)

    first = generator.generate_scene(request)
    again = generator.generate_scene(replace(request, version_id="ver_2"))

    assert len(api.submits) == 1 and again.metadata["ledger"] == "reused"
    assert "provisional" not in first.metadata and "provisional" not in again.metadata
    assert "다시 요청" not in first.metadata["edit"]["fallback_reason"]
    assert (
        len(api.calls_to("GET", PARENT_STATUS)) == 1
    )  # 두 번째 요청은 부모 작업을 조회하지도 않는다
    assert _ledger_status(store, generator, generator.result_key(request)) == "completed"


def test_rejected_edits_still_keep_the_edit_key(ir, store):
    """접수 전에 거절된 편집(모델이 받지 않음)도 나아지지 않는다: 임시 결과가 아니다."""
    api = _api(error_response(400, "video input is not supported"), accepted())
    generator = make_openrouter_generator(store, api, **EDITING)

    clip = generator.generate_scene(_request(ir, store))

    assert clip.metadata["edit"]["applied"] is False and "provisional" not in clip.metadata


def test_a_provisional_regeneration_does_not_displace_another_job_for_the_same_conditions(
    ir, store
):
    """같은 조건을 편집 없이 만드는 작업이 진행 중이면 그 원장 항목을 덮어쓰지 않는다.

    그때는 이 결과를 가리키는 곳이 편집 요청의 키뿐이므로 비우지 않는다 (비우면 다음 요청이 또 생성한다).
    """
    in_flight = "gen-vid-1794450000-" + "P" * 20
    api = _api(accepted(in_flight), accepted(), parent_status=NO_URLS)
    api.add_job(in_flight, status("in_progress", in_flight))
    request = _request(ir, store)
    plain = make_openrouter_generator(store, api, timeout_seconds=1, poll_interval_seconds=1)
    with pytest.raises(GenerationError):
        plain.generate_scene(replace(request, parent=None))  # 기다리다 시간 초과. 원장에는 남는다

    editing = make_openrouter_generator(store, api, edit_parent_wait_seconds=0, **EDITING)
    clip = editing.generate_scene(request)
    again = editing.generate_scene(replace(request, version_id="ver_2"))

    assert clip.metadata["job_id"] == JOB_ID and len(api.submits) == 2
    assert "provisional" not in clip.metadata
    assert again.uri == clip.uri and len(api.submits) == 2
    assert _ledger_status(store, editing, editing.result_key(request)) == "completed"
    assert _ledger_status(store, plain, request.generation_fingerprint) == "submitted"


def test_the_cache_does_not_keep_a_provisional_regeneration(ir, store):
    """생성 캐시를 켠 구성: 임시 결과는 캐시에 남지 않아, 다음 요청이 생성기까지 가서 편집된다."""
    api = _not_yet_then_available(accepted(), accepted(EDIT_JOB))
    cached = CachingVideoGenerator(
        make_openrouter_generator(store, api, edit_parent_wait_seconds=0, **EDITING)
    )
    request = _request(ir, store)

    first = cached.generate_scene(request)
    second = cached.generate_scene(replace(request, version_id="ver_2"))
    third = cached.generate_scene(replace(request, version_id="ver_3"))

    assert first.metadata["provisional"] is True
    assert second.metadata["edit"]["applied"] is True and len(api.submits) == 2
    assert third.metadata["cache_hit"] is True and third.uri == second.uri  # 편집 결과는 캐시된다
    assert cached.hits == 1


def test_a_provisional_result_whose_clip_was_deleted_is_downloaded_again(tmp_path, ir):
    """조건 키로 옮긴 결과의 클립 파일이 지워졌어도 다시 생성(과금)하지 않는다: 끝난 작업의 결과를 다시 내려받는다.

    편집 요청의 키를 비우기 전에는 그 키의 완료 기록으로 다시 내려받았다. 비운 뒤에도 그래야 한다.
    """
    store = LocalFileArtifactStore(tmp_path)
    api = _api(parent_status=NO_URLS)
    generator = make_openrouter_generator(store, api, edit_parent_wait_seconds=0, **EDITING)
    request = _request(ir, store)
    first = generator.generate_scene(request)
    store.local_path(first.uri).unlink()

    again = generator.generate_scene(replace(request, version_id="ver_2"))

    assert len(api.submits) == 1  # 다시 제출하지 않았다
    assert len(api.calls_to("GET", f"/api/v1/videos/{JOB_ID}/content")) == 2
    assert store.exists(again.uri) and again.metadata["job_id"] == JOB_ID
    assert again.metadata["edit"]["applied"] is False and again.metadata["provisional"] is True
    assert _ledger_status(store, generator, generator.result_key(request)) == "released"
