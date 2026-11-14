"""OpenRouterVideoGenerator: 요청 형식·폴링·다운로드·정규화·오류 매핑·비용 안전 규칙. 네트워크와 ffmpeg 없이 돈다."""

from __future__ import annotations

import base64
import json
from dataclasses import replace

import pytest

from frame.adapters.generator import RetryingVideoGenerator
from frame.adapters.generator.openrouter import (
    CONSISTENCY_LINE,
    OpenRouterVideoGenerator,
    compile_video_prompt,
    output_size,
    resolve_openrouter_key,
)
from frame.adapters.shared.http import TransportError
from frame.adapters.storage import InMemoryArtifactStore
from frame.application.errors import ConfigurationError, GenerationError, GenerationTimeout
from frame.application.ports.generator import SceneGenerationRequest
from frame.application.ports.structurer import ReferenceImageInput
from frame.domain.enums import EntityKind
from frame.domain.ir import EntitySpec, RequirementSpec, SceneSpec
from tests.support.openrouter import (
    JOB_ID,
    LANDSCAPE_ONLY,
    NO_SEED_5_10,
    PNG_BYTES,
    SIGNED_URL,
    TEST_KEY,
    WAN_30,
    FakeNormalizer,
    FakeOpenRouterApi,
    FakeTime,
    accepted,
    completed,
    error_response,
    json_response,
    make_openrouter_generator,
    status,
    video_content,
)

CONTENT_PATH = f"/api/v1/videos/{JOB_ID}/content"
STATUS_PATH = f"/api/v1/videos/{JOB_ID}"


@pytest.fixture
def store():
    s = InMemoryArtifactStore()
    s.put("refs/cup.png", PNG_BYTES, "image/png")
    return s


def _request(ir, scene_key="intro", seed=7, references=None, version_id="ver_1"):
    scene = ir.scene(scene_key)
    refs = (ReferenceImageInput("mem://refs/cup.png", "cup"),) if references is None else references
    return SceneGenerationRequest(
        version_id=version_id,
        scene=scene,
        entities=tuple(ir.entity(k) for k in scene.entity_keys),
        references=refs,
        seed=seed,
    )


def _scene_request(duration_ms, seed=7):
    scene = SceneSpec("s1", 0, duration_ms, "한 장면", ("cup",))
    return SceneGenerationRequest(
        "ver_1", scene, (EntitySpec("cup", EntityKind.PROP, "머그컵"),), seed=seed
    )


# ---- 프롬프트 (영어 틀 + 사용자 설명은 그대로) ---------------------------------------


def test_prompt_uses_english_frame_and_keeps_user_text_verbatim(ir):
    prompt = compile_video_prompt(_request(ir, references=()))
    lines = prompt.splitlines()
    assert lines[0] == "Scene: 바리스타가 카운터에서 컵을 들어 보인다"
    assert "Subjects:" in lines
    assert "- barista (person): 흰 셔츠를 입은 바리스타; shirt color: white; holds cup" in lines
    assert "- cup (prop): 세라믹 머그컵; color: red" in lines
    assert "- cafe (background): 따뜻한 조명의 카페; lighting: warm" in lines


def test_prompt_includes_scene_level_conditions():
    scene = SceneSpec(
        "s1",
        0,
        4000,
        "골목길",
        ("cat",),
        (
            RequirementSpec("s1", "mood", "calm"),
            RequirementSpec("s1", "time_of_day", "night"),
            RequirementSpec("s1", "color", "black", "cat"),
        ),
    )
    request = SceneGenerationRequest(
        "ver_1", scene, (EntitySpec("cat", EntityKind.ANIMAL, "고양이"),)
    )
    lines = compile_video_prompt(request).splitlines()
    assert "- cat (animal): 고양이; color: black" in lines
    assert "Scene conditions: mood: calm; time of day: night" in lines


def test_prompt_points_entities_to_their_reference_images(ir):
    prompt = compile_video_prompt(_request(ir), {"cup": [1]})
    assert "- cup (prop, Image 1): 세라믹 머그컵; color: red" in prompt.splitlines()


