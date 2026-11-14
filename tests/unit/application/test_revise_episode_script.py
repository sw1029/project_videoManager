"""ReviseEpisodeScript: 편의 녹음 대본만 고친다 (ADR-0029). 영상은 그대로, 바뀐 줄만 다시 녹음한다."""

from __future__ import annotations

from dataclasses import replace

import pytest

from frame.adapters.fake_media import fake_recording
from frame.adapters.generator import FakeVideoGenerator
from frame.adapters.speech import FakeSpeechRecognizer
from frame.adapters.storage import InMemoryArtifactStore
from frame.application.use_cases.series import (
    AttachNarrationCommand,
    RecordingInput,
    ReviseEpisodeScriptCommand,
)
from frame.domain.errors import NotFound, ValidationError
from frame.domain.series.narration import LineStatus
from frame.domain.series.screen_correction import CorrectionMethod, IssueKind
from frame.domain.series.script_revision import ScriptEdit
from frame.domain.speech import SpeechSegment, Transcript
from tests.support.series import (
    GOOD_READING,
    generate_episode,
    new_episode,
    save_theme,
    series_container,
    series_of,
)
from tests.support.themes import cafe_theme


def _captioned_theme():
    """자막이 말을 받아 적는 테마."""
    theme = cafe_theme()
    rules = tuple(replace(r, follows_speech=r.rule_key == "caption") for r in theme.text_rules)
    return replace(theme, text_rules=rules)


def _kit(theme=None):
    """(컨테이너, 생성 요청을 기록하는 가짜 생성기, 생성하고 완성본까지 만든 편)."""
    store = InMemoryArtifactStore()
    generator = FakeVideoGenerator(store)
    container = series_container(artifact_store=store, generator=generator)
    save_theme(container, theme)
    episode = generate_episode(container, new_episode(container))
    episode = series_of(container).render_episode.execute(episode.episode_id).episode
    generator.requests.clear()
    return container, generator, episode


def _revise(container, episode, *edits: ScriptEdit):
    command = ReviseEpisodeScriptCommand(episode.episode_id, edits)
    return series_of(container).revise_episode_script.execute(command)


def _attach(container, episode, *recordings: RecordingInput):
    command = AttachNarrationCommand(episode.episode_id, tuple(recordings), "ko")
    return series_of(container).attach_narration.execute(command)


def _line_recording(line_key: str, text: str) -> RecordingInput:
    recording = fake_recording(Transcript("ko", (SpeechSegment(150, 1500, text),)))
    return RecordingInput(recording, line_key=line_key, filename=f"{line_key}.wav")


def _heard_on_track(container, episode) -> list[str]:
    series = series_of(container)
    track = container.artifact_store.get(episode.narration.track_uri)
    heard = FakeSpeechRecognizer().transcribe(series.audio.speech_audio(track))
    return [s.text for s in heard.segments]


def test_only_the_script_changes_and_the_video_is_not_made_again():
    container, generator, episode = _kit()

    result = _revise(container, episode, ScriptEdit("L2", "초록빛 말차 라떼예요."))

    updated = result.episode
    assert result.changed_lines == ("L2",) and result.changed_overlays == ()
    assert updated.plan.line("L2").text == "초록빛 말차 라떼예요."
    assert updated.plan.ir == episode.plan.ir and updated.plan.overlays == episode.plan.overlays
    assert (updated.version_id, updated.pending_version_id) == (episode.version_id, None)
    assert updated.render == episode.render  # 화면도 소리도 그대로라 완성본을 다시 만들 일이 없다
    assert generator.requests == []  # 영상 생성 호출이 없다
    assert series_of(container).episodes.get(episode.episode_id) == updated
    assert result.rerecord == ()  # 붙여 둔 녹음이 없다


