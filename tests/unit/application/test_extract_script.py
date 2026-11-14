"""ExtractScript: 영상·음성 파일 -> 대본 (ADR-0028). 소리 읽기와 음성 인식 포트만 쓴다."""

from __future__ import annotations

import pytest

from frame.adapters.fake_media import fake_recording, fake_video
from frame.adapters.series.audio import FakeAudioEditor
from frame.adapters.speech import FakeSpeechRecognizer
from frame.application.ports.speech import SpeechServiceError
from frame.application.use_cases.script import ExtractScript, ExtractScriptCommand
from frame.domain.errors import ValidationError
from frame.domain.script import LineKind, SceneSpan, SoundKind, SoundSpan
from frame.domain.speech import SpeechSegment, Transcript

SAID = Transcript(
    "ko",
    (
        SpeechSegment(400, 2600, "안녕하세요, 오늘의 메뉴예요.", confidence=0.93),
        SpeechSegment(4500, 6200, "바닐라 라떼입니다."),
        SpeechSegment(8600, 10400, "가격은 4,500원."),
    ),
)
SCENES = (
    SceneSpan("greeting", 0, 4000),
    SceneSpan("show", 4000, 8000),
    SceneSpan("closeup", 8000, 12000),
)


def _use_case(recognizer=None) -> tuple[ExtractScript, FakeSpeechRecognizer]:
    recognizer = recognizer or FakeSpeechRecognizer()
    return ExtractScript(FakeAudioEditor(), recognizer), recognizer


def test_script_is_extracted_from_the_sound_of_a_video():
    use_case, recognizer = _use_case()
    video = fake_video(duration_ms=12000, cuts_ms=(4000, 8000), transcript=SAID)
    command = ExtractScriptCommand(
        video, SCENES, language="ko", hint="바닐라 라떼", speaker_visible={"closeup": False}
    )

    result = use_case.execute(command)

    script = result.script
    assert [(line.line_id, line.scene_key, line.text) for line in script.lines] == [
        ("S1", "greeting", "안녕하세요, 오늘의 메뉴예요."),
        ("S2", "show", "바닐라 라떼입니다."),
        ("S3", "closeup", "가격은 4,500원."),
    ]
    assert script.lines[0].confidence == 0.93 and script.duration_ms == 12000
    assert [line.kind for line in script.lines] == [
        LineKind.UNKNOWN,
        LineKind.UNKNOWN,
        LineKind.NARRATION,
    ]
    assert result.transcript == SAID and result.has_sound
    assert recognizer.calls == [
        {"bytes": recognizer.calls[0]["bytes"], "language": "ko", "hint": "바닐라 라떼"}
    ]


def test_a_recording_works_the_same_way():
    use_case, _ = _use_case()

    script = use_case.execute(ExtractScriptCommand(fake_recording(SAID, 11000))).script

    assert [line.text for line in script.lines] == [s.text for s in SAID.segments]
    assert all(line.scene_keys == () for line in script.lines)


def test_media_without_sound_has_no_script_and_costs_nothing():
    use_case, recognizer = _use_case()

    result = use_case.execute(ExtractScriptCommand(fake_video(duration_ms=12000), SCENES))

    assert result.script.is_empty and not result.has_sound and result.transcript is None
    assert recognizer.calls == []  # 소리가 없으면 인식기를 부르지 않는다


def test_file_too_large_or_too_long_is_refused_before_recognition(monkeypatch):
    """받아 적기는 분 단위로 과금한다. 크기와 길이의 상한을 인식하기 전에 본다 (감사 BTS-D37)."""
    from frame.application.use_cases.script import extract_script as module

    use_case, recognizer = _use_case()
    hour = fake_recording(Transcript("ko", (SpeechSegment(0, 3_600_000, "아주 긴 말"),)))
    with pytest.raises(ValidationError, match="너무 깁니다"):
        use_case.execute(ExtractScriptCommand(hour))
    monkeypatch.setattr(module, "MAX_MEDIA_BYTES", 16)
    with pytest.raises(ValidationError, match="너무 큽니다"):
        use_case.execute(ExtractScriptCommand(fake_video(transcript=SAID)))
    assert recognizer.calls == []


def test_file_that_cannot_be_read_is_a_validation_error_not_an_empty_script():
    """읽을 수 없는 파일은 '소리 없는 파일' 이 아니다. 가짜 편집기도 실제처럼 오류를 내고, 그것은 입력의 문제다."""
    use_case, recognizer = _use_case()

    with pytest.raises(ValidationError, match="읽을 수 없습니다"):
        use_case.execute(ExtractScriptCommand(b"not media at all"))
    assert recognizer.calls == []