# ---- 길이 선택 ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "duration_ms", "expected_seconds"),
    [
        (WAN_30, 4000, 4),
        (WAN_30, 3500, 4),
        (WAN_30, 1500, 2),  # Wan 3.0 최소 2초
        (WAN_30, 30000, 30),
        (NO_SEED_5_10, 4000, 5),
        (NO_SEED_5_10, 5000, 5),
        (NO_SEED_5_10, 5001, 10),
    ],
)
def test_duration_is_the_shortest_allowed_length_covering_the_scene(
    store, model, duration_ms, expected_seconds
):
    api = FakeOpenRouterApi(models=[model])
    gen = make_openrouter_generator(
        store, api, model=model["id"], resolution=model["supported_resolutions"][0]
    )
    clip = gen.generate_scene(_scene_request(duration_ms))
    assert api.submits[0].json()["duration"] == expected_seconds
    assert clip.duration_ms == duration_ms
    assert clip.metadata["billed_seconds"] == expected_seconds
    assert clip.metadata["requested_seconds"] == duration_ms / 1000


def test_scene_longer_than_the_model_allows_fails_before_submitting(store):
    api = FakeOpenRouterApi()
    with pytest.raises(GenerationError, match="30") as excinfo:
        make_openrouter_generator(store, api).generate_scene(_scene_request(31000))
    assert excinfo.value.retryable is False
    assert api.submits == []


# ---- 제출 요청 형식 ---------------------------------------------------------------


def test_submit_body_follows_the_openrouter_contract(store, ir):
    api = FakeOpenRouterApi()
    make_openrouter_generator(store, api).generate_scene(_request(ir))

    (submit,) = api.submits
    assert submit.url == "https://openrouter.ai/api/v1/videos"
    assert submit.header("Authorization") == f"Bearer {TEST_KEY}"
    assert submit.header("Content-Type") == "application/json"
    body = submit.json()
    assert body["model"] == "alibaba/wan-3.0"
    assert body["prompt"].startswith("Scene: 바리스타가 카운터에서 컵을 들어 보인다")
    assert body["duration"] == 4
    assert body["aspect_ratio"] == "9:16"
    assert body["resolution"] == "480p"
    assert body["seed"] == 7
    assert body["generate_audio"] is False
    assert "frame_images" not in body  # 함께 보내면 참조 이미지가 무시된다 (image-to-video 로 처리)
    expected_url = "data:image/png;base64," + base64.b64encode(PNG_BYTES).decode("ascii")
    assert body["input_references"] == [{"type": "image_url", "image_url": {"url": expected_url}}]
    assert TEST_KEY.encode() not in submit.body


def test_seed_is_sent_only_when_the_model_supports_it(store, ir):
    api = FakeOpenRouterApi(models=[NO_SEED_5_10])
    gen = make_openrouter_generator(store, api, model="vendor/no-seed", resolution="720p")
    clip = gen.generate_scene(_request(ir, seed=7))
    assert "seed" not in api.submits[0].json()
    assert clip.metadata["seed"] is None

    api = FakeOpenRouterApi()
    make_openrouter_generator(store, api).generate_scene(_request(ir, seed=None))
    assert "seed" not in api.submits[0].json()


def test_scene_without_references_sends_no_input_references(store, ir):
    api = FakeOpenRouterApi()
    make_openrouter_generator(store, api).generate_scene(_request(ir, references=()))
    assert "input_references" not in api.submits[0].json()


def test_http_and_data_reference_uris_are_passed_through(store, ir):
    data_uri = "data:image/jpeg;base64,AAAA"
    refs = (
        ReferenceImageInput("https://example.com/cup.png", "cup"),
        ReferenceImageInput(data_uri, None),
    )
    api = FakeOpenRouterApi()
    make_openrouter_generator(store, api).generate_scene(_request(ir, references=refs))
    urls = [r["image_url"]["url"] for r in api.submits[0].json()["input_references"]]
    assert urls == ["https://example.com/cup.png", data_uri]


