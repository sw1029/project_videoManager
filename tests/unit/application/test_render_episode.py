"""RenderEpisode: 장면 단위로 화면 문구를 얹고, 이어 붙이고, 녹음을 붙인다 (ADR-0027)."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from frame.adapters.fake_media import decode_fake_media, fake_recording
from frame.adapters.series.overlay import FakeOverlayRenderer
from frame.adapters.series.overlay.fake import decode_fake_composite
from frame.application.use_cases.series import AttachNarrationCommand, RecordingInput
from frame.domain.errors import MediaNotAvailable, NotFound
from tests.support.factories import version_of
from tests.support.series import (
    GOOD_READING,
    generate_episode,
    new_episode,
    save_theme,
    series_container,
    series_of,
)


@pytest.fixture
def renderer():
    return FakeOverlayRenderer()


@pytest.fixture
def container(renderer):
    container = series_container(overlay_renderer=renderer)
    save_theme(container)
    return container


@pytest.fixture
def episode(container):
    return generate_episode(container, new_episode(container))


def _render(container, episode):
    return series_of(container).render_episode.execute(episode.episode_id)


def test_episode_must_be_generated_first(container):
    draft = new_episode(container)

    with pytest.raises(MediaNotAvailable):
        _render(container, draft)
    with pytest.raises(NotFound):
        series_of(container).render_episode.execute("epi_none")


def test_only_scenes_with_overlays_are_composed(container, episode, renderer):
    result = _render(container, episode)
    render = result.episode.render
    clips = version_of(container, episode.version_id).clips

    assert render.composed_scene_keys == ("greeting", "closeup")
    assert result.composed_now == ("greeting", "closeup") and result.reused == ()
    # 문구가 없는 장면은 버전의 클립 그대로다 (다시 만들지 않는다)
    assert render.scene_uris["show"] == clips["show"].uri
    assert render.scene_uris["outro"] == clips["outro"].uri
    assert render.scene_uris["closeup"] != clips["closeup"].uri
    composite = decode_fake_composite(container.artifact_store.get(render.scene_uris["closeup"]))
    assert [o["text"] for o in composite["overlays"]] == ["말차 라떼 5,000원"]
    assert len(renderer.renders) == 2
    assert render.version_id == episode.version_id and render.duration_ms == 16000


def test_final_video_joins_the_scenes_in_order(container, episode):
    render = _render(container, episode).episode.render
    manifest = json.loads(container.artifact_store.get(render.video_uri))

    assert manifest["clips"] == [
        render.scene_uris[k] for k in ("greeting", "show", "closeup", "outro")
    ]
    assert render.with_narration is False
    assert series_of(container).episodes.get(episode.episode_id).render == render


def test_rendering_again_reuses_what_was_composed(container, episode, renderer):
    first = _render(container, episode)
    second = _render(container, episode)

    assert second.composed_now == () and second.reused == ("greeting", "closeup")
    assert second.episode.render.scene_uris == first.episode.render.scene_uris
    assert len(renderer.renders) == 2  # 합성기는 다시 불리지 않았다


def test_changing_one_overlay_recomposes_only_its_scene(container, episode, renderer):
    """문구 하나를 고치면 그 장면만 다시 합성되고, 나머지 장면의 클립은 그대로다."""
    before = _render(container, episode).episode.render
    series = series_of(container)
    current = series.episodes.get(episode.episode_id)
    fixed = replace(current.plan.overlay("closeup:price"), text="말차 라떼 5,500원")
    series.episodes.save(replace(current, plan=current.plan.with_overlay(fixed), render=None))

    result = _render(container, episode)
    after = result.episode.render

    assert result.composed_now == ("closeup",) and result.reused == ("greeting",)
    unchanged = ("greeting", "show", "outro")
    assert {k: after.scene_uris[k] for k in unchanged} == {
        k: before.scene_uris[k] for k in unchanged
    }
    assert after.scene_uris["closeup"] != before.scene_uris["closeup"]
    assert len(renderer.renders) == 3
    # 고치기 전의 합성 결과도 남아 있다 (되돌리면 다시 쓴다)
    assert container.artifact_store.exists(before.scene_uris["closeup"])


def test_narration_track_is_muxed_onto_the_final_video(container, episode):
    series = series_of(container)
    silent = _render(container, episode).episode.render
    series.attach_narration.execute(
        AttachNarrationCommand(
            episode.episode_id, (RecordingInput(fake_recording(GOOD_READING), filename="take.wav"),)
        )
    )
    result = _render(container, episode)
    render = result.episode.render

    assert render.with_narration and render.video_uri != silent.video_uri
    assert result.composed_now == ()  # 소리만 붙였다: 장면은 다시 합성하지 않는다
    final = decode_fake_media(container.artifact_store.get(render.video_uri))
    said = [s["text"] for s in final["audio"]["transcript"]["segments"]]
    assert said == [
        "안녕하세요 오늘의 메뉴예요",
        "말차 라떼입니다",
        "가격은 5,000원",
        "매장에서 만나요",
    ]
    assert render.scene_uris == silent.scene_uris


def test_changing_how_overlays_are_drawn_recomposes_the_scenes(container, episode, renderer):
    """합성 결과는 '클립 + 문구 + 그리는 설정' 으로 정한다.

    글꼴에 글자가 없어 빈 네모로 찍힌 문구는 글꼴을 바꿔 고친다. 문구는 그대로이므로, 설정이 키에 없으면
    깨진 합성 결과를 다시 쓰게 된다.
    """
    first = _render(container, episode).episode.render
    assert _render(container, episode).composed_now == ()

    renderer.signature = "fake:another-font"
    changed = _render(container, episode)

    assert changed.composed_now == ("greeting", "closeup") and changed.reused == ()
    assert changed.episode.render.scene_uris["closeup"] != first.scene_uris["closeup"]
    # 문구가 없는 장면은 설정과 무관하다
    assert changed.episode.render.scene_uris["show"] == first.scene_uris["show"]

    renderer.signature = "fake"  # 되돌리면 예전 합성 결과를 다시 쓴다
    back = _render(container, episode)
    assert back.composed_now == () and back.episode.render.scene_uris == first.scene_uris
