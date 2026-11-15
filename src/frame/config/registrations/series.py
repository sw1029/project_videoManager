"""테마·시리즈 기능의 포트 등록 (ADR-0025~0027) — 저장 · 영상 분석 · 테마 추출 · 편 기획 · 소리 편집 · 화면 문구.

포트는 application/ports/series/ 의 것이다. 어느 키를 쓸지는 테마·시리즈 설정(config/theme_settings.py)이 정하고,
조립(config/theme_wiring.py)이 그 키로 여기 올린 팩토리를 부른다.
- 패키지 입구가 내보내는 테스트·오프라인용 구현은 바로 import 한다. 실제 구현(ffmpeg · LLM)은 팩토리가 불릴 때
  import 한다 — 고르지 않은 어댑터의 모듈은 읽지 않는다.
- 필요한 것(ffmpeg · 글꼴)이 없으면 팩토리가 ConfigurationError 를 낸다. 고른 서버는 기동 시점에 실패한다.
말소리 받아 적기(SpeechRecognizer)는 시리즈 기능 밖에서도 쓰는 포트라 speech 모듈이 맡는다.
"""

from __future__ import annotations

import logging

from frame.adapters.series.audio import FakeAudioEditor
from frame.adapters.series.overlay import FakeOverlayRenderer, FakeScreenTextReader
from frame.adapters.series.planning import TemplateSeriesPlanner
from frame.adapters.series.source_analysis import FakeMediaProber, FakeShotDetector
from frame.adapters.series.store import (
    ArtifactEpisodeRepository,
    ArtifactThemeRepository,
    InMemoryEpisodeRepository,
    InMemoryThemeRepository,
)
from frame.adapters.series.theme_extraction import FakeThemeExtractor
from frame.application.ports import ArtifactStore
from frame.application.ports.series.audio import AudioEditor
from frame.application.ports.series.overlay import OverlayRenderer, ScreenTextReader
from frame.application.ports.series.planning import SeriesPlanner
from frame.application.ports.series.repositories import EpisodeRepository, ThemeRepository
from frame.application.ports.series.source_analysis import MediaProber, ShotDetector
from frame.application.ports.series.theme_extraction import ThemeExtractor

from ..registry import AdapterRegistry, BuildContext
from ._dependencies import (
    artifact_store,
    ffmpeg_path,
    llm_address,
    llm_client,
    theme_settings,
    vlm_client,
)

log = logging.getLogger(__name__)


def register(registry: AdapterRegistry) -> None:
    # 테마·편 문서를 두는 곳 (FRAME_THEME_STORE)
    registry.register(ThemeRepository, "artifact", _artifact_themes)
    registry.register(ThemeRepository, "memory", lambda ctx: InMemoryThemeRepository())
    registry.register(EpisodeRepository, "artifact", _artifact_episodes)
    registry.register(EpisodeRepository, "memory", lambda ctx: InMemoryEpisodeRepository())
    # 영상 분석: 겉모습 읽기와 컷 찾기 (FRAME_THEME_MEDIA)
    registry.register(MediaProber, "fake", lambda ctx: FakeMediaProber())
    registry.register(MediaProber, "ffmpeg", _ffmpeg_prober)
    registry.register(ShotDetector, "fake", lambda ctx: FakeShotDetector())
    registry.register(ShotDetector, "ffmpeg", _ffmpeg_shot_detector)
    # 테마 추출 (FRAME_THEME_EXTRACTOR)
    registry.register(ThemeExtractor, "fake", lambda ctx: FakeThemeExtractor())
    registry.register(ThemeExtractor, "vlm", lambda ctx: _vlm_extractor(registry, ctx))
    # 편의 내용 기획 (FRAME_SERIES_PLANNER)
    registry.register(SeriesPlanner, "template", lambda ctx: TemplateSeriesPlanner())
    registry.register(SeriesPlanner, "llm", lambda ctx: _llm_planner(registry, ctx))
    # 소리 편집 (FRAME_AUDIO_EDITOR)
    registry.register(AudioEditor, "fake", lambda ctx: FakeAudioEditor())
    registry.register(AudioEditor, "ffmpeg", _ffmpeg_audio)
    # 화면 문구 얹기 (FRAME_OVERLAY_RENDERER)와, 얹은 문구를 화면에서 다시 읽기 (FRAME_SCREEN_TEXT_READER)
    registry.register(OverlayRenderer, "fake", lambda ctx: FakeOverlayRenderer())
    registry.register(OverlayRenderer, "ffmpeg", _ffmpeg_renderer)
    registry.register(ScreenTextReader, "fake", lambda ctx: FakeScreenTextReader())
    registry.register(ScreenTextReader, "vlm", lambda ctx: _vlm_reader(registry, ctx))
    registry.register(ScreenTextReader, "off", lambda ctx: None)