@pytest.mark.parametrize(
    "uri", ["C:/Users/USER/secret.png", "file:///etc/passwd", "../refs/cup.png", "mem://refs/x.png"]
)
def test_references_outside_the_artifact_store_are_rejected(store, ir, uri):
    api = FakeOpenRouterApi()
    with pytest.raises(GenerationError, match="참조 이미지") as excinfo:
        make_openrouter_generator(store, api).generate_scene(
            _request(ir, references=(ReferenceImageInput(uri, "cup"),))
        )
    assert excinfo.value.retryable is False
    assert api.submits == []


def test_non_image_reference_is_rejected(store, ir):
    uri = store.put("refs/notes.json", b"{}", "application/json")
    api = FakeOpenRouterApi()
    with pytest.raises(GenerationError, match="이미지"):
        make_openrouter_generator(store, api).generate_scene(
            _request(ir, references=(ReferenceImageInput(uri, "cup"),))
        )
    assert api.submits == []


# ---- 정상 흐름: 제출 -> 폴링 -> 다운로드 -> 정규화 -> 저장 ----------------------------------


def test_generates_a_normalized_mp4_clip(store, ir):
    api = FakeOpenRouterApi()
    normalizer = FakeNormalizer()
    clock = FakeTime()
    gen = make_openrouter_generator(store, api, normalizer, clock, poll_interval_seconds=20)
    request = _request(ir)

    clip = gen.generate_scene(request)

    assert clip.scene_key == "intro"
    assert clip.fingerprint == request.fingerprint
    assert clip.duration_ms == request.scene.duration_ms == 4000
    assert clip.uri == "mem://clips/ver_1/intro.mp4"
    assert store.get(clip.uri) == b"MP4:RAW-MP4-BYTES"
    assert store.content_type(clip.uri) == "video/mp4"
    assert normalizer.calls == [
        {"source": b"RAW-MP4-BYTES", "duration_ms": 4000, "fps": 30, "width": 480, "height": 854}
    ]
    # 폴링: 접수 직후 한 번 기다리고, in_progress -> completed
    assert len(api.calls_to("GET", STATUS_PATH)) == 2
    assert clock.sleeps == [20, 20]
    (download,) = api.calls_to("GET", CONTENT_PATH)
    assert download.query == {"index": ["0"]}
    assert download.header("Authorization") == f"Bearer {TEST_KEY}"

    meta = clip.metadata
    assert meta["generator"] == "openrouter"
    assert meta["model"] == "alibaba/wan-3.0"
    assert meta["job_id"] == JOB_ID
    assert meta["generation_id"] == "gen-xyz789"
    assert meta["cost_usd"] == 0.2
    assert meta["estimated_cost_usd"] == pytest.approx(0.2)  # 480p $0.05/s x 4s
    assert meta["requested_seconds"] == 4.0
    assert meta["billed_seconds"] == 4
    assert meta["seed"] == 7
    assert meta["resolution"] == "480p"
    assert meta["aspect_ratio"] == "9:16"
    assert meta["reference_count"] == 1
    assert meta["polls"] == 2
    assert meta["generation_seconds"] == pytest.approx(40.0)
    assert meta["output"] == {
        "width": 480,
        "height": 854,
        "fps": 30.0,
        "frames": 120,
        "duration_ms": 4000,
        "has_audio": False,
        "codec": "h264",
    }
    assert meta["source"]["has_audio"] is True


def test_model_list_is_fetched_once_without_the_key(store, ir):
    api = FakeOpenRouterApi()
    gen = make_openrouter_generator(store, api)
    gen.generate_scene(_request(ir, "intro"))
    gen.generate_scene(_request(ir, "closeup"))
    (models_call,) = api.calls_to("GET", "/api/v1/videos/models")
    assert models_call.header("Authorization") is None


def test_metadata_holds_no_key_and_no_signed_urls(store, ir):
    clip = make_openrouter_generator(store).generate_scene(_request(ir))
    dumped = json.dumps(clip.metadata, ensure_ascii=False)
    assert TEST_KEY not in dumped
    assert SIGNED_URL not in dumped and "SIGNED-SECRET" not in dumped


# ---- 접수 뒤 실패: 다시 제출하지 않는다 (같은 장면 이중 과금 방지) ---------------------------


