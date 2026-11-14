"""어댑터 레지스트리와 기본 등록 (감사 M-02): 설정 키가 어느 어댑터가 되는가.

기본 등록은 포트마다 모듈 하나(frame.config.registrations)에 있다. 등록된 키, 키마다 만들어지는 것,
팩토리끼리의 연결(글·이미지를 읽는 어댑터는 같은 레지스트리에서 LLM 을 찾는다)을 고정한다.
"""

from __future__ import annotations

import pytest

from frame.adapters.llm import ReplayLLMClient
from frame.adapters.shared.runtime import FixedClock, SequentialIdGenerator
from frame.adapters.storage import InMemoryArtifactStore
from frame.application.errors import ConfigurationError
from frame.application.ports import (
    ArtifactStore,
    Checker,
    IdentityReferenceExtractor,
    JobDispatcher,
    JobHandlerRegistry,
    ObjectTracker,
    RequirementStructurer,
    UnitOfWork,
    VideoAssembler,
    VideoGenerator,
)
from frame.application.ports.change_request import ChangeRequestInterpreter
from frame.application.ports.llm import LLMClient
from frame.config import (
    AdapterRegistry,
    BuildContext,
    Settings,
    build_container,
    default_registry,
)

# 포트 -> 설정에 쓸 수 있는 키 (FRAME_GENERATOR=external_api 의 external_api). 어댑터를 더하면 이 표에도 올린다
DEFAULT_KEYS: dict[type, list[str]] = {
    LLMClient: ["fake", "openai"],
    RequirementStructurer: ["fake", "llm", "rule_based"],
    ChangeRequestInterpreter: ["llm", "rule_based"],
    VideoGenerator: ["diffusers", "external_api", "fake"],
    VideoAssembler: ["fake", "ffmpeg"],
    IdentityReferenceExtractor: ["fake", "frame", "off", "vlm_crop"],
    ObjectTracker: ["fake", "opencv"],
    Checker: [
        "fake_consistency",
        "fake_preservation",
        "fake_requirement",
        "ssim_preservation",
        "vlm_consistency",
        "vlm_preservation",
        "vlm_requirement",
    ],
    ArtifactStore: ["local_fs", "memory"],
    UnitOfWork: ["memory", "sqlalchemy"],
    JobDispatcher: ["inline", "recording", "thread"],
}

# 키·패키지·네트워크 같은 바깥 것이 있어야 만들어지는 어댑터. 각자의 테스트가 따로 본다
# (openai: tests/unit/test_settings.py, external_api: tests/unit/generator/test_openrouter_config.py)
NEEDS_THE_OUTSIDE = {(LLMClient, "openai"), (VideoGenerator, "external_api")}

# 자리(설정 키)는 있지만 구현이 아직 없는 뼈대 어댑터. 고르면 서버가 뜨지 않는다 (감사 A-31)
SKELETONS = [(VideoGenerator, "diffusers"), (ObjectTracker, "opencv")]

BUILDABLE_OFFLINE = [
    (port, key)
    for port, keys in DEFAULT_KEYS.items()
    for key in keys
    if (port, key) not in NEEDS_THE_OUTSIDE and (port, key) not in SKELETONS
]


@pytest.fixture
def context(tmp_path):
    """팩토리가 기대는 것을 다 갖춘 조립 맥락. ffmpeg 는 경로만 확인하므로 빈 파일을 준다."""
    ffmpeg = tmp_path / "ffmpeg"
    ffmpeg.write_bytes(b"")
    settings = Settings(
        llm_provider="fake",
        llm_model="text-model",
        vlm_model="vision-model",
        video_ffmpeg=str(ffmpeg),
        artifact_dir=str(tmp_path / "artifacts"),
    )
    return BuildContext(
        settings=settings,
        ids=SequentialIdGenerator(),
        clock=FixedClock(),
        artifact_store=InMemoryArtifactStore(),
        handlers=JobHandlerRegistry(),
    )


@pytest.mark.parametrize("port", DEFAULT_KEYS, ids=lambda port: port.__name__)
def test_the_default_registry_offers_these_keys(port):
    assert default_registry().keys(port) == DEFAULT_KEYS[port]


