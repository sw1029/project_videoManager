"""테마·시리즈 기능의 설정 (ADR-0025~0027). 환경 변수는 서버 입구에서 한 번만 읽는다 (from_env).

기본값은 모두 Fake·오프라인이라 키·ffmpeg·글꼴이 없어도 서버가 뜬다.
조립(theme_wiring.build_theme_services)은 환경 변수를 읽지 않는다. 설정을 주지 않으면 기본값으로 만든다.

| 설정 | 값 (기본값이 앞) | 뜻 |
|---|---|---|
| FRAME_THEME_MEDIA | fake · ffmpeg | 겉모습 읽기와 컷 찾기 |
| FRAME_SPEECH_RECOGNIZER | fake · off · openai | 말소리 받아 적기 (OpenAI 호환 /audio/transcriptions) |
| FRAME_SPEECH_MODEL | whisper-1 | 인식 모델 (낱말 시각을 주는 모델이어야 줄 사이를 정확히 나눈다) |
| FRAME_SPEECH_BASE_URL | (LLM 주소, 없으면 OpenAI) | 인식 API 주소. https 만 받는다 (로컬 서버는 예외) |
| FRAME_SPEECH_KEY_FILE | (주소가 같으면 LLM 의 키) | 인식 전용 키 파일. 주소가 LLM 과 다르면 꼭 주고, 그 키만 싣는다 |
| FRAME_THEME_EXTRACTOR | fake · vlm | 테마 추출 (vlm 은 FRAME_VLM_MODEL 로 프레임을 읽는다) |
| FRAME_THEME_FRAMES | 3 | vlm 추출이 장면 자리마다 보는 프레임 수 |
| FRAME_THEME_ATTEMPTS | 2 | vlm 추출의 재질의 횟수 (프레임이 붙는 요청이라 LLM 설정과 따로 둔다) |
| FRAME_SERIES_PLANNER | template · llm | 편의 내용 기획 |
| FRAME_AUDIO_EDITOR | fake · ffmpeg | 녹음 자르기·놓기·붙이기 |
| FRAME_OVERLAY_RENDERER | fake · ffmpeg | 화면 문구 얹기 |
| FRAME_OVERLAY_FONT | (없으면 한글 글꼴을 찾는다) | 화면 문구의 글꼴 파일 |
| FRAME_SCREEN_TEXT_READER | off · fake · vlm | 얹은 문구를 화면에서 읽어 확인 |
| FRAME_THEME_STORE | artifact · memory | 테마·편 문서를 두는 곳 |
| FRAME_SHORTFORM_PLATFORMS | YOUTUBE_SHORTS,INSTAGRAM_REELS | '기준 내 숏폼' 을 판정할 플랫폼 |
| FRAME_THEME_MIN_SLOT_MS | 3000 | 다시 만들 수 있는 장면 자리의 길이 하한 |
| FRAME_THEME_MAX_SLOT_MS | (없음) | 자리 길이의 상한. 주면 더 긴 자리를 말 멈춤에서 나눈다 |
| FRAME_SHOT_THRESHOLD | 0.3 | ffmpeg 컷 찾기의 scene 점수 하한 |
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import TypeVar

from frame.application.errors import ConfigurationError
from frame.domain.series.shortform import ShortformPlatform
from frame.domain.series.theme import DEFAULT_MIN_SLOT_MS

N = TypeVar("N", int, float)


@dataclass(frozen=True)
class ThemeSettings:
    media: str = "fake"
    speech: str = "fake"
    speech_model: str = "whisper-1"
    speech_base_url: str | None = (
        None  # 없으면 LLM 주소를 따른다 (FRAME_LLM_BASE_URL, 없으면 OpenAI)
    )
    speech_key_file: str | None = None
    speech_timeout_s: float = 120.0
    extractor: str = "fake"
    extractor_frames: int = 3
    # 프레임이 붙는 추출의 재질의 횟수. 글만 보내는 호출의 FRAME_LLM_MAX_ATTEMPTS 와 따로 둔다 (감사 BTS-22)
    extractor_attempts: int = 2
    planner: str = "template"
    audio: str = "fake"
    overlay: str = "fake"
    overlay_font: str | None = None
    text_reader: str = "off"
    store: str = "artifact"
    platforms: tuple[str, ...] = tuple(str(p) for p in ShortformPlatform)
    min_slot_ms: int = DEFAULT_MIN_SLOT_MS
    # 자리 길이의 상한. 주면 그보다 긴 자리를 말 멈춤에서 나눈다 (생성 모델이 만들 수 있는 길이에 맞출 때)
    max_slot_ms: int | None = None
    shot_threshold: float = 0.3

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ThemeSettings:
        src = os.environ if env is None else env
        base = cls()

        def text(name: str, default: str) -> str:
            return (src.get(f"FRAME_{name}") or "").strip() or default

        def number(name: str, default: N, cast: Callable[[str], N]) -> N:
            raw = text(name, "")
            if not raw:
                return default
            try:
                return cast(raw)
            except ValueError:
                raise ConfigurationError(f"FRAME_{name}: 숫자가 아닙니다: {raw!r}") from None

        platforms = tuple(
            p.strip().upper() for p in text("SHORTFORM_PLATFORMS", "").split(",") if p.strip()
        )
        return replace(
            base,
            media=text("THEME_MEDIA", base.media),
            speech=text("SPEECH_RECOGNIZER", base.speech),
            speech_model=text("SPEECH_MODEL", base.speech_model),
            speech_base_url=text("SPEECH_BASE_URL", "") or None,
            speech_key_file=text("SPEECH_KEY_FILE", "") or None,
            speech_timeout_s=number("SPEECH_TIMEOUT_S", base.speech_timeout_s, float),
            extractor=text("THEME_EXTRACTOR", base.extractor),
            extractor_frames=number("THEME_FRAMES", base.extractor_frames, int),
            extractor_attempts=number("THEME_ATTEMPTS", base.extractor_attempts, int),
            planner=text("SERIES_PLANNER", base.planner),
            audio=text("AUDIO_EDITOR", base.audio),
            overlay=text("OVERLAY_RENDERER", base.overlay),
            overlay_font=text("OVERLAY_FONT", "") or None,
            text_reader=text("SCREEN_TEXT_READER", base.text_reader),
            store=text("THEME_STORE", base.store),
            platforms=platforms or base.platforms,
            min_slot_ms=number("THEME_MIN_SLOT_MS", base.min_slot_ms, int),
            max_slot_ms=number("THEME_MAX_SLOT_MS", 0, int) or None,
            shot_threshold=number("SHOT_THRESHOLD", base.shot_threshold, float),
        )