@pytest.mark.parametrize("state", ["failed", "cancelled", "expired"])
def test_terminal_failure_is_reported_and_never_resubmitted(store, ir, state):
    api = FakeOpenRouterApi()
    api.on("GET", STATUS_PATH, status(state, error="upstream rejected the prompt"))
    gen = RetryingVideoGenerator(make_openrouter_generator(store, api), retries=2)

    with pytest.raises(GenerationError) as excinfo:
        gen.generate_scene(_request(ir))

    assert excinfo.value.retryable is False
    message = str(excinfo.value)
    assert state in message and JOB_ID in message and "upstream rejected the prompt" in message
    assert len(api.submits) == 1
    assert api.calls_to("GET", CONTENT_PATH) == []


def test_timeout_raises_generation_timeout_and_is_not_retried(store, ir):
    api = FakeOpenRouterApi()
    api.on("GET", STATUS_PATH, status("in_progress"))
    clock = FakeTime()
    inner = make_openrouter_generator(
        store, api, clock=clock, timeout_seconds=60, poll_interval_seconds=20
    )

    with pytest.raises(GenerationTimeout) as excinfo:
        RetryingVideoGenerator(inner, retries=2).generate_scene(_request(ir))

    assert excinfo.value.retryable is False
    assert JOB_ID in str(excinfo.value)
    assert len(api.submits) == 1
    assert sum(clock.sleeps) == pytest.approx(60)


def test_polling_rides_out_transient_errors(store, ir):
    api = FakeOpenRouterApi()
    api.on(
        "GET",
        STATUS_PATH,
        error_response(502, "Bad gateway"),
        TransportError("connection reset"),
        error_response(429, "Rate limit exceeded"),
        status("in_progress"),
        completed(),
    )
    clip = make_openrouter_generator(store, api).generate_scene(_request(ir))
    assert clip.metadata["polls"] == 5
    assert len(api.submits) == 1


@pytest.mark.parametrize("code", [408, 409, 425, 500, 501, 520, 524, 599])
def test_polling_rides_out_every_status_that_may_pass(store, ir, code):
    """상태 조회의 5xx 는 과금과 무관하다. 한 번 났다고 대기를 끝내 버전을 실패시키지 않는다 (감사 A-37)."""
    api = FakeOpenRouterApi()
    api.on("GET", STATUS_PATH, error_response(code, "upstream hiccup"), completed())

    clip = make_openrouter_generator(store, api).generate_scene(_request(ir))

    assert clip.metadata["polls"] == 2 and len(api.submits) == 1


def test_polling_stops_on_authentication_errors(store, ir):
    api = FakeOpenRouterApi()
    api.on("GET", STATUS_PATH, error_response(401, "Missing Authentication header"))
    with pytest.raises(GenerationError, match="401") as excinfo:
        make_openrouter_generator(store, api).generate_scene(_request(ir))
    assert excinfo.value.retryable is False
    assert JOB_ID in str(excinfo.value)


def test_download_retries_conflicts_then_succeeds(store, ir):
    api = FakeOpenRouterApi()
    api.on(
        "GET",
        CONTENT_PATH,
        error_response(409, "Resource conflict. Please try again later."),
        video_content(b"LATE-MP4"),
    )
    clip = make_openrouter_generator(store, api).generate_scene(_request(ir))
    assert store.get(clip.uri) == b"MP4:LATE-MP4"
    assert len(api.calls_to("GET", CONTENT_PATH)) == 2
    assert len(api.submits) == 1


@pytest.mark.parametrize("code", [408, 425, 429, 500, 520, 524])
def test_download_rides_out_every_status_that_may_pass(store, ir, code):
    api = FakeOpenRouterApi()
    api.on("GET", CONTENT_PATH, error_response(code, "try later"), video_content(b"LATE-MP4"))

    clip = make_openrouter_generator(store, api).generate_scene(_request(ir))

    assert store.get(clip.uri) == b"MP4:LATE-MP4"
    assert len(api.calls_to("GET", CONTENT_PATH)) == 2 and len(api.submits) == 1


