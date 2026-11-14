"""뽑은 대본 (ADR-0028): 받아쓰기를 줄 단위로 다듬는 규칙 — 줄 나누기, 장면 배정, 신뢰도, 말이 아닌 소리."""

from __future__ import annotations

import pytest

from frame.domain.errors import NotFound, ValidationError
from frame.domain.script import (
    LineKind,
    SceneSpan,
    ScriptPolicy,
    SoundKind,
    SoundSpan,
    SpokenLine,
    SpokenScript,
    build_script,
    classify_kinds,
)
from frame.domain.speech import SpeechSegment, Transcript, Word

SCENES = (
    SceneSpan("greeting", 0, 4000),
    SceneSpan("show", 4000, 8000),
    SceneSpan("closeup", 8000, 12000),
)


def _words(*items: tuple[int, int, str]) -> tuple[Word, ...]:
    return tuple(Word(start, end, text) for start, end, text in items)


def _transcript() -> Transcript:
    return Transcript(
        "ko",
        (
            SpeechSegment(
                300,
                3600,
                "안녕하세요, 오늘의 메뉴예요.",
                _words((300, 1200, "안녕하세요"), (1300, 2200, "오늘의"), (2250, 3600, "메뉴예요")),
                confidence=0.9,
            ),
            SpeechSegment(
                4300,
                6800,
                "말차 라떼입니다.",
                _words((4300, 5200, "말차"), (5250, 6800, "라떼입니다")),
                confidence=0.8,
            ),
            SpeechSegment(8400, 10900, "가격은 5,000원.", confidence=0.7),
        ),
    )


def test_each_segment_becomes_a_line_with_time_scene_and_confidence():
    script = build_script(_transcript(), SCENES)

    assert [(line.line_id, line.start_ms, line.end_ms, line.text) for line in script.lines] == [
        ("S1", 300, 3600, "안녕하세요, 오늘의 메뉴예요."),
        ("S2", 4300, 6800, "말차 라떼입니다."),
        ("S3", 8400, 10900, "가격은 5,000원."),
    ]
    assert [line.scene_keys for line in script.lines] == [("greeting",), ("show",), ("closeup",)]
    assert [line.confidence for line in script.lines] == [0.9, 0.8, 0.7]
    assert script.language == "ko" and not script.is_empty
    assert script.in_scene("show") == (script.line("S2"),)
    with pytest.raises(NotFound):
        script.line("S9")


def test_a_long_pause_between_words_splits_the_line_and_keeps_the_punctuation():
    transcript = Transcript(
        "ko",
        (
            SpeechSegment(
                300,
                6800,
                "안녕하세요, 오늘의 메뉴예요. 말차 라떼입니다.",
                _words(
                    (300, 1200, "안녕하세요"),
                    (1300, 2200, "오늘의"),
                    (2250, 3600, "메뉴예요"),
                    (4300, 5200, "말차"),  # 0.7초 쉬었다
                    (5250, 6800, "라떼입니다"),
                ),
                confidence=0.9,
            ),
        ),
    )

    script = build_script(transcript, SCENES)

    assert [(line.start_ms, line.end_ms, line.text) for line in script.lines] == [
        (300, 3600, "안녕하세요, 오늘의 메뉴예요."),
        (4300, 6800, "말차 라떼입니다."),
    ]
    assert [w.text for w in script.lines[1].words] == ["말차", "라떼입니다"]
    assert [line.confidence for line in script.lines] == [0.9, 0.9]
    # 쉼이 기준보다 짧으면 나누지 않는다
    assert len(build_script(transcript, SCENES, policy=ScriptPolicy(split_pause_ms=800)).lines) == 1


def test_words_that_do_not_match_the_text_fall_back_to_the_words():
    transcript = Transcript(
        "ko",
        (
            SpeechSegment(
                0,
                5000,
                "가격은 5,000원입니다. 감사합니다.",
                _words(
                    (0, 900, "가격은"), (1000, 2500, "오천원입니다"), (3600, 5000, "감사합니다")
                ),
            ),
        ),
    )

    script = build_script(transcript, ())

    assert [line.text for line in script.lines] == ["가격은 오천원입니다", "감사합니다"]