# ---- 저장 ------------------------------------------------------------------------
def _document_store(ctx: BuildContext, kept: str) -> ArtifactStore | None:
    """문서를 둘 산출물 저장소. 키로 주소를 알려 주지 않는 저장소면 None 이고, 부른 쪽이 메모리로 물러난다.

    문서 저장소는 키로 주소를 알 수 있는 산출물 저장소가 필요하다 (생성 캐시와 같은 방식).
    물러나는 까닭은 이 기능을 쓰지 않는 서버의 기동을 막지 않으려는 것이다 (감사 BTS-13).
    """
    store = artifact_store(ctx)
    if store.uri_for("themes/index.json") is not None:
        return store
    log.warning(
        "산출물 저장소가 키로 주소를 알려 주지 않아 %s 메모리에 둡니다 (다시 시작하면 사라집니다)",
        kept,
    )
    return None


def _artifact_themes(ctx: BuildContext) -> ThemeRepository:
    store = _document_store(ctx, "테마를")
    return ArtifactThemeRepository(store) if store is not None else InMemoryThemeRepository()


def _artifact_episodes(ctx: BuildContext) -> EpisodeRepository:
    store = _document_store(ctx, "편을")
    return ArtifactEpisodeRepository(store) if store is not None else InMemoryEpisodeRepository()


# ---- 영상 분석 --------------------------------------------------------------------
def _ffmpeg_prober(ctx: BuildContext) -> MediaProber:
    from frame.adapters.series.source_analysis.ffmpeg import FfmpegMediaProber

    return FfmpegMediaProber(ffmpeg_path(ctx))


def _ffmpeg_shot_detector(ctx: BuildContext) -> ShotDetector:
    from frame.adapters.series.source_analysis.ffmpeg import FfmpegShotDetector

    ts = theme_settings(ctx)
    return FfmpegShotDetector(ffmpeg_path(ctx), threshold=ts.shot_threshold)


# ---- 테마 추출 · 편 기획 -----------------------------------------------------------
def _vlm_extractor(registry: AdapterRegistry, ctx: BuildContext) -> ThemeExtractor:
    from frame.adapters.series.theme_extraction.vlm import VlmThemeExtractor

    ts = theme_settings(ctx)
    return VlmThemeExtractor(
        ffmpeg_path(ctx),
        vlm_client(registry, ctx),
        frames_per_slot=ts.extractor_frames,
        max_attempts=ts.extractor_attempts,
    )


def _llm_planner(registry: AdapterRegistry, ctx: BuildContext) -> SeriesPlanner:
    from frame.adapters.series.planning.llm import LLMSeriesPlanner

    s = ctx.settings
    return LLMSeriesPlanner(
        llm_client(registry, ctx), max_attempts=s.llm_max_attempts, timeout_s=s.llm_timeout_s
    )


# ---- 소리 · 화면 문구 --------------------------------------------------------------
def _ffmpeg_audio(ctx: BuildContext) -> AudioEditor:
    from frame.adapters.series.audio.ffmpeg import FfmpegAudioEditor

    return FfmpegAudioEditor(ffmpeg_path(ctx))


def _ffmpeg_renderer(ctx: BuildContext) -> OverlayRenderer:
    from frame.adapters.series.overlay.ffmpeg_renderer import FfmpegOverlayRenderer, resolve_font

    ts = theme_settings(ctx)
    return FfmpegOverlayRenderer(ffmpeg_path(ctx), resolve_font(ts.overlay_font))


def _vlm_reader(registry: AdapterRegistry, ctx: BuildContext) -> ScreenTextReader:
    from frame.adapters.series.overlay.caching_reader import CachingScreenTextReader
    from frame.adapters.series.overlay.vlm_reader import VlmScreenTextReader

    s = ctx.settings
    client = vlm_client(registry, ctx)
    reader = VlmScreenTextReader(ffmpeg_path(ctx), client)
    # 같은 화면은 한 번만 읽는다 (감사 BTS-12). 키에는 답을 바꿀 수 있는 것이 모두 든다:
    # 모델 · 주소 · 읽는 방법(자르는 여백·크기·프롬프트 판) (감사 BTS-D10)
    model = getattr(client, "model", None) or s.vlm_model or s.llm_model
    return CachingScreenTextReader(
        reader, artifact_store(ctx), f"vlm:{model}@{llm_address(ctx)}:{reader.signature}"
    )