def test_download_rejects_a_non_video_payload(store, ir):
    api = FakeOpenRouterApi()
    api.on("GET", CONTENT_PATH, json_response(200, {"error": "not ready"}))
    with pytest.raises(GenerationError, match="영상") as excinfo:
        make_openrouter_generator(store, api).generate_scene(_request(ir))
    assert excinfo.value.retryable is False
    assert JOB_ID in str(excinfo.value)


def test_normalization_failure_keeps_the_paid_source_video(store, ir):
    normalizer = FakeNormalizer(error=GenerationError("ffmpeg 변환 실패"))
    with pytest.raises(GenerationError) as excinfo:
        make_openrouter_generator(store, normalizer=normalizer).generate_scene(_request(ir))
    assert excinfo.value.retryable is False
    source_uri = "mem://clips/ver_1/intro.source.mp4"
    assert store.get(source_uri) == b"RAW-MP4-BYTES"
    assert source_uri in str(excinfo.value)


# ---- 제출 단계 오류 ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "hint"),
    [
        (400, "요청"),
        (401, "키"),
        (402, "크레딧"),
        (403, "권한"),
        (404, "모델"),
        (413, "크기"),
        (500, "서버"),
        (503, "서버"),
    ],
)
def test_submit_errors_fail_once_without_leaking_the_key(store, ir, code, hint):
    api = FakeOpenRouterApi()
    api.on("POST", "/api/v1/videos", error_response(code, f"upstream said no ({TEST_KEY})"))
    gen = RetryingVideoGenerator(make_openrouter_generator(store, api), retries=2)

    with pytest.raises(GenerationError) as excinfo:
        gen.generate_scene(_request(ir))

    message = str(excinfo.value)
    assert str(code) in message and hint in message
    assert TEST_KEY not in message and "f00d" * 4 not in message
    assert excinfo.value.retryable is False
    assert len(api.submits) == 1


def test_submit_retries_429_and_502_then_succeeds(store, ir):
    api = FakeOpenRouterApi()
    api.on(
        "POST",
        "/api/v1/videos",
        error_response(429, "Rate limit exceeded", {"Retry-After": "3"}),
        error_response(502, "Bad gateway"),
        accepted(),
    )
    clock = FakeTime()
    clip = make_openrouter_generator(store, api, clock=clock).generate_scene(_request(ir))
    assert clip.metadata["job_id"] == JOB_ID
    assert len(api.submits) == 3
    assert clock.sleeps[0] == 3  # Retry-After 를 따른다


def test_submit_gives_up_after_the_attempt_budget_with_a_retryable_error(store, ir):
    api = FakeOpenRouterApi()
    api.on("POST", "/api/v1/videos", error_response(429, "Rate limit exceeded"))
    gen = make_openrouter_generator(store, api, submit_attempts=3)
    with pytest.raises(GenerationError, match="429") as excinfo:
        gen.generate_scene(_request(ir))
    assert excinfo.value.retryable is True  # 접수되지 않았으니 과금도 없다
    assert len(api.submits) == 3


def test_submit_without_a_response_is_not_retried(store, ir):
    api = FakeOpenRouterApi()
    api.on("POST", "/api/v1/videos", TransportError("read timed out"))
    gen = RetryingVideoGenerator(make_openrouter_generator(store, api), retries=2)
    with pytest.raises(GenerationError, match="접수") as excinfo:
        gen.generate_scene(_request(ir))
    assert excinfo.value.retryable is False
    assert len(api.submits) == 1


# ---- 모델 메타데이터 검사 (제출 전에 실패) -------------------------------------------------


def test_unknown_model_fails_before_submitting(store, ir):
    api = FakeOpenRouterApi()
    with pytest.raises(GenerationError, match="vendor/missing") as excinfo:
        make_openrouter_generator(store, api, model="vendor/missing").generate_scene(_request(ir))
    assert excinfo.value.retryable is False
    assert api.submits == []


def test_unsupported_aspect_ratio_or_resolution_fails_before_submitting(store, ir):
    api = FakeOpenRouterApi(models=[LANDSCAPE_ONLY])
    gen = make_openrouter_generator(store, api, model="vendor/landscape-only", resolution="720p")
    with pytest.raises(GenerationError, match="9:16"):
        gen.generate_scene(_request(ir))

    api = FakeOpenRouterApi()
    with pytest.raises(GenerationError, match="4K"):
        make_openrouter_generator(store, api, resolution="4K").generate_scene(_request(ir))
    assert api.submits == []