def test_a_line_names_every_scene_it_really_spans():
    transcript = Transcript(
        "ko",
        (
            SpeechSegment(3000, 6000, "장면 둘에 걸친 말"),  # 1초 + 2초
            SpeechSegment(7900, 9500, "경계에 조금 걸친 말"),  # 0.1초 + 1.5초
            SpeechSegment(12500, 13000, "장면 밖의 말"),
        ),
    )

    script = build_script(transcript, SCENES)

    assert script.lines[0].scene_keys == ("show", "greeting")  # 많이 걸친 장면이 앞
    assert script.lines[0].scene_key == "show"
    assert script.lines[1].scene_keys == ("closeup",)  # 0.1초 걸친 장면은 세지 않는다
    assert script.lines[2].scene_keys == () and script.lines[2].scene_key is None


def test_no_speech_is_an_empty_script_not_an_error():
    for transcript in (None, Transcript("ko"), Transcript("ko", (SpeechSegment(0, 900, "  "),))):
        script = build_script(transcript, SCENES, duration_ms=12000, silences=((0, 12000),))

        assert script.is_empty and script.lines == () and script.text == ""
        assert script.sounds == (SoundSpan(0, 12000, SoundKind.SILENCE),)


def test_segments_the_recognizer_did_not_believe_are_not_lines():
    """음악·무음에서 지어낸 문장: 인식기가 '말이 아닐 수 있다' 고 한 데다 신뢰도도 낮다."""
    transcript = Transcript(
        "ko",
        (
            SpeechSegment(300, 3600, "안녕하세요.", confidence=0.9, no_speech=0.02),
            SpeechSegment(5000, 9000, "시청해 주셔서 감사합니다.", confidence=0.2, no_speech=0.9),
            SpeechSegment(9500, 11000, "작게 말한 줄.", confidence=0.3, no_speech=0.1),
        ),
    )

    script = build_script(transcript, SCENES)

    assert [line.text for line in script.lines] == ["안녕하세요.", "작게 말한 줄."]
    assert [line.line_id for line in script.lines] == ["S1", "S2"]
    assert any("시청해 주셔서 감사합니다." in note for note in script.notes)


def test_sound_that_is_neither_speech_nor_silence_is_kept_apart():
    transcript = Transcript(
        "ko", (SpeechSegment(600, 3900, "첫 줄"), SpeechSegment(6100, 9000, "둘째 줄"))
    )

    script = build_script(
        transcript, (), duration_ms=10000, silences=((0, 500), (4000, 6000), (9900, 10000))
    )

    assert script.sounds == (
        SoundSpan(0, 500, SoundKind.SILENCE),
        SoundSpan(4000, 6000, SoundKind.SILENCE),
        SoundSpan(
            9000, 9900, SoundKind.OTHER
        ),  # 말도 무음도 아닌 소리 (음악인지 소음인지는 가르지 못한다)
    )
    # 무음을 재지 않았으면 말이 아닌 소리를 지어내지 않는다
    assert build_script(transcript, (), duration_ms=10000).sounds == ()


def test_lines_without_a_visible_speaker_are_narration():
    script = build_script(_transcript(), SCENES)

    kinds = classify_kinds(script, {"greeting": True, "show": True, "closeup": False})

    assert [line.kind for line in kinds.lines] == [
        LineKind.UNKNOWN,  # 사람이 보이는 장면: 화면 속 말인지 목소리만인지 소리로는 알 수 없다
        LineKind.UNKNOWN,
        LineKind.NARRATION,  # 말할 사람이 화면에 없다
    ]
    # 이미 정해진 종류(사람이 고친 것)는 바꾸지 않는다
    fixed = kinds.with_line(kinds.line("S1").with_kind(LineKind.DIALOGUE))
    assert classify_kinds(fixed, {"greeting": False}).line("S1").kind is LineKind.DIALOGUE


def test_correcting_a_line_keeps_what_was_heard():
    script = build_script(_transcript(), SCENES)

    fixed = script.with_line(script.line("S3").with_text("가격은 5,500원."))

    line = fixed.line("S3")
    assert (line.text, line.heard, line.edited) == ("가격은 5,500원.", "가격은 5,000원.", True)
    assert fixed.line("S1") == script.line("S1") and not fixed.line("S1").edited
    again = fixed.with_line(line.with_text("가격은 5,000원."))  # 되돌리면 고친 줄이 아니다
    assert not again.line("S3").edited
    with pytest.raises(ValidationError):
        line.with_text("  ")


