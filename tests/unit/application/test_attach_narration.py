"""AttachNarration: 사용자가 녹음한 음성을 대본 줄에 맞춰 편에 붙인다 (ADR-0026). 음성은 합성하지 않는다."""

from __future__ import annotations

import hashlib

import pytest

from frame.adapters.shared.media.fake_media import fake_recording, fake_video
from frame.adapters.speech import FakeSpeechRecognizer
from frame.application.use_cases.series import AttachNarrationCommand, RecordingInput
from frame.config.theme_settings import ThemeSettings
from frame.domain.errors import NotFound, ValidationError
from frame.domain.series.narration import LineStatus, PlacementStatus
from frame.domain.speech import SpeechSegment, Transcript
from tests.support.series import (
    GOOD_READING,
    new_episode,
    save_theme,
    series_container,
    series_of,
)
from tests.support.themes import matcha_content

WRONG_PRICE = Transcript(
    "ko",
    (
        SpeechSegment(300, 2400, "안녕하세요 오늘의 메뉴예요"),
        SpeechSegment(3000, 4300, "말차 라떼입니다"),
        SpeechSegment(5000, 6400, "가격은 육천 원"),
        SpeechSegment(7000, 8300, "매장에서 만나요"),
    ),
)


@pytest.fixture
def container():
    container = series_container()
    save_theme(container)
    return container


def _whole(transcript: Transcript = GOOD_READING, filename: str = "take1.m4a") -> RecordingInput:
    return RecordingInput(fake_recording(transcript), filename=filename)


def _line(line_key: str, text: str, start: int = 150, end: int = 1500) -> RecordingInput:
    recording = fake_recording(Transcript("ko", (SpeechSegment(start, end, text),)))
    return RecordingInput(recording, line_key=line_key, filename=f"{line_key}.wav")


def _attach(container, episode, *recordings: RecordingInput, language: str | None = "ko"):
    command = AttachNarrationCommand(episode.episode_id, tuple(recordings), language)
    return series_of(container).attach_narration.execute(command)


def _heard_on_track(container, episode) -> list[tuple[int, int, str]]:
    """붙인 트랙을 가짜 인식기로 다시 읽은 것: (시작, 끝, 말)."""
    series = series_of(container)
    track = container.artifact_store.get(episode.narration.track_uri)
    heard = FakeSpeechRecognizer().transcribe(series.audio.speech_audio(track))
    return [(s.start_ms, s.end_ms, s.text) for s in heard.segments]


def test_one_take_is_aligned_to_the_script_and_placed_in_each_scene(container):
    episode = _attach(container, new_episode(container), _whole())
    narration = episode.narration

    assert narration.ok and narration.problem_lines == ()
    assert [a.status for a in narration.alignments] == [LineStatus.MATCHED] * 4
    assert [p.status for p in narration.placements] == [PlacementStatus.PLACED] * 4
    assert [p.target_start_ms for p in narration.placements] == [200, 4200, 8200, 12200]
    # 트랙에서 줄마다의 말이 그 장면 안에 놓여 있다 (앞뒤 80 ms 여유를 두고 잘라 0.2초 뒤에 놓는다)
    assert _heard_on_track(container, episode) == [
        (280, 2380, "안녕하세요 오늘의 메뉴예요"),
        (4280, 5580, "말차 라떼입니다"),
        (8280, 9680, "가격은 5,000원"),
        (12280, 13580, "매장에서 만나요"),
    ]
    digest = hashlib.sha256(_whole().data).hexdigest()[:16]
    assert narration.recording_uris == (
        f"mem://episodes/{episode.episode_id}/recordings/{digest}.m4a",
    )
    # 트랙의 키에는 내용의 해시가 든다: 편 문서가 가리키는 트랙의 내용은 바뀌지 않는다 (감사 BTS-D08·D39)
    prefix = f"mem://episodes/{episode.episode_id}/narration-"
    assert narration.track_uri.startswith(prefix) and narration.track_uri.endswith(".json")
    assert series_of(container).episodes.get(episode.episode_id) == episode


def test_recognizer_gets_the_language_and_the_key_terms_as_a_hint():
    recognizer = FakeSpeechRecognizer()
    container = series_container(speech_recognizer=recognizer)
    save_theme(container)
    _attach(container, new_episode(container), _whole())

    assert recognizer.calls[0]["language"] == "ko"
    assert recognizer.calls[0]["hint"] == "말차 라떼, 5,000원"