def test_model_list_outage_is_retryable_and_submits_nothing(store, ir):
    api = FakeOpenRouterApi()
    api.on("GET", "/api/v1/videos/models", error_response(503, "Service unavailable"))
    with pytest.raises(GenerationError, match="503") as excinfo:
        make_openrouter_generator(store, api).generate_scene(_request(ir))
    assert excinfo.value.retryable is True
    assert api.submits == []


# ---- 키·설정 ----------------------------------------------------------------------


def test_key_resolution_prefers_explicit_then_env_then_file(tmp_path):
    key_file = tmp_path / "openrouter.key"
    key_file.write_text("OPENROUTER_API_KEY=sk-or-v1-fromfile\n", encoding="utf-8")
    env = {"OPENROUTER_API_KEY": "sk-or-v1-fromenv"}
    assert resolve_openrouter_key("sk-or-v1-explicit", env, key_file) == "sk-or-v1-explicit"
    assert resolve_openrouter_key(None, env, key_file) == "sk-or-v1-fromenv"
    assert resolve_openrouter_key(None, {}, key_file) == "sk-or-v1-fromfile"
    key_file.write_text("  sk-or-v1-bare \n", encoding="utf-8")
    assert resolve_openrouter_key(None, {}, key_file) == "sk-or-v1-bare"


def test_key_file_with_another_providers_key_is_refused_without_leaking(tmp_path):
    key_file = tmp_path / "key.txt"
    key_file.write_text("sk-proj-THIS-IS-AN-OPENAI-KEY\n", encoding="utf-8")
    with pytest.raises(ConfigurationError) as excinfo:
        resolve_openrouter_key(None, {}, key_file)
    assert "OPENAI-KEY" not in str(excinfo.value)
    assert "sk-or-" in str(excinfo.value)


def test_missing_key_is_a_configuration_error(tmp_path):
    with pytest.raises(ConfigurationError, match="OPENROUTER_API_KEY"):
        resolve_openrouter_key(None, {}, None)
    with pytest.raises(ConfigurationError, match="키 파일"):
        resolve_openrouter_key(None, {}, tmp_path / "missing.key")


@pytest.mark.parametrize(
    "base_url", ["http://openrouter.ai/api/v1", "ftp://openrouter.ai", "openrouter.ai/api/v1"]
)
def test_key_is_never_sent_over_plain_http_to_a_remote_host(store, base_url):
    with pytest.raises(ConfigurationError, match="https"):
        make_openrouter_generator(store, base_url=base_url)


def test_plain_http_is_allowed_for_a_local_test_server(store):
    gen = make_openrouter_generator(store, base_url="http://127.0.0.1:8099/api/v1/")
    assert isinstance(gen, OpenRouterVideoGenerator)


@pytest.mark.parametrize(
    "key",
    ["sk-or-v1-first\nsecond", "sk-or-v1-first second", "sk-or-v1-한글", "sk-or-v1-nul\x00byte"],
    ids=["newline", "space", "non-ascii", "control"],
)
def test_a_key_that_a_header_cannot_carry_is_refused_at_startup(store, key):
    """키에 줄바꿈·공백이 끼면 요청을 보낼 때 프로토콜 오류가 나고, 그 문구에 키가 통째로 들어 있다 (감사 A-38).

    기동할 때 키의 내용 없이 알린다.
    """
    with pytest.raises(ConfigurationError, match="쓸 수 없는 글자") as excinfo:
        make_openrouter_generator(store, api_key=key)

    assert "sk-or" not in str(excinfo.value) and "second" not in str(excinfo.value)


def test_key_hint_shows_only_the_last_four_characters(store):
    gen = make_openrouter_generator(store)
    assert gen.key_hint == "..." + TEST_KEY[-4:]
    assert TEST_KEY not in repr(gen)


