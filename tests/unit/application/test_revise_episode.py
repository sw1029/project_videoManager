"""ReviseEpisode: 편의 조건을 고치는 수정 (US-01, ADR-0026·0027).

그 조건이 걸린 장면만 다시 만든다. 수정 버전은 끝난 뒤에만 편의 기준 버전이 되고, 그때 편의 계획도 새 조건을 따른다.
테마가 고정한 것은 편에서 바꿀 수 없다.
"""

from __future__ import annotations

import pytest

from frame.adapters.generator import FakeVideoGenerator
from frame.adapters.storage import InMemoryArtifactStore
from frame.application.errors import GenerationError
from frame.application.use_cases.edit_draft_ir import EditDraftIrCommand
from frame.application.use_cases.series import ReviseEpisodeCommand, SettleEpisodeCommand
from frame.domain.enums import ChangeScope, KeepAspect, RevisionKind, VersionState
from frame.domain.errors import IntegrityViolation, NotFound, ValidationError
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


# ---- 편의 버전을 편 밖에서 고쳤을 때 (감사 P-20: 일반 PUT /ir 과 편) ----
# 편의 버전도 프로젝트의 버전이라, 초안인 동안에는 일반 IR 편집(#11)이 닿는다.
# 편의 계획(화면 문구 · 대본)은 그 편집을 모른다.


def _edit_ir_outside(container, version_id, change) -> None:
    """일반 `PUT /versions/{id}/ir` 이 하는 일: 버전의 IR 문서를 고쳐 저장한다."""
    document = version_of(container, version_id).ir.to_dict()
    change(document)
    container.edit_draft_ir.execute(EditDraftIrCommand(version_id, document))


def _drop_last_scene(document) -> None:
    document["scenes"].pop()


def _make_the_latte_pink(document) -> None:
    """장면의 설명 하나와 이 편만의 조건(라떼의 색)을 고친다. 장면의 짜임은 그대로다."""
    for scene in document["scenes"]:
        if scene["scene_key"] == "show":
            scene["description"] = "바리스타가 딸기 라떼가 담긴 컵을 들어 보인다"
        for requirement in scene["requirements"]:
            if (requirement.get("entity_key"), requirement["predicate"]) == ("latte", "color"):
                requirement["expected_value"] = "pink"


def test_a_revision_edited_outside_the_episode_does_not_break_the_episode(kit):
    """수정 버전의 초안에서 장면을 없앤 뒤 생성해도, 편은 맞는 계획과 함께 기준 버전에 남는다.

    전에는 그 버전이 기준 버전이 되고, 없는 장면의 대본 줄이 든 계획이 저장됐다.
    """
    container, _, episode = kit
    series = series_of(container)
    pending = _revise(container, episode, PINK_SHOW).pending_version_id
    _edit_ir_outside(container, pending, _drop_last_scene)
    container.accept_generation.execute(pending)
    dispatcher_of(container).run_all()
    assert version_of(container, pending).state is VersionState.COMPLETED

    report = series.check_screen.execute(
        episode.episode_id
    )  # 편의 유스케이스가 끝난 수정 버전을 반영한다

    latest = series.episodes.get(episode.episode_id)
    assert (latest.version_id, latest.pending_version_id) == (episode.version_id, None)
    assert latest.plan.ir == episode.plan.ir and latest.render == episode.render
    latest.plan.validate()
    assert pending in latest.plan.notes[-1]
    assert report.version_id == episode.version_id


def test_an_episode_follows_conditions_edited_outside_when_the_scene_layout_is_the_same():
    """기준 버전(초안)에서 장면의 설명과 조건의 값만 고쳤으면 편의 계획이 그 IR 을 따른다 (메모를 남긴다).

    버전의 IR 이 실제로 만드는 영상의 조건이다. 전에는 계획이 옛 조건을 들고 있어, 화면 검수가 만들지 않은 조건으로
    견줬다. 편을 읽는 유스케이스가 들어올 때 따른다 (settle_pending).
    """
    container = series_container()
    save_theme(container)
    series = series_of(container)
    episode = new_episode(container)
    _edit_ir_outside(container, episode.version_id, _make_the_latte_pink)

    followed = _settled(container, episode)

    assert followed.plan.ir == version_of(container, episode.version_id).ir != episode.plan.ir
    assert _values(followed, "show")["show:latte:color"] == "pink"
    assert (followed.plan.script, followed.plan.overlays) == (
        episode.plan.script,
        episode.plan.overlays,
    )
    assert followed.plan.notes[:-1] == episode.plan.notes
    assert episode.version_id in followed.plan.notes[-1] and "편 밖에서" in followed.plan.notes[-1]
    assert series.episodes.get(episode.episode_id) == followed  # 따른 편을 저장했다
    assert _settled(container, episode) == followed  # 한 번만 따른다 (메모가 늘지 않는다)

    generate_episode(container, followed)
    rendered = series.render_episode.execute(episode.episode_id).episode
    assert rendered.render is not None and rendered.plan.ir == followed.plan.ir


@pytest.mark.parametrize("generated", [False, True], ids=["draft", "generated"])
def test_an_episode_whose_base_version_was_edited_outside_says_so(generated):
    """편의 기준 버전(초안)에서 장면의 짜임을 바꾸면 편의 계획이 따를 수 없다. 편의 유스케이스가 그것을 알린다.

    전에는 알리지 않았다: 완성본 만들기는 "영상이 아직 없습니다" 라고 했고 (없는 장면의 클립을 끝없이 기다린다),
    화면 검수는 어긋난 계획으로 견주고도 통과로 답했다.
    """
    container = series_container()
    save_theme(container)
    series = series_of(container)
    episode = new_episode(container)
    _edit_ir_outside(container, episode.version_id, _drop_last_scene)
    if generated:
        generate_episode(container, episode)

    for use in (
        lambda: series.render_episode.execute(episode.episode_id),
        lambda: series.check_screen.execute(episode.episode_id),
        lambda: _revise(container, episode, PINK_SHOW),
    ):
        with pytest.raises(IntegrityViolation, match="편 밖에서") as raised:
            use()
        assert episode.version_id in raised.value.message
    assert series.episodes.get(episode.episode_id) == episode  # 편 문서는 건드리지 않았다


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