def test_wrong_fact_is_attached_but_flagged(container):
    """가격을 틀리게 읽은 녹음: 붙이기는 하되 그 줄을 다시 볼 줄로 남긴다."""
    episode = _attach(container, new_episode(container), _whole(WRONG_PRICE))
    narration = episode.narration

    assert not narration.ok and narration.problem_lines == ("L3",)
    wrong = narration.alignments[2]
    assert wrong.status is LineStatus.MISMATCH and wrong.missing_terms == ("5,000원",)
    assert narration.placements[2].status is PlacementStatus.PLACED  # 소리는 놓여 있다


def test_only_the_wrong_line_is_replaced_by_a_new_recording(container):
    """국소 수정: 틀린 줄만 다시 녹음해 주면 그 줄만 갈아 끼운다. 나머지 줄은 처음 녹음 그대로다."""
    first = _attach(container, new_episode(container), _whole(WRONG_PRICE))
    fixed = _attach(container, first, _line("L3", "가격은 오천 원", 100, 1400))
    narration = fixed.narration

    assert narration.ok
    assert [a.source_index for a in narration.alignments] == [0, 0, 1, 0]
    assert (
        len(narration.recording_uris) == 2
        and narration.recording_uris[0] == first.narration.recording_uris[0]
    )
    untouched = [0, 1, 3]
    assert [narration.placements[i] for i in untouched] == [
        first.narration.placements[i] for i in untouched
    ]
    heard = _heard_on_track(container, fixed)
    assert heard[2] == (8280, 9580, "가격은 오천 원")
    assert [h for i, h in enumerate(heard) if i != 2] == [
        h for i, h in enumerate(_heard_on_track(container, first)) if i != 2
    ]


def test_new_whole_take_replaces_everything(container):
    first = _attach(container, new_episode(container), _whole(WRONG_PRICE))
    again = _attach(container, first, _whole(GOOD_READING, "take2.m4a"))

    assert again.narration.ok and len(again.narration.recording_uris) == 1
    assert again.narration.recording_uris[0].endswith(".m4a")


def test_lines_recorded_one_by_one(container):
    episode = _attach(
        container,
        new_episode(container),
        _line("L1", "안녕하세요 오늘의 메뉴예요", 100, 2200),
        _line("L4", "매장에서 만나요"),
    )
    narration = episode.narration

    assert [a.status for a in narration.alignments] == [
        LineStatus.MATCHED,
        LineStatus.MISSING,
        LineStatus.MISSING,
        LineStatus.MATCHED,
    ]
    assert narration.problem_lines == ("L2", "L3")
    assert [p.source_index for p in narration.placements if p.status is PlacementStatus.PLACED] == [
        0,
        1,
    ]
    assert [text for _, _, text in _heard_on_track(container, episode)] == [
        "안녕하세요 오늘의 메뉴예요",
        "매장에서 만나요",
    ]


def test_silence_the_recognizer_put_inside_a_line_is_not_placed_with_it(container):
    """인식기는 녹음 앞의 무음을 말에 붙여 준다 (0초부터라고 한다). 잰 무음으로 잘라, 말이 제때 시작하게 놓는다."""
    heard = Transcript("ko", (SpeechSegment(0, 2500, "말차 라떼입니다"),))  # 말은 1.1초에 시작한다
    recording = fake_recording(heard, 2900, silences=((0, 1100), (2500, 2900)))

    episode = _attach(
        container,
        new_episode(container),
        RecordingInput(recording, line_key="L2", filename="L2.wav"),
    )

    alignment = next(a for a in episode.narration.alignments if a.line_key == "L2")
    placement = next(p for p in episode.narration.placements if p.line_key == "L2")
    assert (alignment.start_ms, alignment.end_ms) == (1100, 2500)
    assert (placement.source_start_ms, placement.source_end_ms) == (1020, 2580)  # 앞뒤 80 ms 여유
    assert placement.status is PlacementStatus.PLACED and placement.duration_ms == 1560


def test_line_longer_than_its_scene_is_reported(container):
    long_take = Transcript("ko", (SpeechSegment(0, 6000, "안녕하세요 오늘의 메뉴예요"),))
    episode = _attach(container, new_episode(container), _whole(long_take))
    first = episode.narration.placements[0]

    assert first.status is PlacementStatus.OVERFLOW and first.overflow_ms > 0
    assert "L1" in episode.narration.problem_lines


def test_attaching_narration_makes_the_final_video_stale(container):
    from tests.support.series import generate_episode

    episode = generate_episode(container, new_episode(container))
    rendered = series_of(container).render_episode.execute(episode.episode_id).episode
    assert rendered.render is not None

    assert _attach(container, rendered, _whole()).render is None


