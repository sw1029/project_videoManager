"""OpenRouter 어댑터의 부모 클립 편집 (ADR-0016). 네트워크와 ffmpeg 없이 돈다.

- previous_job: 부모 클립을 만든 작업을 previous_job_id 로 이어서 편집 (같은 모델·엔드포인트).
- video_reference: 부모 클립을 input_references 의 video_url 로 편집 모델에 보낸다.
- 제출이 접수 전에 거절되면(400 등) 과금 없이 지금처럼 새로 생성한다.
"""

from __future__ import annotations

import base64
from dataclasses import replace

import pytest

from frame.adapters.generator.openrouter import (
    EDIT_KEEP_LINE,
    VideoModelInfo,
    compile_edit_prompt,
)
from frame.adapters.storage import InMemoryArtifactStore
from frame.application.errors import ConfigurationError, GenerationError
from frame.application.ports.generator import GeneratedClip, ParentClip, SceneGenerationRequest
from frame.config import Settings, build_container
from frame.domain.ir import RequirementSpec
from frame.domain.services.propagation import AttributeChange, DefaultPropagationPolicy
from tests.support.openrouter import (
    JOB_ID,
    WAN_30,
    FakeOpenRouterApi,
    accepted,
    error_response,
    make_openrouter_generator,
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


def _api(*post_responses):
    api = FakeOpenRouterApi(models=(WAN_30, FLUX_EDIT))
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

    data_uri = "data:video/mp4;base64," + base64.b64encode(PARENT_BYTES).decode("ascii")
    # 편집 전용 모델은 길이·해상도·화면비·seed·오디오 옵션을 받지 않는다: 목록에 없는 값은 보내지 않는다
    assert api.submits[0].json() == {
        "model": "black-forest-labs/flux-video-edit",
        "prompt": compile_edit_prompt(_request(ir, store)),
        "input_references": [{"type": "video_url", "video_url": {"url": data_uri}}],
    }
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


def test_missing_parent_clip_file_means_regeneration(ir, store):
    api = _api()
    generator = make_openrouter_generator(store, api, edit_mode="video_reference")
    request = _request(ir, store)
    gone = replace(
        request,
        parent=replace(
            request.parent, clip=replace(request.parent.clip, uri="mem://clips/ver_0/gone.mp4")
        ),
    )

    generator.generate_scene(gone)

    assert "input_references" not in api.submits[0].json()


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
    other_parent = replace(
        request,
        parent=replace(
            request.parent,
            clip=replace(
                request.parent.clip,
                uri=store.put("clips/ver_9/intro.mp4", b"OTHER", "video/mp4"),
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
    defaults = Settings.from_env({})
    assert (defaults.video_edit_mode, defaults.video_edit_model) == ("off", None)
