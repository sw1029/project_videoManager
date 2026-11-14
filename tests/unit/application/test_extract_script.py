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