@pytest.mark.parametrize(
    ("recordings", "message"),
    [
        ((), "하나 이상"),
        ((_whole(), _whole()), "하나만"),
        ((_whole(), _line("L1", "안녕하세요")), "함께"),
        ((_line("L1", "안녕하세요"), _line("L1", "안녕하세요")), "두 번"),
        ((_line("L9", "없는 줄"),), "대본에 없는"),
        ((RecordingInput(b""),), "비어"),
        ((RecordingInput(fake_video()),), "소리가 없습니다"),
    ],
)
def test_bad_recordings_are_rejected(container, recordings, message):
    episode = new_episode(container)

    with pytest.raises(ValidationError, match=message):
        _attach(container, episode, *recordings)
    assert series_of(container).episodes.get(episode.episode_id).narration is None


def test_episode_without_a_script_or_recognizer_cannot_take_narration(container):
    silent_slots = tuple(
        type(s)(s.slot_key, s.description, s.entity_keys, s.requirements, "", s.variables)
        for s in matcha_content().slots
    )
    silent = new_episode(
        container, content=type(matcha_content())(silent_slots, matcha_content().entities)
    )
    with pytest.raises(ValidationError, match="대본이 없습니다"):
        _attach(container, silent, _whole())
    with pytest.raises(NotFound):
        series_of(container).attach_narration.execute(
            AttachNarrationCommand("epi_none", (_whole(),))
        )

    off = series_container(theme_settings=ThemeSettings(speech="off"))
    save_theme(off)
    with pytest.raises(ValidationError, match="음성 인식"):
        _attach(off, new_episode(off), _whole())


# ---- 과금하기 전에 모든 입력을 확인한다 (감사 BTS-12·30) ----
def test_every_recording_is_checked_before_any_is_recognised():
    """셋째 파일이 비어 있으면 앞의 둘도 인식하지 않는다 (앞의 둘을 과금한 뒤에 거절하지 않는다)."""
    recognizer = FakeSpeechRecognizer()
    container = series_container(speech_recognizer=recognizer)
    save_theme(container)
    episode = new_episode(container)
    recordings = (
        _line("L1", "안녕하세요 오늘의 메뉴예요"),
        _line("L2", "말차 라떼입니다"),
        RecordingInput(b"", line_key="L3", filename="L3.wav"),
    )

    with pytest.raises(ValidationError, match="비어"):
        _attach(container, episode, *recordings)
    assert recognizer.calls == []
    assert not any("recordings" in uri for uri in container.artifact_store._blobs)


def test_recording_far_longer_than_what_it_covers_is_refused_before_recognition():
    """인식은 분 단위로 과금한다. 장면에 넣을 수 없을 만큼 긴 녹음은 인식하기 전에 거절한다."""
    recognizer = FakeSpeechRecognizer()
    container = series_container(speech_recognizer=recognizer)
    save_theme(container)
    episode = new_episode(container)
    hour = fake_recording(Transcript("ko", (SpeechSegment(0, 3_600_000, "아주 긴 녹음"),)))

    with pytest.raises(ValidationError, match="너무 깁니다"):
        _attach(container, episode, RecordingInput(hour, filename="take.m4a"))
    with pytest.raises(ValidationError, match="너무 깁니다"):
        _attach(container, episode, RecordingInput(hour, line_key="L1", filename="L1.wav"))
    assert recognizer.calls == []


def test_recording_that_is_silent_from_start_to_end_is_refused_before_recognition():
    """무음 녹음을 인식기에 보내면 과금되고, 인식기가 지어낸 문장이 줄 맞추기에 들어간다 (감사 BTS-D67)."""
    recognizer = FakeSpeechRecognizer()
    container = series_container(speech_recognizer=recognizer)
    save_theme(container)
    episode = new_episode(container)
    made_up = Transcript("ko", (SpeechSegment(300, 2400, "시청해 주셔서 감사합니다"),))
    silent = fake_recording(made_up, 3000, silences=((0, 3000),))

    with pytest.raises(ValidationError, match="무음"):
        _attach(container, episode, RecordingInput(silent, filename="take.wav"))
    with pytest.raises(ValidationError, match="무음"):
        _attach(container, episode, RecordingInput(silent, line_key="L1", filename="L1.wav"))
    assert recognizer.calls == []


def test_recording_too_large_is_refused_before_anything_reads_it(monkeypatch):
    from frame.application.use_cases.series import attach_narration as module

    recognizer = FakeSpeechRecognizer()
    container = series_container(speech_recognizer=recognizer)
    save_theme(container)
    episode = new_episode(container)
    monkeypatch.setattr(module, "MAX_RECORDING_BYTES", 16)

    with pytest.raises(ValidationError, match="너무 큽니다"):
        _attach(container, episode, _whole())
    assert recognizer.calls == []