def test_sound_without_speech_is_an_empty_script_with_the_sound_kept_apart():
    """말은 없고 음악만 있는 영상: 줄은 없고, 말이 아닌 소리가 있었다는 것이 남는다."""
    use_case, _ = _use_case()
    video = fake_video(duration_ms=8000, transcript=Transcript("ko"), silences=((0, 1000),))

    script = use_case.execute(ExtractScriptCommand(video)).script

    assert script.is_empty
    assert script.sounds == (
        SoundSpan(0, 1000, SoundKind.SILENCE),
        SoundSpan(1000, 8000, SoundKind.OTHER),
    )


def test_recognition_must_be_on_and_failures_are_not_hidden():
    video = fake_video(transcript=SAID)
    with pytest.raises(ValidationError, match="FRAME_SPEECH_RECOGNIZER"):
        ExtractScript(FakeAudioEditor(), None).execute(ExtractScriptCommand(video))
    with pytest.raises(ValidationError):
        _use_case()[0].execute(ExtractScriptCommand(b""))
    failing, _ = _use_case(FakeSpeechRecognizer(error=SpeechServiceError("키 없음")))
    with pytest.raises(SpeechServiceError):
        failing.execute(ExtractScriptCommand(video))


# ---- 받아 적기 전에 소리를 본다 (whisper-1 로 실제 말소리를 받아 적어 드러난 것) ----
def test_sound_that_is_all_silence_is_not_sent_to_the_recognizer():
    """무음뿐인 트랙에서 인식기는 문장을 지어낸다 ('고맙습니다.'). 부르지 않으면 지어내지도, 과금되지도 않는다."""
    use_case, recognizer = _use_case()
    video = fake_video(duration_ms=12000, transcript=Transcript("ko"), silences=((0, 12000),))

    result = use_case.execute(ExtractScriptCommand(video, SCENES))

    assert recognizer.calls == []
    assert result.script.is_empty and result.has_sound and result.transcript is None
    assert result.script.sounds == (SoundSpan(0, 12000, SoundKind.SILENCE),)
    assert any("무음" in note for note in result.script.notes)


LONG_LINE = Transcript(
    "ko", (SpeechSegment(300, 7600, "손재주가 별로 없는 사람들은 자주 다칩니다."),)
)
LAST_WORDS = Transcript(
    "ko", (SpeechSegment(12900, 14200, "영수증 필요하신가요?", confidence=0.8),)
)
GAPS = ((0, 300), (7650, 12900), (14240, 16000))


def test_speech_the_recognizer_skipped_is_heard_again_from_that_stretch_alone():
    """whisper-1 은 5초를 쉰 뒤의 마지막 한마디를 통째로 빠뜨렸다. 줄이 없는 소리만 잘라 다시 받아 적는다."""
    use_case, recognizer = _use_case()
    video = fake_video(duration_ms=16000, transcript=LONG_LINE, silences=GAPS, missed=LAST_WORDS)

    result = use_case.execute(ExtractScriptCommand(video, language="ko", hint="영수증"))

    script = result.script
    assert [(line.start_ms, line.end_ms, line.text) for line in script.lines] == [
        (300, 7650, "손재주가 별로 없는 사람들은 자주 다칩니다."),
        (12900, 14240, "영수증 필요하신가요?"),
    ]
    assert script.lines[1].confidence == 0.8
    assert [call["hint"] for call in recognizer.calls] == [
        "영수증",
        "영수증",
    ]  # 전체 한 번 + 그 구간 한 번
    assert recognizer.calls[1]["bytes"] < recognizer.calls[0]["bytes"]
    assert any("다시 받아 적었습니다" in note for note in script.notes)
    assert not any("받아 적은 말이 없습니다" in note for note in script.notes)
    assert result.transcript is not None and len(result.transcript.segments) == 2


def test_what_is_heard_again_must_be_believable():
    """말이 없는 소리만 잘라 주면 인식기는 또 지어낸다 (신뢰도 0.45). 믿을 만하지 않으면 줄로 삼지 않는다."""
    use_case, recognizer = _use_case()
    made_up = Transcript("ko", (SpeechSegment(12900, 14200, "고맙습니다.", confidence=0.45),))
    video = fake_video(duration_ms=16000, transcript=LONG_LINE, silences=GAPS, missed=made_up)

    script = use_case.execute(ExtractScriptCommand(video)).script

    assert [line.text for line in script.lines] == ["손재주가 별로 없는 사람들은 자주 다칩니다."]
    assert len(recognizer.calls) == 2
    assert any(
        "12.9~14.2초에 소리가 있는데 받아 적은 말이 없습니다" in note for note in script.notes
    )


def test_sound_that_fills_the_whole_file_is_not_heard_twice():
    """음악이 내내 깔린 영상: 줄이 없는 소리가 파일 전체다. 같은 소리를 다시 받아 적지 않는다."""
    use_case, recognizer = _use_case()
    video = fake_video(duration_ms=8000, transcript=Transcript("ko"), silences=())

    script = use_case.execute(ExtractScriptCommand(video)).script

    assert script.is_empty and len(recognizer.calls) == 1