def test_the_changed_line_is_taken_off_the_track_and_asked_to_be_recorded_again():
    container, _, episode = _kit()
    series = series_of(container)
    episode = _attach(container, episode, RecordingInput(fake_recording(GOOD_READING)))
    assert episode.narration.ok
    calls = len(series.recognizer.calls)

    result = _revise(container, episode, ScriptEdit("L3", "가격은 단돈 5,000원."))

    updated = result.episode
    assert result.rerecord == ("L3",)
    statuses = {a.line_key: a.status for a in updated.narration.alignments}
    assert statuses == {
        "L1": LineStatus.MATCHED,
        "L2": LineStatus.MATCHED,
        "L3": LineStatus.MISSING,
        "L4": LineStatus.MATCHED,
    }
    # 나머지 줄의 녹음은 그대로 쓴다. 트랙에서는 그 줄의 소리만 빠진다
    assert updated.narration.recording_uris == episode.narration.recording_uris
    assert _heard_on_track(container, updated) == [
        "안녕하세요 오늘의 메뉴예요",
        "말차 라떼입니다",
        "매장에서 만나요",
    ]
    assert updated.render is None  # 소리가 바뀌었다
    assert len(series.recognizer.calls) == calls  # 인식을 다시 하지 않는다
    # 화면 검수는 그 줄을 다시 녹음할 줄로 알린다
    report = series.check_screen.execute(episode.episode_id)
    issue = report.issue("narration:L3")
    assert (issue.kind, issue.method) == (IssueKind.NARRATION, CorrectionMethod.RERECORD)

    # 그 줄만 다시 녹음해 주면 갈아 끼우고 대본과 맞는지 검사한다
    again = _attach(container, updated, _line_recording("L3", "가격은 단돈 오천 원"))

    assert again.narration.ok
    assert "가격은 단돈 오천 원" in _heard_on_track(container, again)
    kept = {a.line_key: a for a in again.narration.alignments}
    before = {a.line_key: a for a in episode.narration.alignments}
    for key in ("L1", "L2", "L4"):
        assert kept[key] == before[key]


def test_a_caption_that_follows_the_speech_changes_with_the_line():
    container, generator, episode = _kit(_captioned_theme())
    series = series_of(container)

    result = _revise(container, episode, ScriptEdit("L2", "초록빛 말차 라떼예요."))

    assert result.changed_overlays == ("show:caption",)
    assert result.episode.plan.overlay("show:caption").text == "초록빛 말차 라떼예요."
    assert result.episode.render is None
    # 완성본을 다시 만들면 자막이 바뀐 장면만 다시 합성된다. 영상 생성은 없다
    rendered = series.render_episode.execute(episode.episode_id)
    assert rendered.composed_now == ("show",) and generator.requests == []


def test_a_line_that_drops_or_changes_a_value_is_warned_but_not_blocked():
    container, _, episode = _kit()

    result = _revise(container, episode, ScriptEdit("L3", "가격은 육천 원."))

    assert len(result.warnings) == 1 and "price" in result.warnings[0]
    assert result.episode.plan.variables == episode.plan.variables  # 변수는 사람이 정한다
    assert result.episode.plan.overlay("closeup:price").text == "말차 라떼 5,000원"


def test_refused_edits_leave_the_episode_untouched():
    container, _, episode = _kit()
    series = series_of(container)
    too_long = "이 문장은 사 초짜리 장면에서 사람이 읽기에는 아무리 빨리 읽어도 너무 길어서 받을 수 없습니다"

    with pytest.raises(ValidationError, match="너무 깁니다"):
        _revise(container, episode, ScriptEdit("L2", too_long))
    with pytest.raises(NotFound):
        _revise(container, episode, ScriptEdit("L9", "없는 줄"))
    with pytest.raises(NotFound):
        series.revise_episode_script.execute(
            ReviseEpisodeScriptCommand("epi_none", (ScriptEdit("L1", "가"),))
        )
    assert series.episodes.get(episode.episode_id) == episode
    same = _revise(container, episode, ScriptEdit("L2", episode.plan.line("L2").text))
    assert same.changed_lines == () and same.episode == episode