@pytest.mark.parametrize(
    ("port", "key"), BUILDABLE_OFFLINE, ids=lambda value: getattr(value, "__name__", value)
)
def test_every_default_adapter_can_be_built(port, key, context):
    built = default_registry().build(port, key, context)

    if (port, key) == (IdentityReferenceExtractor, "off"):
        assert built is None  # 기준 이미지를 쓰지 않는다
    elif port is UnitOfWork:
        assert isinstance(built(), UnitOfWork)  # 저장 단위를 여는 팩토리를 돌려준다
    else:
        assert isinstance(built, port)
    if hasattr(built, "shutdown"):
        built.shutdown()


@pytest.mark.parametrize(
    ("port", "key"), SKELETONS, ids=lambda value: getattr(value, "__name__", value)
)
def test_a_skeleton_adapter_is_refused_when_it_is_built(port, key, context):
    """뼈대 어댑터는 부르면 언제나 실패한다. 조립할 때 거절하지 않으면 유료 생성이 끝난 뒤에야 실패한다."""
    with pytest.raises(ConfigurationError, match="뼈대") as raised:
        default_registry().build(port, key, context)

    assert key in str(raised.value)


@pytest.mark.parametrize(
    ("settings", "variable"),
    [
        (Settings(tracker="opencv"), "FRAME_TRACKER"),
        (Settings(generator="diffusers"), "FRAME_GENERATOR"),
    ],
    ids=["tracker", "generator"],
)
def test_choosing_a_skeleton_adapter_stops_the_server_from_starting(settings, variable):
    """다른 어댑터와 같은 원칙이다: 쓸 수 없는 구성은 요청을 받기 전에, 기동할 때 실패한다."""
    with pytest.raises(ConfigurationError, match=variable):
        build_container(settings)


def test_an_unknown_key_names_the_port_and_the_available_keys(context):
    with pytest.raises(ConfigurationError) as raised:
        default_registry().build(VideoGenerator, "sora", context)

    message = str(raised.value)
    assert "VideoGenerator" in message and "'sora'" in message
    assert all(key in message for key in DEFAULT_KEYS[VideoGenerator])


def test_a_registered_factory_replaces_the_one_under_the_same_key(context):
    registry = AdapterRegistry()
    registry.register(ArtifactStore, "memory", lambda ctx: "first")
    registry.register(ArtifactStore, "memory", lambda ctx: "second")

    assert registry.keys(ArtifactStore) == ["memory"]
    assert registry.build(ArtifactStore, "memory", context) == "second"


@pytest.mark.parametrize(
    ("port", "key", "missing"),
    [
        (VideoGenerator, "fake", "artifact_store"),
        (VideoAssembler, "fake", "artifact_store"),
        (IdentityReferenceExtractor, "fake", "artifact_store"),
        (Checker, "ssim_preservation", "artifact_store"),
        (JobDispatcher, "inline", "handlers"),
    ],
    ids=lambda value: getattr(value, "__name__", value),
)
def test_a_factory_says_which_prerequisite_is_not_built_yet(port, key, missing, context):
    setattr(context, missing, None)

    with pytest.raises(ConfigurationError, match=missing):
        default_registry().build(port, key, context)


def test_adapters_that_read_text_or_images_find_the_llm_in_the_same_registry(context):
    """구조화·변경 요청 해석은 FRAME_LLM_MODEL, 이미지를 읽는 어댑터는 FRAME_VLM_MODEL 로 LLM 을 얻는다."""
    registry = default_registry()
    asked: list[str] = []

    def recording_llm(ctx: BuildContext) -> LLMClient:
        asked.append(ctx.settings.llm_model)
        return ReplayLLMClient()

    registry.register(LLMClient, "fake", recording_llm)

    registry.build(RequirementStructurer, "llm", context)
    registry.build(ChangeRequestInterpreter, "llm", context)
    assert asked == ["text-model", "text-model"]

    asked.clear()
    registry.build(IdentityReferenceExtractor, "vlm_crop", context)
    for key in ("vlm_consistency", "vlm_requirement", "vlm_preservation"):
        registry.build(Checker, key, context)
    assert asked == ["vision-model"] * 4


def test_the_vlm_model_falls_back_to_the_llm_model(context):
    registry = default_registry()
    asked: list[str] = []

    def recording_llm(ctx: BuildContext) -> LLMClient:
        asked.append(ctx.settings.llm_model)
        return ReplayLLMClient()

    registry.register(LLMClient, "fake", recording_llm)
    context.settings = Settings(
        llm_provider="fake", llm_model="text-model", video_ffmpeg=context.settings.video_ffmpeg
    )

    registry.build(Checker, "vlm_consistency", context)

    assert asked == ["text-model"]
