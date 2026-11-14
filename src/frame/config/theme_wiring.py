"""테마·시리즈 기능의 조립 (ADR-0025~0027). 어댑터 고르기와 유스케이스 묶음을 한곳에 둔다.

새 경계의 설정(theme_settings)·등록을 이 기능의 모듈에 모아 Settings·registry·container 는 거의 건드리지 않는다
(컨테이너는 build_theme_services 를 한 번 부를 뿐이다). 기본값은 모두 Fake·오프라인이라
키·ffmpeg·글꼴이 없어도 서버가 뜬다. 실제 어댑터를 고르면 필요한 것이 없을 때 기동 시점에 실패한다.

설정(FRAME_*)의 목록과 기본값은 theme_settings 에 있다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from frame.application.errors import ConfigurationError
from frame.application.ports.common import Clock, IdGenerator
from frame.application.ports.generator import VideoAssembler
from frame.application.ports.llm import LLMClient
from frame.application.ports.series.audio import AudioEditor
from frame.application.ports.series.overlay import OverlayRenderer, ScreenTextReader
from frame.application.ports.series.planning import SeriesPlanner
from frame.application.ports.series.repositories import EpisodeRepository, ThemeRepository
from frame.application.ports.series.source_analysis import MediaProber, ShotDetector
from frame.application.ports.series.theme_extraction import ThemeExtractor
from frame.application.ports.speech import SpeechRecognizer
from frame.application.ports.unit_of_work import UnitOfWorkFactory
from frame.application.use_cases import AcceptGeneration, CreateRevision
from frame.application.use_cases.script import ExtractScript
from frame.application.use_cases.series import (
    AdoptAnchors,
    AttachNarration,
    CheckEpisodeScreen,
    CorrectEpisodeScreen,
    CreateEpisode,
    ExtractTheme,
    RenderEpisode,
    ReviseEpisode,
    SettleEpisode,
)
from frame.domain.series.shortform import ShortformPlatform

from .registry import AdapterRegistry, BuildContext
from .settings import Settings
from .theme_settings import ThemeSettings

log = logging.getLogger(__name__)

OPENAI_BASE_URL = "https://api.openai.com/v1"


@dataclass
class ThemeServices:
    """테마·시리즈 유스케이스와 그 어댑터 묶음. Container.series 로 나간다."""

    settings: ThemeSettings
    themes: ThemeRepository
    episodes: EpisodeRepository
    prober: MediaProber
    shot_detector: ShotDetector
    recognizer: SpeechRecognizer | None
    extractor: ThemeExtractor
    planner: SeriesPlanner
    audio: AudioEditor
    renderer: OverlayRenderer
    text_reader: ScreenTextReader | None
    extract_theme: ExtractTheme
    create_episode: CreateEpisode
    adopt_anchors: AdoptAnchors
    attach_narration: AttachNarration
    render_episode: RenderEpisode
    check_screen: CheckEpisodeScreen
    correct_screen: CorrectEpisodeScreen
    extract_script: ExtractScript
    revise_episode: ReviseEpisode
    settle_episode: SettleEpisode

    def adapter_keys(self) -> dict[str, str]:
        """어떤 어댑터로 조립됐는지 (/health·기록용)."""
        s = self.settings
        return {
            "theme_media": s.media,
            "speech_recognizer": s.speech,
            "theme_extractor": s.extractor,
            "series_planner": s.planner,
            "audio_editor": s.audio,
            "overlay_renderer": s.overlay,
            "screen_text_reader": s.text_reader,
            "theme_store": s.store,
        }


Factory = Callable[[], Any]


def build_theme_services(
    *,
    settings: Settings,
    registry: AdapterRegistry,
    ctx: BuildContext,
    uow_factory: UnitOfWorkFactory,
    assembler: VideoAssembler,
    create_revision: CreateRevision,
    accept_generation: AcceptGeneration,
    overrides: Mapping[str, Any] | None = None,
) -> ThemeServices:
    """컨테이너가 만든 것(저장소·조립기·유스케이스) 위에 테마·시리즈 기능을 조립한다.

    overrides 의 키: theme_settings, theme_repository, episode_repository, media_prober, shot_detector,
    speech_recognizer, theme_extractor, series_planner, audio_editor, overlay_renderer, screen_text_reader.
    """
    ov = dict(overrides or {})
    # 환경 변수는 서버 입구에서 한 번만 읽어 넘긴다 (presentation/main.py). 여기서 읽으면 설정을 직접 넘긴
    # 테스트·도구의 컨테이너에도 셸의 FRAME_THEME_* 가 끼어든다 (감사 BTS-13)
    ts: ThemeSettings = ov.get("theme_settings") or ThemeSettings()
    store = _require(ctx.artifact_store, "artifact_store")
    ids: IdGenerator = ctx.ids
    clock: Clock = ctx.clock

    def pick(name: str, choices: Mapping[str, Factory], key: str, label: str) -> Any:
        if name in ov:
            return ov[name]
        if key not in choices:
            raise ConfigurationError(
                f"{label} 에 '{key}' 어댑터가 없습니다. 사용 가능: {sorted(choices)}"
            )
        return choices[key]()

    def ffmpeg() -> str:
        from frame.adapters.generator.ffmpeg import resolve_ffmpeg

        return resolve_ffmpeg(settings.video_ffmpeg)

    def llm() -> LLMClient:
        return registry.build(LLMClient, settings.llm_provider, ctx)

    def vlm() -> LLMClient:
        """이미지를 읽는 LLM. 공급자·키·주소는 구조화와 같고 모델만 FRAME_VLM_MODEL 로 바꾼다."""
        model = settings.vlm_model or settings.llm_model
        return registry.build(
            LLMClient,
            settings.llm_provider,
            replace(ctx, settings=replace(settings, llm_model=model)),
        )

    # ---- 저장 ----
    # 문서 저장소는 키로 주소를 알 수 있는 산출물 저장소가 필요하다. 그렇지 않으면 메모리로 물러난다
    # (생성 캐시와 같은 방식). 이 기능을 쓰지 않는 서버의 기동을 막지 않는다 (감사 BTS-13)
    documents_ok = store.uri_for("themes/index.json") is not None
    if not documents_ok and ts.store == "artifact":
        log.warning(
            "산출물 저장소가 키로 주소를 알려 주지 않아 테마·편을 메모리에 둡니다 (다시 시작하면 사라집니다)"
        )

    def _artifact_themes() -> ThemeRepository:
        from frame.adapters.series.store import ArtifactThemeRepository

        return ArtifactThemeRepository(store) if documents_ok else _memory_themes()

    def _artifact_episodes() -> EpisodeRepository:
        from frame.adapters.series.store import ArtifactEpisodeRepository

        return ArtifactEpisodeRepository(store) if documents_ok else _memory_episodes()

    def _memory_themes() -> ThemeRepository:
        from frame.adapters.series.store import InMemoryThemeRepository

        return InMemoryThemeRepository()

    def _memory_episodes() -> EpisodeRepository:
        from frame.adapters.series.store import InMemoryEpisodeRepository

        return InMemoryEpisodeRepository()

    themes: ThemeRepository = pick(
        "theme_repository",
        {"artifact": _artifact_themes, "memory": _memory_themes},
        ts.store,
        "FRAME_THEME_STORE",
    )
    episodes: EpisodeRepository = pick(
        "episode_repository",
        {"artifact": _artifact_episodes, "memory": _memory_episodes},
        ts.store,
        "FRAME_THEME_STORE",
    )

    # ---- 영상 분석 ----
    def _fake_prober() -> MediaProber:
        from frame.adapters.series.source_analysis import FakeMediaProber

        return FakeMediaProber()

    def _ffmpeg_prober() -> MediaProber:
        from frame.adapters.series.source_analysis.ffmpeg import FfmpegMediaProber

        return FfmpegMediaProber(ffmpeg())

    def _fake_shots() -> ShotDetector:
        from frame.adapters.series.source_analysis import FakeShotDetector

        return FakeShotDetector()

    def _ffmpeg_shots() -> ShotDetector:
        from frame.adapters.series.source_analysis.ffmpeg import FfmpegShotDetector

        return FfmpegShotDetector(ffmpeg(), threshold=ts.shot_threshold)

    prober: MediaProber = pick(
        "media_prober",
        {"fake": _fake_prober, "ffmpeg": _ffmpeg_prober},
        ts.media,
        "FRAME_THEME_MEDIA",
    )
    shot_detector: ShotDetector = pick(
        "shot_detector",
        {"fake": _fake_shots, "ffmpeg": _ffmpeg_shots},
        ts.media,
        "FRAME_THEME_MEDIA",
    )

    def _fake_recognizer() -> SpeechRecognizer:
        from frame.adapters.speech import FakeSpeechRecognizer

        return FakeSpeechRecognizer()

    def _openai_recognizer() -> SpeechRecognizer:
        from frame.adapters.llm.key_store import LLMKeyStore
        from frame.adapters.speech.caching import CachingSpeechRecognizer
        from frame.adapters.speech.openai import OpenAISpeechRecognizer
        from frame.application.ports.llm import LLMKeySource

        llm_base = _plain_url(settings.llm_base_url or OPENAI_BASE_URL)
        base_url = _speech_base_url(ts.speech_base_url or llm_base)
        key_source: LLMKeySource
        if base_url == llm_base and ts.speech_key_file is None:
            # LLM 과 같은 주소: LLM 이 쓰는 키를 함께 쓴다 (화면에서 넣은 키 포함)
            key_source = ctx.llm_keys or LLMKeyStore(key_file=settings.llm_key_file)
        else:
            # 다른 주소이거나 전용 키를 줬다: 그 키 파일의 키만 싣는다. 환경 변수(OPENAI_API_KEY)나 LLM 키로
            # 대신하지 않는다. LLMKeyStore 는 파일보다 환경 변수를 먼저 보므로 환경 변수 대체를 끈다 (감사 BTS-11)
            if ts.speech_key_file is None:
                raise ConfigurationError(
                    f"FRAME_SPEECH_KEY_FILE 이 필요합니다: 인식 주소({base_url})가 LLM 주소({llm_base})와 "
                    "다릅니다. LLM 키를 다른 주소로 보내지 않도록 인식 전용 키 파일을 주세요"
                )
            if not Path(ts.speech_key_file).is_file():
                raise ConfigurationError("FRAME_SPEECH_KEY_FILE: 키 파일이 없습니다")
            key_source = LLMKeyStore(key_file=ts.speech_key_file, env={})
        recognizer = OpenAISpeechRecognizer(
            key_source=key_source,
            model=ts.speech_model,
            base_url=base_url,
            timeout_s=ts.speech_timeout_s,
        )
        # 같은 소리는 한 번만 과금한다: 결과를 산출물 저장소에 남긴다 (감사 BTS-12)
        return CachingSpeechRecognizer(recognizer, store, f"{ts.speech_model}@{base_url}")

    recognizer: SpeechRecognizer | None = pick(
        "speech_recognizer",
        {"fake": _fake_recognizer, "openai": _openai_recognizer, "off": lambda: None},
        ts.speech,
        "FRAME_SPEECH_RECOGNIZER",
    )

    def _fake_extractor() -> ThemeExtractor:
        from frame.adapters.series.theme_extraction import FakeThemeExtractor

        return FakeThemeExtractor()

    def _vlm_extractor() -> ThemeExtractor:
        from frame.adapters.series.theme_extraction.vlm import VlmThemeExtractor

        return VlmThemeExtractor(
            ffmpeg(),
            vlm(),
            frames_per_slot=ts.extractor_frames,
            max_attempts=ts.extractor_attempts,
        )

    extractor: ThemeExtractor = pick(
        "theme_extractor",
        {"fake": _fake_extractor, "vlm": _vlm_extractor},
        ts.extractor,
        "FRAME_THEME_EXTRACTOR",
    )

    # ---- 시리즈 기획 ----
    def _template_planner() -> SeriesPlanner:
        from frame.adapters.series.planning import TemplateSeriesPlanner

        return TemplateSeriesPlanner()

    def _llm_planner() -> SeriesPlanner:
        from frame.adapters.series.planning.llm import LLMSeriesPlanner

        return LLMSeriesPlanner(
            llm(), max_attempts=settings.llm_max_attempts, timeout_s=settings.llm_timeout_s
        )

    planner: SeriesPlanner = pick(
        "series_planner",
        {"template": _template_planner, "llm": _llm_planner},
        ts.planner,
        "FRAME_SERIES_PLANNER",
    )

    # ---- 소리·화면 문구 ----
    def _fake_audio() -> AudioEditor:
        from frame.adapters.series.audio import FakeAudioEditor

        return FakeAudioEditor()

    def _ffmpeg_audio() -> AudioEditor:
        from frame.adapters.series.audio.ffmpeg import FfmpegAudioEditor

        return FfmpegAudioEditor(ffmpeg())

    audio: AudioEditor = pick(
        "audio_editor",
        {"fake": _fake_audio, "ffmpeg": _ffmpeg_audio},
        ts.audio,
        "FRAME_AUDIO_EDITOR",
    )

    def _fake_renderer() -> OverlayRenderer:
        from frame.adapters.series.overlay import FakeOverlayRenderer

        return FakeOverlayRenderer()

    def _ffmpeg_renderer() -> OverlayRenderer:
        from frame.adapters.series.overlay.ffmpeg_renderer import (
            FfmpegOverlayRenderer,
            resolve_font,
        )

        return FfmpegOverlayRenderer(ffmpeg(), resolve_font(ts.overlay_font))

    renderer: OverlayRenderer = pick(
        "overlay_renderer",
        {"fake": _fake_renderer, "ffmpeg": _ffmpeg_renderer},
        ts.overlay,
        "FRAME_OVERLAY_RENDERER",
    )

    def _fake_reader() -> ScreenTextReader:
        from frame.adapters.series.overlay import FakeScreenTextReader

        return FakeScreenTextReader()

    def _vlm_reader() -> ScreenTextReader:
        from frame.adapters.series.overlay.caching_reader import CachingScreenTextReader
        from frame.adapters.series.overlay.vlm_reader import VlmScreenTextReader

        client = vlm()
        profile = f"vlm:{getattr(client, 'model', settings.vlm_model)}"
        # 같은 화면은 한 번만 읽는다 (감사 BTS-12)
        return CachingScreenTextReader(VlmScreenTextReader(ffmpeg(), client), store, profile)

    text_reader: ScreenTextReader | None = pick(
        "screen_text_reader",
        {"fake": _fake_reader, "vlm": _vlm_reader, "off": lambda: None},
        ts.text_reader,
        "FRAME_SCREEN_TEXT_READER",
    )

    try:
        platforms = tuple(ShortformPlatform(p) for p in ts.platforms)
    except ValueError as exc:
        raise ConfigurationError(
            f"FRAME_SHORTFORM_PLATFORMS 에 알 수 없는 플랫폼이 있습니다: {ts.platforms} "
            f"(사용 가능: {', '.join(ShortformPlatform)})"
        ) from exc

    check_screen = CheckEpisodeScreen(themes, episodes, uow_factory, store, text_reader)
    return ThemeServices(
        settings=ts,
        themes=themes,
        episodes=episodes,
        prober=prober,
        shot_detector=shot_detector,
        recognizer=recognizer,
        extractor=extractor,
        planner=planner,
        audio=audio,
        renderer=renderer,
        text_reader=text_reader,
        extract_theme=ExtractTheme(
            themes,
            store,
            prober,
            shot_detector,
            audio,
            recognizer,
            extractor,
            ids,
            clock,
            platforms=platforms,
            min_slot_ms=ts.min_slot_ms,
        ),
        create_episode=CreateEpisode(themes, episodes, planner, uow_factory, ids, clock),
        adopt_anchors=AdoptAnchors(themes, episodes, uow_factory),
        attach_narration=AttachNarration(episodes, store, audio, recognizer),
        render_episode=RenderEpisode(episodes, uow_factory, store, renderer, assembler, audio),
        check_screen=check_screen,
        extract_script=ExtractScript(audio, recognizer),
        correct_screen=CorrectEpisodeScreen(
            episodes, check_screen, uow_factory, create_revision, accept_generation
        ),
        revise_episode=ReviseEpisode(
            themes, episodes, uow_factory, create_revision, accept_generation
        ),
        settle_episode=SettleEpisode(episodes, uow_factory),
    )


def _plain_url(url: str) -> str:
    return url.strip().rstrip("/")


def _speech_base_url(url: str) -> str:
    """인식 API 주소는 https 만 받는다 (로컬 시험 서버는 예외). 키를 평문으로 보내지 않기 위해서다.

    판정은 OpenRouter 어댑터의 것을 그대로 쓴다. 감사 세션이 그 함수를 adapters/shared 로 옮길 때 이 import 도 옮긴다
    (복사하지 않는다, 감사 BTS-11).
    """
    from frame.adapters.generator.openrouter import _checked_base_url

    try:
        return _checked_base_url(url)
    except ConfigurationError:
        raise ConfigurationError(
            f"FRAME_SPEECH_BASE_URL 은 https 여야 합니다 (키를 평문으로 보내지 않도록): {url}"
        ) from None


def _require(value: Any, name: str) -> Any:
    if value is None:
        raise ConfigurationError(f"{name} 가 아직 만들어지지 않았습니다")
    return value
