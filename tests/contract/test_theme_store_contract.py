"""테마·편 저장소 계약 테스트 (ADR-0025·0026). 메모리와 산출물 저장소(JSON 문서) 구현이 같은 규약을 지킨다."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from frame.adapters.storage import InMemoryArtifactStore, LocalFileArtifactStore
from frame.adapters.theme_store import (
    ArtifactEpisodeRepository,
    ArtifactThemeRepository,
    InMemoryEpisodeRepository,
    InMemoryThemeRepository,
)
from frame.application.errors import ConfigurationError
from frame.application.ports.artifact_store import ArtifactStore
from frame.application.ports.theme_store import ThemeRecord
from frame.domain.episode import build_episode_plan
from frame.domain.errors import ValidationError
from frame.domain.ir import IntermediateRepresentation, SceneSpec
from frame.domain.screen import Region, TextRole
from frame.domain.series import Episode, NarrationResult, RenderResult
from frame.domain.services.narration import LineAlignment, LineStatus, Placement, PlacementStatus
from frame.domain.shortform import check_platforms
from frame.domain.theme import AnchorOrigin
from frame.domain.video_analysis import (
    ScreenText,
    SpeechSegment,
    Transcript,
    VideoAnalysis,
    shots_from_cuts,
)
from tests.support.clock import START
from tests.support.themes import cafe_theme, matcha_brief, matcha_content


@pytest.fixture(params=["memory", "artifact_memory", "artifact_local_fs"])
def repositories(request, tmp_path):
    if request.param == "memory":
        return InMemoryThemeRepository(), InMemoryEpisodeRepository()
    store = (
        InMemoryArtifactStore()
        if request.param == "artifact_memory"
        else LocalFileArtifactStore(tmp_path / "artifacts")
    )
    return ArtifactThemeRepository(store), ArtifactEpisodeRepository(store)


def _record(theme_id: str = "thm_cafe", minutes: int = 0) -> ThemeRecord:
    theme = cafe_theme(theme_id).with_reference(
        "mug", "mem://references/mug.jpg", AnchorOrigin.SOURCE, "white ceramic mug"
    )
    media = theme.source.media
    return ThemeRecord(
        theme=theme,
        source_ir=IntermediateRepresentation(
            entities=tuple(c.entity_spec() for c in theme.cast),
            scenes=tuple(
                SceneSpec(s.slot_key, *theme.format.span(s.slot_key), s.purpose, s.cast_keys)
                for s in theme.format.slots
            ),
        ),
        created_at=START + timedelta(minutes=minutes),
        analysis=VideoAnalysis(
            media,
            shots_from_cuts([4000, 8000, 12000], media.duration_ms),
            Transcript("ko", (SpeechSegment(400, 2600, "안녕하세요, 오늘의 메뉴예요."),)),
        ),
        screen_texts=(
            ScreenText("오늘의 메뉴", 0, 4000, Region(0.1, 0.08, 0.8, 0.08), TextRole.TITLE),
        ),
        shortform=check_platforms(media).to_dict(),
        notes=("메모 하나",),
        detail={"method": "fake", "usage": {"input_tokens": 10}},
    )


def _episode(episode_id: str = "ep_1", index: int = 1, theme_id: str = "thm_cafe") -> Episode:
    brief = matcha_brief()
    return Episode(
        episode_id=episode_id,
        theme_id=theme_id,
        index=index,
        project_id="prj_1",
        version_id="ver_1",
        brief=brief,
        plan=build_episode_plan(cafe_theme(theme_id), brief, matcha_content()),
        created_at=START,
    )


# ---- 테마 ----
def test_theme_record_round_trips(repositories):
    themes, _ = repositories
    record = _record()
    themes.save(record)

    loaded = themes.get("thm_cafe")
    assert loaded == record
    loaded.theme.validate()
    loaded.source_ir.validate()
    assert themes.get("missing") is None


def test_saving_again_overwrites_and_list_is_in_creation_order(repositories):
    themes, _ = repositories
    themes.save(_record("thm_b", minutes=5))
    themes.save(_record("thm_a", minutes=1))
    themes.save(replace(_record("thm_b", minutes=5), notes=("고친 메모",)))

    assert [r.theme.theme_id for r in themes.list()] == ["thm_a", "thm_b"]
    assert themes.get("thm_b").notes == ("고친 메모",)


def test_empty_repository_lists_nothing(repositories):
    themes, episodes = repositories
    assert themes.list() == [] and episodes.list_by_theme("thm_cafe") == []


# ---- 편 ----
def test_episode_round_trips_with_narration_and_render(repositories):
    _, episodes = repositories
    episode = replace(
        _episode(),
        narration=NarrationResult(
            recording_uris=("mem://episodes/ep_1/recordings/0.wav",),
            alignments=(
                LineAlignment(
                    "L1", LineStatus.MATCHED, 1.0, "안녕하세요 오늘의 메뉴예요", 300, 2400
                ),
                LineAlignment(
                    "L3", LineStatus.MISMATCH, 0.83, "가격은 육천 원", 5000, 6400, 0, ("5,000원",)
                ),
            ),
            placements=(
                Placement("L1", "greeting", PlacementStatus.PLACED, 0, 220, 2480, 200, 1.0, 2260),
            ),
            track_uri="mem://episodes/ep_1/narration.wav",
        ),
        render=RenderResult(
            "mem://episodes/ep_1/final.mp4",
            "ver_1",
            16000,
            {"greeting": "mem://episodes/ep_1/scenes/greeting.mp4"},
            ("greeting",),
            True,
        ),
    )
    episodes.save(episode)

    loaded = episodes.get("ep_1")
    assert loaded == episode
    assert loaded.narration.problem_lines == ("L3",) and not loaded.narration.ok
    assert episodes.get("missing") is None


def test_episodes_are_listed_by_theme_in_series_order(repositories):
    _, episodes = repositories
    episodes.save(_episode("ep_b", 2))
    episodes.save(_episode("ep_a", 1))
    episodes.save(_episode("ep_other", 1, theme_id="thm_other"))
    episodes.save(replace(_episode("ep_b", 2), version_id="ver_9"))

    assert [e.episode_id for e in episodes.list_by_theme("thm_cafe")] == ["ep_a", "ep_b"]
    assert episodes.get("ep_b").version_id == "ver_9"


# ---- 편의 기록 규칙 ----
def test_moving_to_a_revision_keeps_history_and_drops_the_render():
    episode = replace(_episode(), render=RenderResult("mem://final.mp4", "ver_1", 16000))
    moved = episode.with_version("ver_2")

    assert (moved.version_id, moved.history, moved.render) == ("ver_2", ("ver_1",), None)
    assert episode.with_version("ver_1") is episode


# ---- 산출물 저장소 구현의 제약 ----
def test_artifact_repository_refuses_unsafe_ids():
    themes = ArtifactThemeRepository(InMemoryArtifactStore())
    with pytest.raises(ValidationError):
        themes.get("../secret")
    with pytest.raises(ValidationError):
        themes.save(replace(_record(), theme=replace(_record().theme, theme_id="a/b")))


def test_artifact_repository_needs_a_store_with_predictable_uris():
    class OpaqueStore(ArtifactStore):
        def put(self, key, data, content_type=None):
            return "remote://1"

        def get(self, uri):
            return b""

        def exists(self, uri):
            return False

    with pytest.raises(ConfigurationError):
        ArtifactThemeRepository(OpaqueStore())
