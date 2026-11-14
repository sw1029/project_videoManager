"""테마·시리즈 유스케이스 테스트의 공용 조립 (Fake 어댑터 + 메모리 저장소 + Recording 디스패처)."""

from __future__ import annotations

from typing import Any

from frame.application.ports.series.repositories import ThemeRecord
from frame.application.use_cases.series import CreateEpisodeCommand
from frame.config import Container, Settings
from frame.config.theme_settings import ThemeSettings
from frame.config.theme_wiring import ThemeServices
from frame.domain.ir import IntermediateRepresentation, SceneSpec
from frame.domain.series.episode import EpisodeBrief, EpisodeContent
from frame.domain.series.record import Episode
from frame.domain.series.source_analysis import SpeechSegment, Transcript
from frame.domain.series.theme import Theme
from tests.support.clock import START
from tests.support.factories import dispatcher_of, make_container
from tests.support.themes import cafe_theme, matcha_brief, matcha_content

# 말차 라떼 편의 대본을 그대로 읽은 녹음 (줄 사이에 쉼이 있다)
GOOD_READING = Transcript(
    "ko",
    (
        SpeechSegment(300, 2400, "안녕하세요 오늘의 메뉴예요"),
        SpeechSegment(3000, 4300, "말차 라떼입니다"),
        SpeechSegment(5000, 6400, "가격은 5,000원"),
        SpeechSegment(7000, 8300, "매장에서 만나요"),
    ),
)


def series_container(
    settings: Settings | None = None,
    theme_settings: ThemeSettings | None = None,
    **overrides: Any,
) -> Container:
    """화면 글자 읽기까지 켠 Fake 조립. theme_settings·어댑터를 overrides 로 바꿀 수 있다."""
    overrides.setdefault("theme_settings", theme_settings or ThemeSettings(text_reader="fake"))
    return make_container(settings, **overrides)


def series_of(container: Container) -> ThemeServices:
    assert container.series is not None
    return container.series


def save_theme(container: Container, theme: Theme | None = None) -> ThemeRecord:
    """추출을 거치지 않고 테마를 저장소에 바로 넣는다 (원본 IR 은 자리 그대로의 빈 장면)."""
    theme = theme or cafe_theme()
    record = ThemeRecord(
        theme=theme,
        source_ir=IntermediateRepresentation(
            entities=tuple(c.entity_spec() for c in theme.cast),
            scenes=tuple(
                SceneSpec(s.slot_key, *theme.format.span(s.slot_key), s.purpose, s.cast_keys)
                for s in theme.format.slots
            ),
        ),
        created_at=START,
    )
    series_of(container).themes.save(record)
    return record


def new_episode(
    container: Container,
    theme_id: str = "thm_cafe",
    brief: EpisodeBrief | None = None,
    content: EpisodeContent | None = None,
    *,
    planned: bool = False,
) -> Episode:
    """편을 만든다. planned=True 면 내용을 주지 않고 기획기에 맡긴다."""
    command = CreateEpisodeCommand(
        theme_id=theme_id,
        brief=brief or matcha_brief(),
        content=None if planned else (content or matcha_content()),
    )
    return series_of(container).create_episode.execute(command)


def generate_episode(container: Container, episode: Episode) -> Episode:
    """편의 기준 버전을 생성까지 돌린다 (접수 + 파이프라인 실행). 저장소의 최신 편을 돌려준다."""
    container.accept_generation.execute(episode.version_id)
    dispatcher_of(container).run_all()
    latest = series_of(container).episodes.get(episode.episode_id)
    assert latest is not None
    return latest
