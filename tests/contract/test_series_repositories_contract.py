"""테마·편 저장소 계약 테스트 (ADR-0025·0026). 메모리와 산출물 저장소(JSON 문서) 구현이 같은 규약을 지킨다."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from frame.adapters.series.store import (
    ArtifactEpisodeRepository,
    ArtifactThemeRepository,
    InMemoryEpisodeRepository,
    InMemoryThemeRepository,
)
from frame.adapters.storage import InMemoryArtifactStore, LocalFileArtifactStore
from frame.application.errors import ConfigurationError, StorageError
from frame.application.ports.artifact_store import ArtifactStore
from frame.application.ports.series.repositories import ThemeRecord
from frame.domain.enums import VersionState
from frame.domain.errors import ValidationError
from frame.domain.ir import IntermediateRepresentation, SceneSpec
from frame.domain.screen import Region, TextRole
from frame.domain.series.episode import OverlayItem, OverlayKind, build_episode_plan
from frame.domain.series.narration import LineAlignment, LineStatus, Placement, PlacementStatus
from frame.domain.series.record import Episode, NarrationResult, RenderResult
from frame.domain.series.screen_correction import patch_overlay_id
from frame.domain.series.shortform import check_platforms
from frame.domain.series.source_analysis import ScreenText, VideoAnalysis, shots_from_cuts
from frame.domain.series.theme import AnchorOrigin
from frame.domain.speech import SpeechSegment, Transcript
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
@pytest.mark.parametrize(
    "bad",
    ["../secret", "a/b", "thm_1\n", "index.json", "thm.a", "THM_A", "", "-x" * 40],
)
def test_memory_repository_refuses_the_same_ids(bad):
    """메모리 저장소도 같은 id 만 받는다. 테스트에서 통과한 id 가 실제 저장소에서 거절되지 않게 (감사 BTS-14)."""
    themes, episodes = InMemoryThemeRepository(), InMemoryEpisodeRepository()
    with pytest.raises(ValidationError):
        themes.get(bad)
    with pytest.raises(ValidationError):
        themes.save(replace(_record(), theme=replace(_record().theme, theme_id=bad)))
    with pytest.raises(ValidationError):
        episodes.get(bad)
    with pytest.raises(ValidationError):
        episodes.save(replace(_episode(), episode_id=bad))


@pytest.mark.parametrize(
    "bad",
    ["../secret", "a/b", "thm_1\n", "index.json", "thm.a", "THM_A", "", "-x" * 40],
)
def test_artifact_repository_refuses_unsafe_ids(bad):
    """id 는 소문자·숫자·밑줄·붙임표만 받는다 (점·대문자·줄바꿈·경로 구분자를 받지 않는다, 감사 BTS-14)."""
    themes = ArtifactThemeRepository(InMemoryArtifactStore())
    with pytest.raises(ValidationError):
        themes.get(bad)
    with pytest.raises(ValidationError):
        themes.save(replace(_record(), theme=replace(_record().theme, theme_id=bad)))


def test_broken_index_is_reported_and_not_overwritten():
    """색인이 목록이 아니면 빈 목록으로 읽지 않는다. 그러면 다음 저장이 색인을 덮어써 다른 테마가 목록에서 사라진다."""
    store = InMemoryArtifactStore()
    themes = ArtifactThemeRepository(store)
    themes.save(_record("thm_a"))
    index = store.uri_for("themes/index.json")
    store.put("themes/index.json", b'{"oops": 1}', "application/json")

    with pytest.raises(StorageError):
        themes.list()
    with pytest.raises(StorageError):
        themes.save(_record("thm_b"))
    assert store.get(index) == b'{"oops": 1}'  # 색인을 덮어쓰지 않았다


def test_broken_document_is_a_storage_error_when_read_by_id():
    """손으로 고쳐 깨진 문서를 id 로 읽으면 이름 없는 KeyError 가 아니라 저장소 오류다 (감사 BTS-14 의 남은 것)."""
    store = InMemoryArtifactStore()
    themes, episodes = ArtifactThemeRepository(store), ArtifactEpisodeRepository(store)
    themes.save(_record())
    episodes.save(_episode())
    for uri in list(store._blobs):
        if uri.endswith(("theme.json", "episode.json")):
            store._blobs[uri] = (b"{}", "application/json")

    with pytest.raises(StorageError, match="thm_cafe"):
        themes.get("thm_cafe")
    with pytest.raises(StorageError, match="ep_1"):
        episodes.get("ep_1")


def test_theme_id_follows_the_rule_of_the_store():
    """도메인과 저장소의 id 규칙이 같다: 검사를 통과한 테마가 저장에서 거절되지 않는다 (감사 BTS-14 의 남은 것)."""
    theme = _record().theme
    for bad in ("THM_A", "thm.a", "thm a", ""):
        with pytest.raises(ValidationError):
            replace(theme, theme_id=bad).validate()
    replace(theme, theme_id="thm_2-b").validate()


def test_broken_document_is_skipped_when_listing():
    """문서 하나가 깨져도 나머지는 읽힌다 (편 만들기가 테마의 편 목록을 읽는다)."""
    store = InMemoryArtifactStore()
    themes, episodes = ArtifactThemeRepository(store), ArtifactEpisodeRepository(store)
    themes.save(_record("thm_a"))
    themes.save(_record("thm_b", minutes=1))
    episodes.save(_episode("ep_1", 1))
    episodes.save(_episode("ep_2", 2))
    store.put("themes/thm_a/theme.json", b"{}", "application/json")
    store.put("episodes/ep_1/episode.json", b"not json", "application/json")

    assert [r.theme.theme_id for r in themes.list()] == ["thm_b"]
    assert [e.episode_id for e in episodes.list_by_theme("thm_cafe")] == ["ep_2"]


# ---- 편의 키 잠금 (감사 BTS-24) ----
def test_lock_lets_the_same_thread_in_again_and_makes_other_threads_wait(repositories):
    import threading

    _, episodes = repositories
    order: list[str] = []
    entered = threading.Event()

    def worker(key: str, name: str) -> None:
        with episodes.lock(key):
            order.append(name)
            entered.set()

    with episodes.lock("ep_1"):
        with episodes.lock(
            "ep_1"
        ):  # 같은 스레드는 다시 들어간다 (유스케이스가 다른 유스케이스를 부른다)
            pass
        same = threading.Thread(target=worker, args=("ep_1", "same key"))
        other = threading.Thread(target=worker, args=("ep_2", "other key"))
        same.start()
        other.start()
        other.join(5)
        assert order == ["other key"] and same.is_alive()  # 같은 키는 기다리고, 다른 키는 지나간다
    same.join(5)
    assert order == ["other key", "same key"]


# ---- 만들고 있는 수정 버전 (감사 BTS-16) ----
def test_pending_revision_becomes_the_base_only_when_it_completes():
    episode = replace(_episode(), render=RenderResult("mem://final.mp4", "ver_1", 16000))
    waiting = episode.with_pending("ver_2")
    changed = replace(episode.plan.ir, scenes=episode.plan.ir.scenes[:1])

    assert (waiting.version_id, waiting.pending_version_id) == ("ver_1", "ver_2")
    assert waiting.render == episode.render  # 아직 기준 버전의 완성본이 맞다
    for state in (VersionState.DRAFT, VersionState.QUEUED, VersionState.PROCESSING):
        assert waiting.settled(state, changed) == waiting

    done = waiting.settled(VersionState.COMPLETED, changed)
    assert (done.version_id, done.history, done.pending_version_id) == ("ver_2", ("ver_1",), None)
    assert done.plan.ir == changed and done.render is None  # 계획도 새 조건을 따른다

    for state in (
        VersionState.FAILED,
        None,
    ):  # 실패했거나 버전이 없어졌다: 기준 버전 그대로 계속 쓴다
        dropped = waiting.settled(state, None)
        assert (dropped.version_id, dropped.pending_version_id) == ("ver_1", None)
        assert dropped.history == () and dropped.render == episode.render
    assert episode.settled(VersionState.COMPLETED, changed) is episode  # 기다리는 버전이 없다
    assert Episode.from_dict(waiting.to_dict()) == waiting


def test_patches_of_remade_scenes_are_dropped_when_the_revision_becomes_the_base():
    """가린 문구(PATCH)는 그 클립의 그 자리를 가린 것이다 (감사 BTS-D01).

    장면을 다시 만든 수정 버전이 기준 버전이 되면 그 장면의 가린 문구를 계획에서 빼고 메모로 알린다.
    다시 만들지 않은 장면의 가린 문구와, 테마의 문구 틀에서 온 문구는 그대로다.
    """
    episode = _episode()

    def patch(scene_key: str) -> OverlayItem:
        return OverlayItem(
            overlay_id=patch_overlay_id(scene_key, "mug", "text"),
            scene_key=scene_key,
            text="FRAME",
            start_ms=0,
            end_ms=1000,
            region=Region(0.3, 0.4, 0.3, 0.1),
            kind=OverlayKind.PATCH,
        )

    plan = episode.plan.with_overlay(patch("closeup")).with_overlay(patch("show"))
    waiting = replace(episode, plan=plan).with_pending("ver_2")

    done = waiting.settled(VersionState.COMPLETED, plan.ir, remade_scene_keys=("closeup",))

    kept = [o.overlay_id for o in done.plan.overlays]
    assert "closeup:patch:mug:text" not in kept and "show:patch:mug:text" in kept
    assert "closeup:price" in kept  # 문구 틀에서 온 문구는 새 클립에도 얹는다
    assert len(done.plan.notes) == len(plan.notes) + 1
    assert "closeup:patch:mug:text" in done.plan.notes[-1]
    # 다시 만든 장면이 없으면 계획의 문구와 메모는 그대로다
    same = waiting.settled(VersionState.COMPLETED, plan.ir)
    assert same.plan.overlays == plan.overlays and same.plan.notes == plan.notes
    # 수정 버전이 실패했으면 가린 문구도 그대로다 (기준 버전의 클립이 그대로이므로)
    failed = waiting.settled(VersionState.FAILED, None, remade_scene_keys=("closeup",))
    assert failed.plan == plan


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