@pytest.mark.parametrize(
    ("resolution", "aspect_ratio", "size"),
    [
        ("480p", "9:16", (480, 854)),
        ("720p", "9:16", (720, 1280)),
        ("1080p", "9:16", (1080, 1920)),
        ("720p", "16:9", (1280, 720)),
        ("720p", "1:1", (720, 720)),
        ("768p", "9:16", (768, 1366)),
    ],
)
def test_output_size_follows_resolution_and_aspect_ratio(resolution, aspect_ratio, size):
    assert output_size(resolution, aspect_ratio) == size


def test_unknown_resolution_label_is_a_configuration_error(store):
    with pytest.raises(ConfigurationError, match="8K"):
        make_openrouter_generator(store, resolution="8K")


# ---- 정체성 기준 이미지 (ADR-0015) ----------------------------------------------------


def _anchored(ir, store, scene_key="outro", description="", **kwargs):
    """barista 의 정체성 기준 이미지를 실은 요청 (사용자 참조는 없음)."""
    uri = store.put("references/identity/abc/barista.png", PNG_BYTES + b"-barista", "image/png")
    request = _request(ir, scene_key=scene_key, references=(), **kwargs)
    identity = (ReferenceImageInput(uri, "barista", description),)
    return replace(request, identity_references=identity)


def test_identity_references_are_sent_after_user_references(store, ir):
    api = FakeOpenRouterApi()
    request = replace(
        _anchored(ir, store, scene_key="intro"),
        references=(ReferenceImageInput("mem://refs/cup.png", "cup"),),
    )

    clip = make_openrouter_generator(store, api).generate_scene(request)

    body = api.submits[0].json()
    urls = [r["image_url"]["url"] for r in body["input_references"]]
    assert urls == [
        "data:image/png;base64," + base64.b64encode(PNG_BYTES).decode("ascii"),
        "data:image/png;base64," + base64.b64encode(PNG_BYTES + b"-barista").decode("ascii"),
    ]
    lines = body["prompt"].splitlines()
    assert any(line.startswith("- cup (prop, Image 1)") for line in lines)
    assert any(line.startswith("- barista (person, Image 2)") for line in lines)
    assert lines[-1] == CONSISTENCY_LINE
    assert (clip.metadata["reference_count"], clip.metadata["identity_reference_count"]) == (2, 1)
    assert clip.fingerprint == request.fingerprint


def test_prompt_without_entity_references_has_no_consistency_line(ir):
    assert CONSISTENCY_LINE not in compile_video_prompt(_request(ir, references=()))
    assert CONSISTENCY_LINE not in compile_video_prompt(_request(ir, references=()), {None: [1]})


def test_prompt_carries_the_appearance_read_from_the_identity_image(store, ir):
    request = _anchored(ir, store, description="short black hair, round glasses")
    prompt = compile_video_prompt(request, {"barista": [1]})

    assert (
        "- barista (person, Image 1): 흰 셔츠를 입은 바리스타; "
        "looks like: short black hair, round glasses; shirt color: white"
    ) in prompt.splitlines()


def test_entities_sharing_one_identity_image_send_it_once(store, ir):
    api = FakeOpenRouterApi()
    uri = store.put("references/identity/abc/frame.png", PNG_BYTES, "image/png")
    request = replace(
        _request(ir, references=()),
        identity_references=(
            ReferenceImageInput(uri, "barista"),
            ReferenceImageInput(uri, "cup"),
        ),
    )

    make_openrouter_generator(store, api).generate_scene(request)

    body = api.submits[0].json()
    assert len(body["input_references"]) == 1
    lines = body["prompt"].splitlines()
    assert any(line.startswith("- barista (person, Image 1)") for line in lines)
    assert any(line.startswith("- cup (prop, Image 1)") for line in lines)


def test_ledger_key_follows_the_identity_references(store, ir):
    """같은 조건이라도 기준 이미지가 생기면 다른 작업이다 (원장이 기준 없는 결과를 돌려주지 않는다)."""
    api = FakeOpenRouterApi()
    generator = make_openrouter_generator(store, api)
    plain = _request(ir, scene_key="outro", references=())

    generator.generate_scene(plain)
    generator.generate_scene(_anchored(ir, store))
    again = generator.generate_scene(_anchored(ir, store, version_id="ver_2"))

    assert len(api.submits) == 2
    assert again.metadata["ledger"] == "reused"


