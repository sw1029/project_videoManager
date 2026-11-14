"""ReviseEpisode: 편의 조건을 고치는 수정 (US-01, ADR-0026·0027).

그 조건이 걸린 장면만 다시 만든다. 수정 버전은 끝난 뒤에만 편의 기준 버전이 되고, 그때 편의 계획도 새 조건을 따른다.
테마가 고정한 것은 편에서 바꿀 수 없다.
"""

from __future__ import annotations

import pytest

from frame.adapters.generator import FakeVideoGenerator
from frame.adapters.storage import InMemoryArtifactStore
from frame.application.errors import GenerationError
from frame.application.use_cases.series import ReviseEpisodeCommand, SettleEpisodeCommand
from frame.domain.enums import ChangeScope, KeepAspect, RevisionKind, VersionState
from frame.domain.errors import NotFound, ValidationError
from frame.domain.revision import AttributeChange, KeepItem
from tests.support.factories import dispatcher_of, version_of
from tests.support.series import (
    generate_episode,
    new_episode,
    save_theme,
    series_container,
    series_of,
)

PINK_SHOW = AttributeChange("latte", "color", "pink", ChangeScope.SCENES, ("show",))


@pytest.fixture
def kit():
    """(컨테이너, 생성 요청을 기록하는 가짜 생성기, 생성하고 완성본까지 만든 편)."""
    store = InMemoryArtifactStore()
    generator = FakeVideoGenerator(store)
    container = series_container(artifact_store=store, generator=generator)
    save_theme(container)
    episode = generate_episode(container, new_episode(container))
    episode = series_of(container).render_episode.execute(episode.episode_id).episode
    generator.requests.clear()
    return container, generator, episode


def _revise(container, episode, change, **options):
    command = ReviseEpisodeCommand(episode.episode_id, change, **options)
    return series_of(container).revise_episode.execute(command)


def _settled(container, episode, **options):
    command = SettleEpisodeCommand(episode.episode_id, **options)
    return series_of(container).settle_episode.execute(command)


def _values(episode, scene_key):
    return {r.key: r.expected_value for r in episode.plan.ir.scene(scene_key).requirements}


def test_changing_a_condition_remakes_only_the_scenes_it_touches(kit):
    container, generator, episode = kit
    before = version_of(container, episode.version_id)
    keep = (KeepItem(KeepAspect.BACKGROUND),)

    waiting = _revise(
        container, episode, PINK_SHOW, keep=keep, request_text="컵 색을 분홍으로", generate=True
    )

    # 수정 버전이 끝나기 전에는 편의 기준 버전·계획·완성본이 그대로다
    pending = waiting.pending_version_id
    assert pending is not None and waiting.version_id == episode.version_id
    assert waiting.plan == episode.plan and waiting.render == episode.render
    assert series_of(container).episodes.get(episode.episode_id) == waiting

    dispatcher_of(container).run_all()
    revised = _settled(container, episode)

    # 끝나면 그 버전이 기준 버전이 되고, 편의 계획도 새 조건을 따른다. 완성본은 다시 만들어야 한다
    assert revised.version_id == pending and revised.history == (episode.version_id,)
    assert revised.pending_version_id is None and revised.render is None
    assert _values(revised, "show")["show:latte:color"] == "pink"
    assert _values(revised, "closeup")["closeup:latte:color"] == "green"  # 짚은 장면만
    # 문구와 대본은 그대로다 (조건이 아니다)
    assert revised.plan.overlays == episode.plan.overlays
    assert revised.plan.script == episode.plan.script
    revision = version_of(container, revised.version_id)
    assert revision.state is VersionState.COMPLETED and revision.ir == revised.plan.ir
    assert revision.intent.kind is RevisionKind.CHANGE and revision.intent.keep == keep
    assert revision.intent.target_scene_keys == ("show",)
    assert revision.intent.request_text == "컵 색을 분홍으로"
    assert [r.scene.scene_key for r in generator.requests] == ["show"]
    for scene_key in ("greeting", "closeup", "outro"):
        assert revision.clips[scene_key].uri == before.clips[scene_key].uri
    # 완성본을 다시 만들면 문구가 있는 장면은 예전 합성 결과를 다시 쓴다 (클립이 그대로다)
    rendered = series_of(container).render_episode.execute(episode.episode_id)
    assert rendered.composed_now == () and rendered.episode.render.version_id == revised.version_id


def test_a_condition_can_be_added_to_one_scene(kit):
    """둘째 실호출의 경우: 손에 든 장면의 컵에는 글자 조건이 없었다. 그 장면에만 조건을 더한다."""
    container, generator, episode = kit
    change = AttributeChange("latte", "text", "말차", ChangeScope.SCENES, ("show",))

    _revise(container, episode, change, generate=True)
    dispatcher_of(container).run_all()
    revised = _settled(container, episode)

    assert _values(revised, "show")["show:latte:text"] == "말차"
    assert "closeup:latte:text" not in _values(revised, "closeup")
    assert [r.scene.scene_key for r in generator.requests] == ["show"]


