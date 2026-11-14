"""CreateEpisode·AdoptAnchors: 테마로 편을 만들고, 편 사이에 같은 모습을 잇는다 (ADR-0026)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from frame.adapters.generator import FakeVideoGenerator
from frame.adapters.series import TemplateSeriesPlanner
from frame.adapters.storage import InMemoryArtifactStore
from frame.application.series import AdoptAnchorsCommand, CreateEpisodeCommand
from frame.config import Settings
from frame.domain.enums import ReferenceOrigin, VersionState
from frame.domain.errors import NotFound, ValidationError
from frame.domain.theme import AnchorOrigin, SourceOrigin
from tests.support.factories import version_of
from tests.support.series import (
    generate_episode,
    new_episode,
    save_theme,
    series_container,
    series_of,
)
from tests.support.themes import cafe_theme, matcha_brief, matcha_content


@pytest.fixture
def container():
    container = series_container()
    save_theme(container)
    return container


def test_episode_becomes_a_project_with_a_draft_version(container):
    episode = new_episode(container)
    version = version_of(container, episode.version_id)
    project = container.queries.get_project(episode.project_id).project

    assert version.state is VersionState.DRAFT and version.ir == episode.plan.ir
    assert version.target_duration_ms == 16000 and version.seed == 7
    assert "카페 신메뉴 소개" in version.source_prompt and "말차 라떼" in version.source_prompt
    assert project.title == "카페 신메뉴 소개 — 말차 라떼 편"
    assert (episode.theme_id, episode.index) == ("thm_cafe", 1)
    assert series_of(container).episodes.get(episode.episode_id) == episode


def test_episode_is_generated_by_the_existing_pipeline(container):
    episode = generate_episode(container, new_episode(container))
    version = version_of(container, episode.version_id)

    assert version.state is VersionState.COMPLETED and version.video_uri
    assert set(version.clips) == {"greeting", "show", "closeup", "outro"}


def test_episodes_of_a_theme_are_numbered_in_order(container):
    first = new_episode(container)
    second = new_episode(container, brief=matcha_brief(title="딸기 라떼 편"))

    assert (first.index, second.index) == (1, 2)
    listed = series_of(container).episodes.list_by_theme("thm_cafe")
    assert [e.episode_id for e in listed] == [first.episode_id, second.episode_id]
    assert first.project_id != second.project_id


def test_planner_fills_the_content_when_none_is_given():
    planner = TemplateSeriesPlanner()
    container = series_container(series_planner=planner)
    save_theme(container)
    new_episode(container)  # 앞선 편
    episode = new_episode(container, brief=matcha_brief(title="말차 라떼 다시"), planned=True)

    assert [line.text for line in episode.plan.script][1] == "말차 라떼입니다."
    request = planner.requests[0]
    assert [e.title for e in request.earlier] == ["말차 라떼 편"]  # 앞선 편을 기획에 알려 준다
    assert request.earlier[0].variables == {"menu": "말차 라떼", "price": "5,000원"}


def test_bad_request_creates_nothing(container):
    series = series_of(container)
    with pytest.raises(NotFound):
        series.create_episode.execute(CreateEpisodeCommand("thm_none", matcha_brief()))
    broken = replace(matcha_content(), slots=matcha_content().slots[:2])
    with pytest.raises(ValidationError):
        new_episode(container, content=broken)
    with pytest.raises(ValidationError):
        new_episode(container, brief=matcha_brief(variables={"menu": "말차 라떼"}))
    assert series.episodes.list_by_theme("thm_cafe") == []


# ---- 편 사이의 정체성 ----
def _identity_container(theme=None):
    """정체성 기준 이미지를 뽑는 조립 (ADR-0015 의 fake 추출기)."""
    store = InMemoryArtifactStore()
    generator = FakeVideoGenerator(store)
    container = series_container(
        Settings(jobs="recording", persistence="memory", identity="fake"),
        artifact_store=store,
        generator=generator,
    )
    save_theme(container, theme)
    return container, generator


def test_theme_reference_becomes_the_user_reference_of_every_episode():
    theme = cafe_theme().with_reference(
        "barista", "mem://references/barista.png", AnchorOrigin.SOURCE, "young woman, dark hair"
    )
    container, generator = _identity_container(theme)
    container.artifact_store.put("references/barista.png", b"png-bytes", "image/png")
    episode = generate_episode(container, new_episode(container))

    references = container.queries.get_version(episode.version_id).reference_images
    user = [r for r in references if r.origin is ReferenceOrigin.USER]
    assert [(r.entity_key, r.image_uri) for r in user] == [
        ("barista", "mem://references/barista.png")
    ]
    # 바리스타가 나오는 장면의 생성 요청마다 그 이미지가 참조로 간다 (나오지 않는 장면에는 가지 않는다)
    sent = {r.scene.scene_key: [ref.image_uri for ref in r.references] for r in generator.requests}
    assert sent["greeting"] == sent["show"] == sent["outro"] == ["mem://references/barista.png"]
    assert sent["closeup"] == []
    # 사용자 참조가 있는 객체는 클립에서 다시 뽑지 않는다 (그 이미지가 기준이다)
    derived = {r.entity_key for r in references if r.origin is ReferenceOrigin.DERIVED}
    assert "barista" not in derived


def test_pilot_episode_anchors_the_look_for_later_episodes():
    """기준 이미지가 없는 테마: 처음 만든 편에서 뽑은 기준을 테마에 붙이면 다음 편부터 그것을 쓴다."""
    container, generator = _identity_container()
    series = series_of(container)
    pilot = generate_episode(container, new_episode(container))

    result = series.adopt_anchors.execute(AdoptAnchorsCommand("thm_cafe", pilot.version_id))

    assert set(result.adopted) == {"barista", "mug"}
    theme = series.themes.get("thm_cafe").theme
    assert all(c.reference_origin is AnchorOrigin.PILOT for c in theme.references())
    anchors = {c.entity_key: c.reference_image_uri for c in theme.references()}

    generator.requests.clear()
    second = generate_episode(container, new_episode(container, brief=matcha_brief(title="2편")))
    sent = {
        r.scene.scene_key: {ref.entity_key: ref.image_uri for ref in r.references}
        for r in generator.requests
    }
    assert sent["show"] == anchors  # 바리스타와 컵 모두 첫 편의 모습
    assert sent["closeup"] == {"mug": anchors["mug"]}
    assert second.version_id != pilot.version_id
    # 이미 붙어 있으면 다시 붙이지 않는다 (overwrite 를 주면 바꾼다)
    again = series.adopt_anchors.execute(AdoptAnchorsCommand("thm_cafe", second.version_id))
    assert again.adopted == ()


def test_anchor_can_be_adopted_under_a_different_key():
    """FRAME 이 만든 원본 영상의 객체 키가 테마의 키와 다를 때 (red_mug -> mug)."""
    container, _ = _identity_container()
    series = series_of(container)
    pilot = generate_episode(container, new_episode(container))
    command = AdoptAnchorsCommand(
        "thm_cafe", pilot.version_id, AnchorOrigin.SOURCE, key_map={"mug": "latte"}
    )
    result = series.adopt_anchors.execute(command)

    theme = result.record.theme
    assert (
        "mug" in result.adopted and theme.cast_member("mug").reference_origin is AnchorOrigin.SOURCE
    )
    with pytest.raises(ValidationError):
        series.adopt_anchors.execute(replace(command, key_map={"ghost": "latte"}))
    with pytest.raises(NotFound):
        series.adopt_anchors.execute(AdoptAnchorsCommand("thm_cafe", "ver_none"))
    with pytest.raises(NotFound):
        series.adopt_anchors.execute(AdoptAnchorsCommand("thm_none", pilot.version_id))


def test_external_theme_cannot_take_anchors_cut_from_its_source():
    container, _ = _identity_container(cafe_theme(origin=SourceOrigin.EXTERNAL))
    series = series_of(container)
    pilot = generate_episode(container, new_episode(container))

    with pytest.raises(ValidationError, match="외부 영상"):
        series.adopt_anchors.execute(
            AdoptAnchorsCommand("thm_cafe", pilot.version_id, AnchorOrigin.SOURCE)
        )
    assert series.themes.get("thm_cafe").theme.references() == ()
    # 처음 만든 편의 것은 받을 수 있다
    assert series.adopt_anchors.execute(AdoptAnchorsCommand("thm_cafe", pilot.version_id)).adopted
