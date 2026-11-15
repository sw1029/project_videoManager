"""편·테마 문서는 그 키를 쥔 채 읽고 고쳐 쓴다 (감사 BTS-24 · D03 · D07 · D38).

유스케이스가 잠금을 쥐는지는 잠금 자체의 테스트로는 알 수 없다: 그 줄을 지워도 다른 테스트가 모른다.
여기서는 저장소가 '무슨 키를 쥔 채 무엇을 읽고 썼는지' 를 적게 하고, 유스케이스를 돌린 뒤에 그 기록을 본다.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace

import pytest

from frame.adapters.series.planning import TemplateSeriesPlanner
from frame.adapters.series.store import InMemoryEpisodeRepository, InMemoryThemeRepository
from frame.adapters.shared.media.fake_media import fake_recording
from frame.application.ports.series.repositories import theme_lock_key
from frame.application.use_cases.series import (
    AdoptAnchorsCommand,
    AttachNarrationCommand,
    CorrectScreenCommand,
    CorrectSourceScriptCommand,
    LineCorrection,
    RecordingInput,
)
from frame.domain.errors import ValidationError
from tests.support.series import (
    GOOD_READING,
    generate_episode,
    new_episode,
    save_theme,
    series_container,
    series_of,
)


class _WatchedEpisodes(InMemoryEpisodeRepository):
    """쥐고 있는 키와, 편 문서를 저장할 때 쥐고 있던 키를 적는다."""

    def __init__(self) -> None:
        super().__init__()
        self.held: list[str] = []
        self.saves: list[tuple[str, str, tuple[str, ...]]] = []  # (편, 테마, 그때 쥔 키)

    def lock(self, key):
        inner = super().lock(key)

        @contextmanager
        def watched():
            with inner:
                self.held.append(key)
                try:
                    yield
                finally:
                    self.held.remove(key)

        return watched()

    def save(self, episode) -> None:
        self.saves.append((episode.episode_id, episode.theme_id, tuple(self.held)))
        super().save(episode)


class _WatchedThemes(InMemoryThemeRepository):
    """테마 문서를 읽고 쓸 때 쥐고 있던 키를 적는다."""

    def __init__(self, episodes: _WatchedEpisodes) -> None:
        super().__init__()
        self._episodes = episodes
        self.reads: list[tuple[str, tuple[str, ...]]] = []
        self.saves: list[tuple[str, tuple[str, ...]]] = []

    def get(self, theme_id):
        self.reads.append((theme_id, tuple(self._episodes.held)))
        return super().get(theme_id)

    def save(self, record) -> None:
        self.saves.append((record.theme.theme_id, tuple(self._episodes.held)))
        super().save(record)


@pytest.fixture
def watched():
    episodes = _WatchedEpisodes()
    themes = _WatchedThemes(episodes)
    container = series_container(theme_repository=themes, episode_repository=episodes)
    save_theme(container)
    themes.reads.clear()
    themes.saves.clear()
    return container, themes, episodes


def test_every_episode_document_is_written_while_its_key_is_held(watched):
    """편 문서를 쓰는 유스케이스는 그 편의 키를 쥔다. 새 편은 테마의 키를 쥐고 순번을 매겨 쓴다.

    쥐지 않으면 오래 걸리는 합성·인식 도중에 다른 유스케이스가 한 수정이 마지막 저장에 지워진다.
    """
    container, _, episodes = watched
    series = series_of(container)
    episode = generate_episode(container, new_episode(container))
    series.render_episode.execute(episode.episode_id)
    current = series.episodes.get(episode.episode_id)
    wrong = replace(current.plan.overlay("closeup:price"), text="말차 라떼 6,000원")
    with episodes.lock(episode.episode_id):  # 테스트가 직접 고칠 때도 같은 규칙을 지킨다
        episodes.save(replace(current, plan=current.plan.with_overlay(wrong), render=None))
    series.correct_screen.execute(CorrectScreenCommand(episode.episode_id))
    recording = RecordingInput(fake_recording(GOOD_READING), filename="take.wav")
    series.attach_narration.execute(AttachNarrationCommand(episode.episode_id, (recording,), "ko"))
    series.render_episode.execute(episode.episode_id)
    series.check_screen.execute(episode.episode_id)

    assert len(episodes.saves) >= 5  # 만들기 · 완성본 · 문구 수정 · 녹음 · 완성본
    for episode_id, theme_id, held in episodes.saves:
        assert episode_id in held or theme_lock_key(theme_id) in held, (episode_id, held)
    assert episodes.saves[0][2] == (theme_lock_key("thm_cafe"),)  # 새 편은 테마의 키로
    assert episodes.held == []  # 쥔 키는 모두 놓았다


def test_theme_document_is_read_and_rewritten_under_the_theme_key(watched):
    """테마 문서를 읽고 -> 고쳐 -> 덮어쓰는 유스케이스는 둘이다: 기준 이미지 채택과 원본 대본 교정.

    둘이 같은 키를 쥐지 않으면 겹쳤을 때 한쪽의 수정(기준 이미지 또는 고친 대본)이 지워진다 (감사 BTS-D03·D38).
    """
    container, themes, episodes = watched
    series = series_of(container)
    episode = generate_episode(container, new_episode(container))
    themes.reads.clear()
    key = theme_lock_key("thm_cafe")

    series.adopt_anchors.execute(AdoptAnchorsCommand("thm_cafe", episode.version_id))
    with pytest.raises(ValidationError):  # 이 테마에는 대본이 없다. 그래도 읽는 것은 키를 쥔 뒤다
        series.correct_source_script.execute(
            CorrectSourceScriptCommand("thm_cafe", (LineCorrection("S1", "고친 글"),))
        )

    assert len(themes.reads) >= 2
    assert all(held == (key,) for _, held in themes.reads), themes.reads
    assert all(held == (key,) for _, held in themes.saves), themes.saves
    assert episodes.held == []


def test_episode_numbers_do_not_collide_when_another_episode_appears_while_planning():
    """순번은 기획이 끝난 뒤에, 테마의 키를 쥐고 매긴다. 기획하는 동안 다른 편이 생겨도 번호가 겹치지 않는다."""

    class _SlowPlanner(TemplateSeriesPlanner):
        """기획하는 사이에 같은 테마의 다른 편이 만들어진다."""

        container = None

        def plan(self, request):
            if self.container is not None:
                container, self.container = self.container, None
                new_episode(container)  # 내용을 준 편: 기획기를 거치지 않는다
            return super().plan(request)

    planner = _SlowPlanner()
    container = series_container(series_planner=planner)
    save_theme(container)
    planner.container = container

    late = new_episode(container, planned=True)

    episodes = series_of(container).episodes.list_by_theme("thm_cafe")
    assert sorted(e.index for e in episodes) == [1, 2] and late.index == 2
    assert len({e.episode_id for e in episodes}) == 2