def test_failed_revision_leaves_the_episode_on_its_base_version(kit):
    """수정 버전이 실패해도 편은 멈추지 않는다: 기준 버전·계획·완성본이 그대로고, 다시 고칠 수 있다 (감사 BTS-16)."""
    container, generator, episode = kit
    series = series_of(container)
    generator._error = GenerationError("생성 서버 오류", retryable=False)
    waiting = _revise(container, episode, PINK_SHOW, generate=True)
    dispatcher_of(container).run_all()
    assert version_of(container, waiting.pending_version_id).state is VersionState.FAILED

    # 편을 읽는 유스케이스가 들어오면 실패한 수정 버전을 잊는다
    report = series.check_screen.execute(episode.episode_id)
    after = series.episodes.get(episode.episode_id)

    assert (after.version_id, after.pending_version_id, after.history) == (
        episode.version_id,
        None,
        (),
    )
    assert after.plan == episode.plan and after.render == episode.render
    assert report.version_id == episode.version_id and report.checked_requirements > 0
    assert series.render_episode.execute(episode.episode_id).composed_now == ()  # 완성본도 된다
    generator._error = None
    again = _revise(container, episode, PINK_SHOW, generate=True)  # 다시 고칠 수 있다
    assert again.pending_version_id not in (None, waiting.pending_version_id)


def test_no_other_revision_while_one_is_being_made(kit):
    """만들고 있는 수정 버전이 있는 동안에는 다른 수정을 받지 않는다. 만들다 만 초안은 버릴 수 있다."""
    container, generator, episode = kit
    drafted = _revise(container, episode, PINK_SHOW)  # 기본은 초안까지 (생성은 요청해야 접수한다)

    assert version_of(container, drafted.pending_version_id).state is VersionState.DRAFT
    assert dispatcher_of(container).run_all() == 0 and generator.requests == []
    with pytest.raises(ValidationError, match="만들고 있습니다"):
        _revise(container, episode, AttributeChange("latte", "color", "blue"))
    assert _settled(container, episode).pending_version_id == drafted.pending_version_id

    dropped = _settled(container, episode, discard_draft=True)
    assert dropped.pending_version_id is None and dropped.version_id == episode.version_id
    assert _revise(container, episode, AttributeChange("latte", "color", "blue")).pending_version_id


def test_draft_is_not_discarded_once_it_is_running(kit):
    container, _, episode = kit
    running = _revise(container, episode, PINK_SHOW, generate=True)  # 접수됨 (아직 돌지 않았다)

    kept = _settled(container, episode, discard_draft=True)

    assert kept.pending_version_id == running.pending_version_id


def test_what_the_theme_fixes_cannot_be_changed_in_an_episode(kit):
    """고정 출연의 고정 속성과 스타일·자리의 조건은 테마의 약속이다. 편에서 다른 값으로 바꿀 수 없다."""
    container, generator, episode = kit

    with pytest.raises(ValidationError, match="테마"):
        _revise(container, episode, AttributeChange("barista", "outfit", "black hoodie"))
    with pytest.raises(ValidationError, match="테마"):
        _revise(container, episode, AttributeChange(None, "lighting", "neon"))
    with pytest.raises(ValidationError, match="테마"):
        _revise(
            container,
            episode,
            AttributeChange(None, "camera", "wide shot", ChangeScope.SCENES, ("closeup",)),
        )
    # 아무것도 바뀌지 않았다
    assert series_of(container).episodes.get(episode.episode_id) == episode
    assert generator.requests == []


def test_a_fixed_condition_can_still_be_repaired(kit):
    """값을 그대로 두고 그 장면을 다시 맞추는 것(REPAIR)은 약속을 바꾸지 않는다."""
    container, generator, episode = kit
    change = AttributeChange(
        "barista", "outfit", "white shirt and brown apron", ChangeScope.SCENES, ("greeting",)
    )

    _revise(container, episode, change, kind=RevisionKind.REPAIR, generate=True)
    dispatcher_of(container).run_all()
    revised = _settled(container, episode)

    assert version_of(container, revised.version_id).intent.kind is RevisionKind.REPAIR
    assert [r.scene.scene_key for r in generator.requests] == ["greeting"]
    assert revised.plan.ir == episode.plan.ir  # 조건은 그대로다
    assert revised.history == (episode.version_id,)


def test_change_without_scenes_reaches_every_scene_of_the_entity(kit):
    container, _, episode = kit

    _revise(container, episode, AttributeChange("latte", "color", "pink"), generate=True)
    dispatcher_of(container).run_all()
    revised = _settled(container, episode)

    assert _values(revised, "show")["show:latte:color"] == "pink"
    assert _values(revised, "closeup")["closeup:latte:color"] == "pink"


def test_unknown_episode_and_unchanged_value_are_rejected(kit):
    container, _, episode = kit

    with pytest.raises(NotFound):
        series_of(container).revise_episode.execute(
            ReviseEpisodeCommand("epi_none", AttributeChange("latte", "color", "pink"))
        )
    with pytest.raises(NotFound):
        series_of(container).settle_episode.execute(SettleEpisodeCommand("epi_none"))
    with pytest.raises(ValidationError):  # 값이 같으면 CHANGE 가 아니다 (REPAIR 로 요청한다)
        _revise(container, episode, AttributeChange("latte", "color", "green"))
    assert series_of(container).episodes.get(episode.episode_id).pending_version_id is None
