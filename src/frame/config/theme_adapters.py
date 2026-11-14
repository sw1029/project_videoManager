"""테마·시리즈 기능의 어댑터 고르기 (ADR-0025~0028). 조립(theme_wiring.build_theme_services)이 부른다.

개념마다 함수 하나가 설정(theme_settings)을 보고 어댑터를 고른다:
저장(pick_repositories) · 영상 분석(pick_source_analysis) · 말소리(pick_recognizer) · 추출(pick_extractor) ·
기획(pick_planner) · 소리(pick_audio) · 화면 문구(pick_overlay) · 숏폼 플랫폼(shortform_platforms).
- 바꿔 끼운 어댑터(overrides)가 있으면 그것을 쓴다 (테스트·도구).
- 실제 구현은 고를 때 import 한다. 고르지 않은 어댑터의 의존성(ffmpeg·키·선택 설치)이 없어도 서버가 뜬다.
- 설정에 없는 이름이면 ConfigurationError 로 쓸 수 있는 이름을 알린다. 필요한 것이 없으면 기동 시점에 실패한다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from frame.application.errors import ConfigurationError
from frame.application.ports.artifact_store import ArtifactStore
from frame.application.ports.llm import LLMClient
from frame.application.ports.series.audio import AudioEditor
from frame.application.ports.series.overlay import OverlayRenderer, ScreenTextReader
from frame.application.ports.series.planning import SeriesPlanner
from frame.application.ports.series.repositories import EpisodeRepository, ThemeRepository
from frame.application.ports.series.source_analysis import MediaProber, ShotDetector
from frame.application.ports.series.theme_extraction import ThemeExtractor
from frame.application.ports.speech import SpeechRecognizer
from frame.domain.series.shortform import ShortformPlatform

from .registry import AdapterRegistry, BuildContext
from .settings import Settings
from .theme_settings import ThemeSettings

log = logging.getLogger(__name__)

OPENAI_BASE_URL = "https://api.openai.com/v1"

Factory = Callable[[], Any]


@dataclass(frozen=True)
class AdapterPicker:
    """조립하는 동안 함께 쓰는 것: 설정, 등록부, 컨테이너가 만든 것, 바꿔 끼울 어댑터(overrides)."""

    settings: Settings
    registry: AdapterRegistry
    ctx: BuildContext
    ts: ThemeSettings
    store: ArtifactStore
    overrides: Mapping[str, Any]

    def pick(self, name: str, choices: Mapping[str, Factory], key: str, label: str) -> Any:
        """바꿔 끼운 어댑터가 있으면 그것을, 없으면 설정(key)이 고른 것을 만든다."""
        if name in self.overrides:
            return self.overrides[name]
        if key not in choices:
            raise ConfigurationError(
                f"{label} 에 '{key}' 어댑터가 없습니다. 사용 가능: {sorted(choices)}"
            )
        return choices[key]()

    def ffmpeg(self) -> str:
        from frame.adapters.generator.ffmpeg import resolve_ffmpeg

        return resolve_ffmpeg(self.settings.video_ffmpeg)

    def llm(self) -> LLMClient:
        return self.registry.build(LLMClient, self.settings.llm_provider, self.ctx)

    def vlm(self) -> LLMClient:
        """이미지를 읽는 LLM. 공급자·키·주소는 구조화와 같고 모델만 FRAME_VLM_MODEL 로 바꾼다."""
        model = self.settings.vlm_model or self.settings.llm_model
        return self.registry.build(
            LLMClient,
            self.settings.llm_provider,
            replace(self.ctx, settings=replace(self.settings, llm_model=model)),
        )


# ---- 저장 ------------------------------------------------------------------------
def pick_repositories(w: AdapterPicker) -> tuple[ThemeRepository, EpisodeRepository]:
    """테마·편 문서를 두는 곳.

    문서 저장소는 키로 주소를 알 수 있는 산출물 저장소가 필요하다. 그렇지 않으면 메모리로 물러난다
    (생성 캐시와 같은 방식). 이 기능을 쓰지 않는 서버의 기동을 막지 않는다 (감사 BTS-13).
    """
    from frame.adapters.series.store import (
        ArtifactEpisodeRepository,
        ArtifactThemeRepository,
        InMemoryEpisodeRepository,
        InMemoryThemeRepository,
    )

    documents_ok = w.store.uri_for("themes/index.json") is not None
    if not documents_ok and w.ts.store == "artifact":
        log.warning(
            "산출물 저장소가 키로 주소를 알려 주지 않아 테마·편을 메모리에 둡니다 (다시 시작하면 사라집니다)"
        )

    def _artifact_themes() -> ThemeRepository:
        return ArtifactThemeRepository(w.store) if documents_ok else InMemoryThemeRepository()

    def _artifact_episodes() -> EpisodeRepository:
        return ArtifactEpisodeRepository(w.store) if documents_ok else InMemoryEpisodeRepository()

    themes: ThemeRepository = w.pick(
        "theme_repository",
        {"artifact": _artifact_themes, "memory": InMemoryThemeRepository},
        w.ts.store,
        "FRAME_THEME_STORE",
    )
    episodes: EpisodeRepository = w.pick(
        "episode_repository",
        {"artifact": _artifact_episodes, "memory": InMemoryEpisodeRepository},
        w.ts.store,
        "FRAME_THEME_STORE",
    )
    return themes, episodes


# ---- 영상 분석 --------------------------------------------------------------------
def pick_source_analysis(w: AdapterPicker) -> tuple[MediaProber, ShotDetector]:
    """겉모습 읽기와 컷 찾기."""

    def _fake_prober() -> MediaProber:
        from frame.adapters.series.source_analysis import FakeMediaProber

        return FakeMediaProber()

    def _ffmpeg_prober() -> MediaProber:
        from frame.adapters.series.source_analysis.ffmpeg import FfmpegMediaProber

        return FfmpegMediaProber(w.ffmpeg())

    def _fake_shots() -> ShotDetector:
        from frame.adapters.series.source_analysis import FakeShotDetector

        return FakeShotDetector()

    def _ffmpeg_shots() -> ShotDetector:
        from frame.adapters.series.source_analysis.ffmpeg import FfmpegShotDetector

        return FfmpegShotDetector(w.ffmpeg(), threshold=w.ts.shot_threshold)

    prober: MediaProber = w.pick(
        "media_prober",
        {"fake": _fake_prober, "ffmpeg": _ffmpeg_prober},
        w.ts.media,
        "FRAME_THEME_MEDIA",
    )
    shot_detector: ShotDetector = w.pick(
        "shot_detector",
        {"fake": _fake_shots, "ffmpeg": _ffmpeg_shots},
        w.ts.media,
        "FRAME_THEME_MEDIA",
    )
    return prober, shot_detector


# ---- 말소리 ----------------------------------------------------------------------
def pick_recognizer(w: AdapterPicker) -> SpeechRecognizer | None:
    """말소리 받아 적기: 가짜 · OpenAI 호환 API · 로컬 · 끔."""

    def _fake() -> SpeechRecognizer:
        from frame.adapters.speech import FakeSpeechRecognizer

        return FakeSpeechRecognizer()

    recognizer: SpeechRecognizer | None = w.pick(
        "speech_recognizer",
        {
            "fake": _fake,
            "openai": lambda: _openai_recognizer(w),
            "faster_whisper": lambda: _local_recognizer(w),
            "off": lambda: None,
        },
        w.ts.speech,
        "FRAME_SPEECH_RECOGNIZER",
    )
    return recognizer


def _openai_recognizer(w: AdapterPicker) -> SpeechRecognizer:
    from frame.adapters.llm.key_store import LLMKeyStore
    from frame.adapters.speech.caching import CachingSpeechRecognizer
    from frame.adapters.speech.openai import OpenAISpeechRecognizer
    from frame.application.ports.llm import LLMKeySource

    settings, ts = w.settings, w.ts
    llm_base = _plain_url(settings.llm_base_url or OPENAI_BASE_URL)
    base_url = _speech_base_url(ts.speech_base_url or llm_base)
    key_source: LLMKeySource
    if base_url == llm_base and ts.speech_key_file is None:
        # LLM 과 같은 주소: LLM 이 쓰는 키를 함께 쓴다 (화면에서 넣은 키 포함)
        key_source = w.ctx.llm_keys or LLMKeyStore(key_file=settings.llm_key_file)
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
    return CachingSpeechRecognizer(recognizer, w.store, f"{ts.speech_model}@{base_url}")


def _local_recognizer(w: AdapterPicker) -> SpeechRecognizer:
    from importlib.util import find_spec

    from frame.adapters.speech.caching import CachingSpeechRecognizer
    from frame.adapters.speech.faster_whisper import FasterWhisperRecognizer

    ts = w.ts
    if find_spec("faster_whisper") is None:
        raise ConfigurationError(
            "FRAME_SPEECH_RECOGNIZER=faster_whisper 에 필요한 faster-whisper 꾸러미가 없습니다. "
            'pip install -e ".[speech-local]" 로 설치하세요'
        )
    local = FasterWhisperRecognizer(
        model=ts.speech_local_model,
        device=ts.speech_local_device,
        compute_type=ts.speech_local_compute,
        vad=ts.speech_local_vad,
    )
    # 과금은 없지만 같은 소리를 다시 받아 적지 않는다 (시간이 든다)
    profile = f"faster-whisper:{ts.speech_local_model}:vad={ts.speech_local_vad}"
    return CachingSpeechRecognizer(local, w.store, profile)


# ---- 테마 추출 · 시리즈 기획 -------------------------------------------------------
def pick_extractor(w: AdapterPicker) -> ThemeExtractor:
    def _fake() -> ThemeExtractor:
        from frame.adapters.series.theme_extraction import FakeThemeExtractor

        return FakeThemeExtractor()

    def _vlm() -> ThemeExtractor:
        from frame.adapters.series.theme_extraction.vlm import VlmThemeExtractor

        return VlmThemeExtractor(
            w.ffmpeg(),
            w.vlm(),
            frames_per_slot=w.ts.extractor_frames,
            max_attempts=w.ts.extractor_attempts,
        )

    extractor: ThemeExtractor = w.pick(
        "theme_extractor", {"fake": _fake, "vlm": _vlm}, w.ts.extractor, "FRAME_THEME_EXTRACTOR"
    )
    return extractor


def pick_planner(w: AdapterPicker) -> SeriesPlanner:
    def _template() -> SeriesPlanner:
        from frame.adapters.series.planning import TemplateSeriesPlanner

        return TemplateSeriesPlanner()

    def _llm() -> SeriesPlanner:
        from frame.adapters.series.planning.llm import LLMSeriesPlanner

        return LLMSeriesPlanner(
            w.llm(), max_attempts=w.settings.llm_max_attempts, timeout_s=w.settings.llm_timeout_s
        )

    planner: SeriesPlanner = w.pick(
        "series_planner", {"template": _template, "llm": _llm}, w.ts.planner, "FRAME_SERIES_PLANNER"
    )
    return planner


# ---- 소리 · 화면 문구 --------------------------------------------------------------
def pick_audio(w: AdapterPicker) -> AudioEditor:
    def _fake() -> AudioEditor:
        from frame.adapters.series.audio import FakeAudioEditor

        return FakeAudioEditor()

    def _ffmpeg() -> AudioEditor:
        from frame.adapters.series.audio.ffmpeg import FfmpegAudioEditor

        return FfmpegAudioEditor(w.ffmpeg())

    audio: AudioEditor = w.pick(
        "audio_editor", {"fake": _fake, "ffmpeg": _ffmpeg}, w.ts.audio, "FRAME_AUDIO_EDITOR"
    )
    return audio


def pick_overlay(w: AdapterPicker) -> tuple[OverlayRenderer, ScreenTextReader | None]:
    """화면 문구를 얹는 것과, 얹은 문구를 화면에서 다시 읽는 것."""

    def _fake_renderer() -> OverlayRenderer:
        from frame.adapters.series.overlay import FakeOverlayRenderer

        return FakeOverlayRenderer()

    def _ffmpeg_renderer() -> OverlayRenderer:
        from frame.adapters.series.overlay.ffmpeg_renderer import (
            FfmpegOverlayRenderer,
            resolve_font,
        )

        return FfmpegOverlayRenderer(w.ffmpeg(), resolve_font(w.ts.overlay_font))

    def _fake_reader() -> ScreenTextReader:
        from frame.adapters.series.overlay import FakeScreenTextReader

        return FakeScreenTextReader()

    def _vlm_reader() -> ScreenTextReader:
        from frame.adapters.series.overlay.caching_reader import CachingScreenTextReader
        from frame.adapters.series.overlay.vlm_reader import VlmScreenTextReader

        client = w.vlm()
        profile = f"vlm:{getattr(client, 'model', w.settings.vlm_model)}"
        # 같은 화면은 한 번만 읽는다 (감사 BTS-12)
        return CachingScreenTextReader(VlmScreenTextReader(w.ffmpeg(), client), w.store, profile)

    renderer: OverlayRenderer = w.pick(
        "overlay_renderer",
        {"fake": _fake_renderer, "ffmpeg": _ffmpeg_renderer},
        w.ts.overlay,
        "FRAME_OVERLAY_RENDERER",
    )
    text_reader: ScreenTextReader | None = w.pick(
        "screen_text_reader",
        {"fake": _fake_reader, "vlm": _vlm_reader, "off": lambda: None},
        w.ts.text_reader,
        "FRAME_SCREEN_TEXT_READER",
    )
    return renderer, text_reader


def shortform_platforms(ts: ThemeSettings) -> tuple[ShortformPlatform, ...]:
    """'기준 내 숏폼' 을 판정할 플랫폼."""
    try:
        return tuple(ShortformPlatform(p) for p in ts.platforms)
    except ValueError as exc:
        raise ConfigurationError(
            f"FRAME_SHORTFORM_PLATFORMS 에 알 수 없는 플랫폼이 있습니다: {ts.platforms} "
            f"(사용 가능: {', '.join(ShortformPlatform)})"
        ) from exc


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
