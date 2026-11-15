"""테마·시리즈 기능의 조립 (ADR-0025~0029). 고른 어댑터로 유스케이스를 묶어 Container.series 로 내보낸다.

build_theme_services 가 입구다 (컨테이너가 한 번 부른다). 다른 포트와 같은 방식으로 조립한다:
설정(theme_settings)의 키로 등록부에서 어댑터를 만들고, 바꿔 끼운 것(overrides)이 있으면 그것을 쓴다.
키가 어느 어댑터가 되는지는 등록 모듈에 있다 — config/registrations/series.py (저장 · 영상 분석 · 추출 · 기획 ·
소리 · 화면 문구), config/registrations/speech.py (말소리). 여기서는 고른 것으로 유스케이스를 묶는다.

기본값은 모두 Fake·오프라인이라 키·ffmpeg·글꼴이 없어도 서버가 뜬다. 실제 어댑터를 고르면 필요한 것이 없을 때
기동 시점에 실패한다 (인식 API 의 키는 예외다: 화면에서 넣는 키가 있어 기동 때 알 수 없다).
설정(FRAME_*)의 목록과 기본값은 theme_settings 에 있다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from frame.application.errors import ConfigurationError
from frame.application.ports.artifact_store import ArtifactStore
from frame.application.ports.assembler import VideoAssembler
from frame.application.ports.common import Clock, IdGenerator
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
    CorrectSourceScript,
    CreateEpisode,
    ExtractTheme,
    RenderEpisode,
    ReviseEpisode,
    ReviseEpisodeScript,
    SettleEpisode,
)
from frame.domain.series.shortform import ShortformPlatform

from .registry import AdapterRegistry, BuildContext
from .theme_settings import ThemeSettings


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
    correct_source_script: CorrectSourceScript
    revise_episode_script: ReviseEpisodeScript
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


def build_theme_services(
    *,
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
    어댑터의 팩토리는 ctx 의 설정(ctx.settings)과 여기서 넣는 테마 설정(ctx.theme_settings)을 읽는다.
    """
    ov = dict(overrides or {})
    # 환경 변수는 서버 입구에서 한 번만 읽어 넘긴다 (presentation/main.py). 여기서 읽으면 설정을 직접 넘긴
    # 테스트·도구의 컨테이너에도 셸의 FRAME_THEME_* 가 끼어든다 (감사 BTS-13)
    ts: ThemeSettings = ov.get("theme_settings") or ThemeSettings()
    ts.validate()  # 코드에서 직접 만든 설정도 범위를 본다 (기동할 때 드러나게)
    store: ArtifactStore = _require(ctx.artifact_store, "artifact_store")
    ctx.theme_settings = ts  # 이 기능의 팩토리가 읽는다
    ids: IdGenerator = ctx.ids
    clock: Clock = ctx.clock

    def pick(name: str, port: type, key: str, setting: str) -> Any:
        """바꿔 끼운 어댑터가 있으면 그것을, 없으면 설정(setting)의 키로 등록부에서 만든다."""
        if name in ov:
            return ov[name]
        return registry.build(port, key, ctx, setting=setting)

    themes: ThemeRepository = pick(
        "theme_repository", ThemeRepository, ts.store, "FRAME_THEME_STORE"
    )
    episodes: EpisodeRepository = pick(
        "episode_repository", EpisodeRepository, ts.store, "FRAME_THEME_STORE"
    )
    prober: MediaProber = pick("media_prober", MediaProber, ts.media, "FRAME_THEME_MEDIA")
    shot_detector: ShotDetector = pick("shot_detector", ShotDetector, ts.media, "FRAME_THEME_MEDIA")
    recognizer: SpeechRecognizer | None = pick(
        "speech_recognizer", SpeechRecognizer, ts.speech, "FRAME_SPEECH_RECOGNIZER"
    )
    extractor: ThemeExtractor = pick(
        "theme_extractor", ThemeExtractor, ts.extractor, "FRAME_THEME_EXTRACTOR"
    )
    planner: SeriesPlanner = pick(
        "series_planner", SeriesPlanner, ts.planner, "FRAME_SERIES_PLANNER"
    )
    audio: AudioEditor = pick("audio_editor", AudioEditor, ts.audio, "FRAME_AUDIO_EDITOR")
    renderer: OverlayRenderer = pick(
        "overlay_renderer", OverlayRenderer, ts.overlay, "FRAME_OVERLAY_RENDERER"
    )
    text_reader: ScreenTextReader | None = pick(
        "screen_text_reader", ScreenTextReader, ts.text_reader, "FRAME_SCREEN_TEXT_READER"
    )
    platforms = _shortform_platforms(ts)

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
            max_slot_ms=ts.max_slot_ms,
        ),
        create_episode=CreateEpisode(themes, episodes, planner, uow_factory, ids, clock),
        adopt_anchors=AdoptAnchors(themes, episodes, uow_factory),
        attach_narration=AttachNarration(episodes, store, audio, recognizer),
        render_episode=RenderEpisode(episodes, uow_factory, store, renderer, assembler, audio),
        check_screen=check_screen,
        extract_script=ExtractScript(audio, recognizer),
        correct_source_script=CorrectSourceScript(themes, episodes),
        revise_episode_script=ReviseEpisodeScript(themes, episodes, uow_factory, store, audio),
        correct_screen=CorrectEpisodeScreen(
            episodes, check_screen, uow_factory, create_revision, accept_generation
        ),
        revise_episode=ReviseEpisode(
            themes, episodes, uow_factory, create_revision, accept_generation
        ),
        settle_episode=SettleEpisode(episodes, uow_factory),
    )


def _shortform_platforms(ts: ThemeSettings) -> tuple[ShortformPlatform, ...]:
    """'기준 내 숏폼' 을 판정할 플랫폼."""
    try:
        return tuple(ShortformPlatform(p) for p in ts.platforms)
    except ValueError as exc:
        raise ConfigurationError(
            f"FRAME_SHORTFORM_PLATFORMS 에 알 수 없는 플랫폼이 있습니다: {ts.platforms} "
            f"(사용 가능: {', '.join(ShortformPlatform)})"
        ) from exc


def _require(value: Any, name: str) -> Any:
    if value is None:
        raise ConfigurationError(f"{name} 가 아직 만들어지지 않았습니다")
    return value