def test_several_references_of_one_entity_are_named_in_order(ir):
    prompt = compile_video_prompt(_request(ir), {"cup": [1, 3], None: [2]})
    lines = prompt.splitlines()
    assert "- cup (prop, Image 1 and Image 3): 세라믹 머그컵; color: red" in lines
    assert "Overall look: follow Image 2" in lines


def test_provider_messages_keep_only_the_host_of_any_url(store, ir):
    """제공자가 올려 받은 참조 이미지의 서명 URL 을 오류에 섞어 보내도 기록에는 호스트만 남긴다."""
    signed = (
        "http://files.example-oss.com/restful/data-uri/123/abc?Expires=1794627566"
        "&OSSAccessKeyId=LTAI-SECRET&Signature=SIG-SECRET"
    )
    api = FakeOpenRouterApi().on(
        "GET",
        STATUS_PATH,
        status("failed", error=f"Error validating image resolution: image {signed} is too small"),
    )

    with pytest.raises(GenerationError) as excinfo:
        make_openrouter_generator(store, api).generate_scene(_request(ir))

    message = str(excinfo.value)
    assert "Error validating image resolution" in message and "is too small" in message
    assert "http://files.example-oss.com/..." in message
    assert "SECRET" not in message and "Expires" not in message


@pytest.mark.parametrize(
    ("leaky", "kept", "gone"),
    [
        (
            "upload to https://user:PASSWORD@storage.example/bucket/a.mp4 failed",
            "https://storage.example/...",
            ["PASSWORD", "user:"],
        ),
        (
            "could not fetch storage.example/out.mp4?X-Amz-Signature=SIG-SECRET&X-Amz-Expires=600 in time",
            "storage.example/out.mp4?...",
            ["SIG-SECRET", "X-Amz"],
        ),
        (
            "rejected input_references[0]: data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ end",
            "data:image/png;base64,...",
            ["iVBORw0KGgo"],
        ),
    ],
    ids=["userinfo", "signed-query-without-scheme", "data-uri"],
)
def test_provider_messages_do_not_carry_credentials_or_payloads(store, ir, leaky, kept, gone):
    """제공자의 오류 문구는 버전의 error_message 로 API 응답에 나간다 (감사 A-38)."""
    api = FakeOpenRouterApi().on("GET", STATUS_PATH, status("failed", error=leaky))

    with pytest.raises(GenerationError) as excinfo:
        make_openrouter_generator(store, api).generate_scene(_request(ir))

    message = str(excinfo.value)
    assert kept in message
    assert not [secret for secret in gone if secret in message]


# ---- 수정 지시 (ADR-0017): 새로 생성하는 프롬프트에도 꼭 지켜야 할 값을 적는다 -------------------


def _directed(ir, kind, previous=None, predicate="color", scene_key="intro"):
    from frame.application.ports.generator import RevisionDirective

    request = _request(ir, scene_key)
    enforce = tuple(r for r in request.scene.requirements if r.predicate == predicate)
    return replace(request, directive=RevisionDirective(kind, enforce, previous))


def test_prompt_ends_with_the_requirement_a_revision_must_get_right(ir):
    """수정 뒤에도 객체 설명에 예전 표현이 남을 수 있다 (2026-11-12 실호출: '빨간 머그컵' + color: teal).

    무엇을 고치려는 수정인지 마지막 줄에 따로 적는다.
    """
    from frame.domain.enums import RevisionKind

    changed = compile_video_prompt(_directed(ir, RevisionKind.CHANGE, previous="blue"))
    repaired = compile_video_prompt(_directed(ir, RevisionKind.REPAIR))
    relation = compile_video_prompt(_directed(ir, RevisionKind.REPAIR, predicate="holds"))

    assert changed.splitlines()[-1] == "Must show: cup color: red (changed from blue)."
    assert repaired.splitlines()[-1] == (
        "Must show: cup color: red (an earlier render got this wrong)."
    )
    assert relation.splitlines()[-1] == (
        "Must show: barista holds cup (an earlier render got this wrong)."
    )
    assert "Must show" not in compile_video_prompt(_request(ir))