def test_recording_that_cannot_be_read_is_a_validation_error_with_real_and_fake_editors():
    """읽을 수 없는 녹음에 실제 편집기는 AudioError 를 던지고 가짜는 None 을 돌려준다. 둘 다 같은 안내가 된다."""
    from frame.adapters.series.audio import FakeAudioEditor
    from frame.application.ports.series.audio import AudioError

    class _Strict(FakeAudioEditor):  # 실제 ffmpeg 편집기처럼 군다
        def speech_audio(self, media, **span):
            if super().speech_audio(media, **span) is None:
                raise AudioError("ffmpeg: Invalid data found when processing input")
            return super().speech_audio(media, **span)

    for editor in (FakeAudioEditor(), _Strict()):
        container = series_container(audio_editor=editor)
        save_theme(container)
        episode = new_episode(container)
        with pytest.raises(ValidationError, match="소리가 없습니다"):
            _attach(container, episode, RecordingInput(b"not audio at all", filename="x.wav"))


def test_track_an_episode_points_to_is_not_overwritten_by_a_failed_attach(container):
    """트랙을 쓴 뒤 편 문서 저장이 실패해도, 문서가 가리키는 트랙의 내용은 그대로다 (감사 BTS-D08·D39).

    트랙이 고정 키면 문서는 옛 맞춤 기록을 갖고 같은 주소의 트랙만 새 내용이 된다 (다음 완성본에 다른 소리가 붙는다).
    """
    series = series_of(container)
    episode = _attach(container, new_episode(container), _whole())
    before = container.artifact_store.get(episode.narration.track_uri)
    original_save = series.episodes.save

    def failing(_episode):
        raise RuntimeError("저장 실패")

    series.episodes.save = failing
    try:
        with pytest.raises(RuntimeError):
            _attach(container, episode, _line("L3", "가격은 육천 원"))
    finally:
        series.episodes.save = original_save

    kept = series.episodes.get(episode.episode_id)
    assert kept.narration == episode.narration
    assert container.artifact_store.get(kept.narration.track_uri) == before


# ---- 녹음 파일은 내용으로 이름을 짓는다 (감사 BTS-23) ----
def test_failed_attach_does_not_change_the_recordings_an_episode_points_to(container):
    """트랙을 만들다 실패해도 편이 가리키는 녹음 파일의 내용은 바뀌지 않는다.

    번호로 이름을 지으면 실패한 붙이기가 예전 파일을 새 녹음으로 덮어써, 이어서 한 줄만 다시 녹음할 때
    예전 녹음에서 잰 시각이 새 파일에 적용된다 (오류 없이 어긋난다).
    """
    from frame.application.ports.series.audio import AudioError

    series = series_of(container)
    episode = new_episode(container)
    first = _attach(container, episode, _whole())
    uri = first.narration.recording_uris[0]
    before = container.artifact_store.get(uri)

    series.audio._error = AudioError("트랙을 만들지 못함")
    other = fake_recording(
        Transcript("ko", (SpeechSegment(100, 900, "전혀 다른 녹음"),)),
    )
    with pytest.raises(AudioError):
        _attach(container, episode, RecordingInput(other, filename="take2.m4a"))
    series.audio._error = None

    assert container.artifact_store.get(uri) == before  # 편이 가리키는 파일은 그대로다
    assert series.episodes.get(episode.episode_id).narration == first.narration
    fixed = _attach(container, episode, _line("L3", "가격은 오천 원"))
    assert fixed.narration.ok and fixed.narration.recording_uris[0] == uri


def test_odd_file_names_never_reach_the_storage_key(container):
    episode = new_episode(container)
    odd = RecordingInput(_whole().data, filename="..\\..\\take.m4a\n")

    narration = _attach(container, episode, odd).narration

    assert narration.recording_uris[0].endswith(".bin") and ".." not in narration.recording_uris[0]


def test_returned_narration_is_exactly_what_was_stored(container):
    """돌려준 값과 저장된 값이 같다 (유사도를 저장할 때 반올림해 서로 달랐다, 감사 BTS-21)."""
    episode = new_episode(container)
    wobbly = fake_recording(
        Transcript(
            "ko", (SpeechSegment(300, 2400, "안녕하세요 오늘 메뉴예요"),)
        )  # 대본과 조금 다르다
    )

    attached = _attach(container, episode, RecordingInput(wobbly, filename="take.m4a"))

    stored = series_of(container).episodes.get(episode.episode_id)
    assert stored.narration == attached.narration
    assert 0 < attached.narration.alignments[0].similarity < 1
