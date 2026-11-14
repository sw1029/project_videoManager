"""ReviseEpisode: 편의 조건을 고치는 수정 (US-01, ADR-0026·0027).

그 조건이 걸린 장면만 다시 만들고, 편의 계획도 새 조건을 따른다. 테마가 고정한 것은 편에서 바꿀 수 없다.
"""

from __future__ import annotations

import pytest

from frame.adapters.generator import FakeVideoGenerator
from frame.adapters.storage import InMemoryArtifactStore
from frame.application.series import ReviseEpisodeCommand
from frame.domain.enums import ChangeScope, KeepAspect, RevisionKind, VersionState
from frame.domain.errors import NotFound, ValidationError
from frame.domain.revision import KeepItem
from frame.domain.services.propagation import AttributeChange
from tests.support.factories import dispatcher_of, version_of
from tests.support.series import (
    generate_episode,
    new_episode,
    save_theme,
    series_container,
    series_of,
)


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


def _values(episode, scene_key):
    return {r.key: r.expected_value for r in episode.plan.ir.scene(scene_key).requirements}


def test_changing_a_condition_remakes_only_the_scenes_it_touches(kit):
    container, generator, episode = kit
    before = version_of(container, episode.version_id)
    change = AttributeChange("latte", "color", "pink", ChangeScope.SCENES, ("show",))
    keep = (KeepItem(KeepAspect.BACKGROUND),)

    revised = _revise(container, episode, change, keep=keep, request_text="컵 색을 분홍으로")

    # 편은 새 버전을 가리키고, 편의 계획도 새 조건을 따른다. 완성본은 다시 만들어야 한다
    assert revised.version_id != episode.version_id and revised.history == (episode.version_id,)
    assert revised.render is None
    assert _values(revised, "show")["show:latte:color"] == "pink"
    assert _values(revised, "closeup")["closeup:latte:color"] == "green"  # 짚은 장면만
    assert series_of(container).episodes.get(episode.episode_id) == revised
    # 문구와 대본은 그대로다 (조건이 아니다)
    assert revised.plan.overlays == episode.plan.overlays
    assert revised.plan.script == episode.plan.script

    dispatcher_of(container).run_all()
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

    revised = _revise(container, episode, change)

    assert _values(revised, "show")["show:latte:text"] == "말차"
    assert "closeup:latte:text" not in _values(revised, "closeup")
    dispatcher_of(container).run_all()
    assert [r.scene.scene_key for r in generator.requests] == ["show"]


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
    assert (
        series_of(container).episodes.get(episode.episode_id) == episode
    )  # 아무것도 바뀌지 않았다
    assert generator.requests == []


def test_a_fixed_condition_can_still_be_repaired(kit):
    """값을 그대로 두고 그 장면을 다시 맞추는 것(REPAIR)은 약속을 바꾸지 않는다."""
    container, generator, episode = kit
    change = AttributeChange(
        "barista", "outfit", "white shirt and brown apron", ChangeScope.SCENES, ("greeting",)
    )

    revised = _revise(container, episode, change, kind=RevisionKind.REPAIR)

    dispatcher_of(container).run_all()
    revision = version_of(container, revised.version_id)
    assert revision.intent.kind is RevisionKind.REPAIR
    assert [r.scene.scene_key for r in generator.requests] == ["greeting"]
    assert revised.plan.ir == episode.plan.ir  # 조건은 그대로다


def test_revision_is_only_drafted_when_generation_is_not_asked(kit):
    container, generator, episode = kit
    change = AttributeChange("latte", "color", "pink")

    revised = _revise(container, episode, change, generate=False)

    assert version_of(container, revised.version_id).state is VersionState.DRAFT
    assert dispatcher_of(container).run_all() == 0 and generator.requests == []
    assert _values(revised, "show")["show:latte:color"] == "pink"
    assert (
        _values(revised, "closeup")["closeup:latte:color"] == "pink"
    )  # 범위를 안 주면 나오는 모든 장면


def test_unknown_episode_and_unchanged_value_are_rejected(kit):
    container, _, episode = kit

    with pytest.raises(NotFound):
        series_of(container).revise_episode.execute(
            ReviseEpisodeCommand("epi_none", AttributeChange("latte", "color", "pink"))
        )
    with pytest.raises(ValidationError):  # 값이 같으면 CHANGE 가 아니다 (REPAIR 로 요청한다)
        _revise(container, episode, AttributeChange("latte", "color", "green"))