def test_script_round_trips_and_checks_its_lines():
    script = classify_kinds(
        build_script(_transcript(), SCENES, duration_ms=12000, silences=((0, 300),)),
        {"closeup": False},
    )

    assert SpokenScript.from_dict(script.to_dict()) == script
    script.validate()
    assert script.pace_cps == pytest.approx(script.chars / (script.speech_ms / 1000))
    overlapping = SpokenScript(
        "ko", (SpokenLine("S1", 0, 2000, "가"), SpokenLine("S2", 1500, 3000, "나"))
    )
    with pytest.raises(ValidationError):
        overlapping.validate()
    with pytest.raises(ValidationError):
        SpokenScript(
            "ko", (SpokenLine("S1", 0, 1000, "가"), SpokenLine("S1", 1000, 2000, "나"))
        ).validate()


# ---- 잰 무음으로 줄을 다듬는다 -----------------------------------------------------
# 아래의 시각은 오픈 데이터의 말소리를 16초 트랙에 놓고 whisper-1 로 받아 적은 실제 값이다
# (docs/research/theme-series.md §8.7): 인식기는 첫 줄을 0초로 당겨 적고, 멈춤을 낱말 안에 숨긴다.
def _four_lines() -> Transcript:
    return Transcript(
        "ko",
        (
            SpeechSegment(
                0,
                2540,
                "카드로 결제해도 되나요?",
                _words((0, 1480, "카드로"), (1480, 1980, "결제해도"), (1980, 2540, "되나요?")),
                confidence=0.75,
            ),
            SpeechSegment(5220, 6800, "영업부는 5층에 있습니다.", confidence=0.75),
            SpeechSegment(8800, 10520, "한 달에 300만 원을 법니다.", confidence=0.75),
            SpeechSegment(13060, 14160, "영수증 필요하신가요?", confidence=0.75),
        ),
    )


FOUR_SILENCES = ((0, 1167), (2497, 5088), (6597, 8490), (10473, 12902), (14241, 16000))


def test_a_line_starts_and_ends_where_the_sound_does():
    """말소리는 1.167초에 시작하는데 인식기는 0초라고 한다. 잰 무음이 있으면 소리가 나는 곳에 맞춘다."""
    script = build_script(_four_lines(), (), duration_ms=16000, silences=FOUR_SILENCES)

    assert [(line.start_ms, line.end_ms) for line in script.lines] == [
        (1167, 2497),  # 앞의 무음을 잘라 낸다 · 끝도 소리가 끝나는 곳으로
        (5088, 6597),  # 조금 늦게 잡은 시작은 소리가 시작하는 곳으로 당긴다
        (8490, 10473),
        (12902, 14241),
    ]
    assert [line.text for line in script.lines] == [s.text for s in _four_lines().segments]
    assert script.notes == ()
    assert [span.kind for span in script.sounds] == [SoundKind.SILENCE] * 5


def test_without_measured_silence_the_recognizer_times_are_kept():
    untouched = [(s.start_ms, s.end_ms) for s in _four_lines().segments]

    assert [(x.start_ms, x.end_ms) for x in build_script(_four_lines()).lines] == untouched
    # 배경음이 깔려 무음이 없으면 맞출 곳이 없다. 소리의 처음·끝으로 늘이지도 않는다
    noisy = build_script(_four_lines(), (), duration_ms=16000, silences=())
    assert [(x.start_ms, x.end_ms) for x in noisy.lines] == untouched


def _sentence_with_a_pause() -> Transcript:
    return Transcript(
        "ko",
        (
            SpeechSegment(
                0,
                7020,
                "아울러 미약하게나마 제가 할 수 있는 일을 찾아 실천하겠습니다.",
                _words(
                    (1280, 2340, "아울러"),
                    (2340, 3400, "미약하게나마"),
                    (3400, 4540, "제가"),  # 0.9초의 멈춤이 이 낱말 안에 들어 있다
                    (4540, 4960, "할"),
                    (4960, 5140, "수"),
                    (5140, 5400, "있는"),
                    (5400, 5780, "일을"),
                    (5780, 6000, "찾아"),
                    (6000, 6920, "실천하겠습니다."),
                ),
                confidence=0.73,
            ),
        ),
    )


def test_a_pause_hidden_inside_a_word_splits_the_line():
    """인식기의 낱말 시각은 멈춤을 다음 낱말에 붙인다 (낱말 사이가 0 이다). 잰 무음이 멈춤을 알려 준다."""
    silences = ((0, 1050), (1167, 1815), (3420, 4320), (7368, 16000))

    script = build_script(_sentence_with_a_pause(), (), duration_ms=16000, silences=silences)

    assert [(line.start_ms, line.end_ms, line.text) for line in script.lines] == [
        (1815, 3420, "아울러 미약하게나마"),
        (4320, 7368, "제가 할 수 있는 일을 찾아 실천하겠습니다."),
    ]
    assert [w.text for w in script.lines[0].words] == ["아울러", "미약하게나마"]
    # 1.05~1.167초의 딸깍 소리는 소리로 치지 않는다 (줄이 거기서 시작하지 않는다)


def test_a_short_pause_does_not_split_the_line():
    silences = ((0, 1815), (3420, 3900), (7368, 16000))  # 0.48초

    script = build_script(_sentence_with_a_pause(), (), duration_ms=16000, silences=silences)

    assert [(line.start_ms, line.end_ms) for line in script.lines] == [(1815, 7368)]


def test_a_sentence_made_up_over_silence_is_not_a_line():
    """소리가 전혀 없는 트랙에서 whisper-1 은 '고맙습니다.' 를 지어냈다 (신뢰도 0.45)."""
    transcript = Transcript(
        "ko", (SpeechSegment(13380, 14780, "고맙습니다.", confidence=0.452, no_speech=0.5),)
    )

    script = build_script(transcript, (), duration_ms=16000, silences=((0, 16000),))

    assert script.is_empty
    assert script.sounds == (SoundSpan(0, 16000, SoundKind.SILENCE),)
    assert any("고맙습니다." in note and "소리가 없는" in note for note in script.notes)


def test_a_segment_the_recognizer_is_sure_is_not_speech_is_dropped_whatever_its_confidence():
    transcript = Transcript(
        "ko", (SpeechSegment(13380, 14780, "고맙습니다.", confidence=0.452, no_speech=0.943),)
    )

    assert build_script(transcript).is_empty


def test_words_too_fast_to_be_speech_are_not_a_line():
    """잡음만 있는 트랙에서 whisper-1 은 0.14초 안에 열네 글자를 말했다고 했다."""
    transcript = Transcript(
        "ko",
        (
            SpeechSegment(7360, 7500, "오늘도 영상 봐주셔서 감사합니다.", confidence=0.456),
            SpeechSegment(9000, 10300, "카드로 결제해도 되나요?", confidence=0.75),
        ),
    )

    script = build_script(transcript)

    assert [line.text for line in script.lines] == ["카드로 결제해도 되나요?"]
    assert any("오늘도 영상 봐주셔서 감사합니다." in note for note in script.notes)


def test_sound_that_no_line_explains_is_noted():
    """whisper-1 은 5초를 쉰 뒤의 마지막 한마디를 통째로 빠뜨렸다. 소리는 있는데 줄이 없으면 알린다."""
    transcript = Transcript(
        "ko",
        (
            SpeechSegment(
                0, 8000, "손재주가 별로 없는 사람들은 처음에 자주 상처를 입었을 것입니다."
            ),
        ),
    )
    silences = ((0, 1500), (7653, 12901), (14241, 16000))

    script = build_script(transcript, (), duration_ms=16000, silences=silences)

    assert [(line.start_ms, line.end_ms) for line in script.lines] == [(1500, 7653)]
    assert SoundSpan(12901, 14241, SoundKind.OTHER) in script.sounds
    assert any("12.9~14.2초" in note for note in script.notes)


def test_two_lines_in_one_stretch_of_sound_keep_the_boundary_between_them():
    transcript = Transcript(
        "ko",
        (
            SpeechSegment(1000, 3000, "첫 문장입니다."),
            SpeechSegment(3000, 5200, "둘째 문장입니다."),
        ),
    )

    script = build_script(transcript, (), duration_ms=8000, silences=((0, 900), (5300, 8000)))

    assert [(line.start_ms, line.end_ms) for line in script.lines] == [(900, 3000), (3000, 5300)]
