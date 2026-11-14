"""녹음 붙이기 규칙: 녹음을 대본 줄에 맞추고 장면에 놓는다 (ADR-0026)."""

from __future__ import annotations

import pytest

from frame.domain.errors import ValidationError
from frame.domain.series.episode import ScriptLine
from frame.domain.series.narration import (
    LineAlignment,
    LineStatus,
    NarrationPolicy,
    Placement,
    PlacementStatus,
    align_script,
    check_line,
    fit_alignment,
    missing_key_terms,
    normalize_speech,
    place_lines,
    similarity,
    speech_units,
)
from frame.domain.speech import SpeechSegment, Transcript, Word

SCRIPT = (
    ScriptLine("L1", "greeting", "안녕하세요, 오늘의 메뉴예요.", 3600),
    ScriptLine("L2", "show", "말차 라떼입니다.", 3600),
    ScriptLine("L3", "closeup", "가격은 오천 원.", 3600),
    ScriptLine("L4", "outro", "매장에서 만나요!", 3600),
)
SPANS = {
    "greeting": (0, 4000),
    "show": (4000, 8000),
    "closeup": (8000, 12000),
    "outro": (12000, 16000),
}
KEY_TERMS = ("말차 라떼", "5,000원")


def _segments(*items: tuple[int, int, str]) -> Transcript:
    return Transcript("ko", tuple(SpeechSegment(a, b, text) for a, b, text in items))


def _words(*items: tuple[int, int, str]) -> Transcript:
    """구간 하나에 낱말 시각이 붙은 인식 결과 (쉬지 않고 이어 읽은 녹음)."""
    words = tuple(Word(a, b, text) for a, b, text in items)
    return Transcript(
        "ko",
        (
            SpeechSegment(
                words[0].start_ms, words[-1].end_ms, " ".join(w.text for w in words), words
            ),
        ),
    )


# ---- 글자 견주기 ----
def test_speech_is_compared_without_spacing_punctuation_or_number_style():
    assert normalize_speech("가격은 5,000원입니다!") == "가격은오천원입니다"
    assert similarity("가격은 오천 원.", "가격은 5,000원") == 1.0
    assert similarity("Hello, FRAME", "hello frame") == 1.0
    assert similarity("안녕하세요", "") == 0.0
    assert 0.8 < similarity("가격은 오천 원", "가격은 육천 원") < 0.9  # 한 글자만 달라도 높다


def test_key_terms_catch_what_similarity_misses():
    assert missing_key_terms("가격은 오천 원.", "가격은 육천 원", KEY_TERMS) == ("5,000원",)
    assert missing_key_terms("가격은 오천 원.", "가격은 5,000원", KEY_TERMS) == ()
    assert (
        missing_key_terms("매장에서 만나요!", "매장에서 만나요", KEY_TERMS) == ()
    )  # 줄에 없는 핵심어는 안 본다
    assert missing_key_terms("말차 라떼입니다.", "말자 라떼입니다", KEY_TERMS) == ("말차 라떼",)


# ---- 맞추기 ----
def test_each_line_gets_its_segment():
    transcript = _segments(
        (300, 2400, "안녕하세요 오늘의 메뉴예요"),
        (3000, 4300, "말차 라떼입니다"),
        (5000, 6400, "가격은 5,000원"),
        (7000, 8300, "매장에서 만나요"),
    )
    result = align_script(SCRIPT, transcript, key_terms=KEY_TERMS)

    assert [a.status for a in result] == [LineStatus.MATCHED] * 4
    assert [(a.start_ms, a.end_ms) for a in result] == [
        (300, 2400),
        (3000, 4300),
        (5000, 6400),
        (7000, 8300),
    ]
    assert all(a.similarity == 1.0 for a in result)
    assert result[2].heard == "가격은 5,000원"


def test_word_timestamps_split_a_recording_read_without_pauses():
    transcript = _words(
        (200, 900, "안녕하세요,"),
        (900, 1400, "오늘의"),
        (1400, 2100, "메뉴예요."),
        (2100, 2600, "말차"),
        (2600, 3400, "라떼입니다."),
        (3400, 3900, "가격은"),
        (3900, 4300, "오천"),
        (4300, 4600, "원."),
        (4600, 5200, "매장에서"),
        (5200, 5900, "만나요!"),
    )
    result = align_script(SCRIPT, transcript, key_terms=KEY_TERMS)

    assert [a.status for a in result] == [LineStatus.MATCHED] * 4
    assert [(a.start_ms, a.end_ms) for a in result] == [
        (200, 2100),
        (2100, 3400),
        (3400, 4600),
        (4600, 5900),
    ]
    assert len(speech_units(transcript)) == 10


def test_false_starts_and_filler_are_skipped():
    """앞에서 읽다 만 부분과 군말은 건너뛰고, 제대로 읽은 쪽을 고른다."""
    transcript = _segments(
        (100, 900, "안녕하세요 오늘"),
        (1000, 1500, "아 다시 할게요"),
        (2000, 4100, "안녕하세요 오늘의 메뉴예요"),
        (4800, 6100, "말차 라떼입니다"),
        (6500, 6900, "음"),
        (7000, 8400, "가격은 오천 원"),
        (9000, 10300, "매장에서 만나요"),
    )
    result = align_script(SCRIPT, transcript, key_terms=KEY_TERMS)

    assert [a.status for a in result] == [LineStatus.MATCHED] * 4
    assert (result[0].start_ms, result[0].end_ms) == (2000, 4100)
    assert (result[2].start_ms, result[2].end_ms) == (7000, 8400)


def test_unread_line_is_missing_and_the_rest_still_match():
    transcript = _segments(
        (300, 2400, "안녕하세요 오늘의 메뉴예요"),
        (3000, 4300, "말차 라떼입니다"),
        (5000, 6300, "매장에서 만나요"),
    )
    result = align_script(SCRIPT, transcript)

    assert [a.status for a in result] == [
        LineStatus.MATCHED,
        LineStatus.MATCHED,
        LineStatus.MISSING,
        LineStatus.MATCHED,
    ]
    assert result[2].start_ms is None and result[2].heard == ""
    assert (result[3].start_ms, result[3].end_ms) == (5000, 6300)


def test_wrong_fact_is_a_mismatch_even_when_the_wording_is_close():
    transcript = _segments(
        (300, 2400, "안녕하세요 오늘의 메뉴예요"),
        (3000, 4300, "말차 라떼입니다"),
        (5000, 6400, "가격은 육천 원"),
        (7000, 8300, "매장에서 만나요"),
    )
    with_terms = align_script(SCRIPT, transcript, key_terms=KEY_TERMS)
    without_terms = align_script(SCRIPT, transcript)

    assert with_terms[2].status is LineStatus.MISMATCH
    assert with_terms[2].missing_terms == ("5,000원",) and with_terms[2].similarity > 0.8
    assert without_terms[2].status is LineStatus.MATCHED  # 유사도만으로는 놓친다
    assert [a.status for a in with_terms[:2]] == [LineStatus.MATCHED] * 2


def test_different_wording_is_a_mismatch():
    transcript = _segments((300, 2400, "여러분 반갑습니다 좋은 아침이에요"))
    result = align_script(SCRIPT[:1], transcript)

    assert result[0].status is LineStatus.MISMATCH and result[0].similarity < 0.6
    assert result[0].heard == "여러분 반갑습니다 좋은 아침이에요"


def test_silent_recording_and_empty_script():
    assert [a.status for a in align_script(SCRIPT, Transcript())] == [LineStatus.MISSING] * 4
    assert align_script((), _segments((0, 1000, "안녕하세요"))) == ()


def test_line_recorded_on_its_own_is_checked_as_a_whole():
    good = check_line(
        SCRIPT[2], _segments((120, 1500, "가격은 5,000원")), source_index=2, key_terms=KEY_TERMS
    )
    bad = check_line(
        SCRIPT[2], _segments((120, 1500, "가격은 육천 원")), source_index=2, key_terms=KEY_TERMS
    )
    silent = check_line(SCRIPT[2], Transcript(), source_index=2)

    assert (good.status, good.source_index, good.start_ms, good.end_ms) == (
        LineStatus.MATCHED,
        2,
        120,
        1500,
    )
    assert bad.status is LineStatus.MISMATCH and bad.missing_terms == ("5,000원",)
    assert silent.status is LineStatus.MISSING and silent.source_index == 2


# ---- 놓기 ----
def _aligned(*spans: tuple[int, int] | None) -> tuple[LineAlignment, ...]:
    return tuple(
        LineAlignment(line.line_key, LineStatus.MISSING)
        if span is None
        else LineAlignment(line.line_key, LineStatus.MATCHED, 1.0, line.text, span[0], span[1])
        for line, span in zip(SCRIPT, spans, strict=False)
    )


def test_lines_that_fit_are_placed_at_their_scene_start():
    alignments = _aligned((300, 2400), (3000, 4300), (5000, 6400), (7000, 8300))
    placements = place_lines(SCRIPT, alignments, SPANS, [9000])

    assert [p.status for p in placements] == [PlacementStatus.PLACED] * 4
    assert [p.target_start_ms for p in placements] == [200, 4200, 8200, 12200]
    assert all(p.tempo == 1.0 and p.overflow_ms == 0 for p in placements)
    first = placements[0]
    assert (first.source_start_ms, first.source_end_ms) == (220, 2480)  # 앞뒤 80 ms 여유
    assert first.duration_ms == 2260 and first.target_end_ms == 2460


def test_cut_does_not_reach_into_the_neighbouring_line():
    """줄 사이가 붙어 있으면 여유는 두 줄의 가운데까지만 둔다."""
    alignments = _aligned((200, 2100), (2100, 3400), (3400, 4600), (4600, 5900))
    placements = place_lines(SCRIPT, alignments, SPANS, [6000])

    assert [(p.source_start_ms, p.source_end_ms) for p in placements] == [
        (120, 2100),
        (2100, 3400),
        (3400, 4600),
        (4600, 5980),
    ]


def test_slightly_long_line_is_sped_up_to_fit():
    alignments = _aligned((0, 4000))  # 4초를 3.6초 예산에 넣는다 (여유 포함 4.08초)
    placement = place_lines(SCRIPT[:1], alignments, SPANS, [4080])[0]

    assert placement.status is PlacementStatus.SPED_UP
    assert placement.tempo == pytest.approx(4080 / 3600)
    assert placement.duration_ms == 3600 and placement.overflow_ms == 0


def test_much_longer_line_overflows_and_reports_by_how_much():
    alignments = _aligned((0, 6000))
    placement = place_lines(SCRIPT[:1], alignments, SPANS, [6000])[0]

    assert placement.status is PlacementStatus.OVERFLOW and placement.tempo == 1.15
    assert placement.duration_ms == round(6000 / 1.15)
    assert placement.overflow_ms == placement.duration_ms - 3600


def test_missing_line_has_no_audio_to_place():
    alignments = _aligned((300, 2400), None, (5000, 6400), (7000, 8300))
    placements = place_lines(SCRIPT, alignments, SPANS, [9000])

    assert placements[1].status is PlacementStatus.MISSING and placements[1].duration_ms == 0
    assert placements[2].status is PlacementStatus.PLACED
    # 맞춘 결과가 아예 없는 줄도 놓을 것이 없다
    assert place_lines(SCRIPT, alignments[:1], SPANS, [9000])[3].status is PlacementStatus.MISSING


def test_lines_recorded_separately_are_cut_from_their_own_files():
    alignments = tuple(
        LineAlignment(line.line_key, LineStatus.MATCHED, 1.0, line.text, 100, 1500, source_index=i)
        for i, line in enumerate(SCRIPT)
    )
    placements = place_lines(SCRIPT, alignments, SPANS, [1600, 1600, 1550, 1600])

    assert [p.source_index for p in placements] == [0, 1, 2, 3]
    assert [(p.source_start_ms, p.source_end_ms) for p in placements] == [
        (20, 1580),
        (20, 1580),
        (20, 1550),  # 파일 끝을 넘지 않는다
        (20, 1580),
    ]
    with pytest.raises(ValidationError, match="녹음 파일"):
        place_lines(SCRIPT, alignments, SPANS, [1600])


def test_unknown_scene_and_bad_policy_are_rejected():
    with pytest.raises(ValidationError, match="장면"):
        place_lines(SCRIPT, _aligned((0, 1000)), {"greeting": (0, 4000)}, [1000])
    with pytest.raises(ValidationError):
        NarrationPolicy(max_tempo=0.9)
    with pytest.raises(ValidationError):
        NarrationPolicy(min_similarity=1.5)


def test_results_round_trip():
    alignment = LineAlignment(
        "L3", LineStatus.MISMATCH, 0.8333, "가격은 육천 원", 5000, 6400, 0, ("5,000원",)
    )
    placement = Placement(
        "L3", "closeup", PlacementStatus.SPED_UP, 0, 4920, 6480, 8200, 1.1, 1418, 0
    )

    assert LineAlignment.from_dict(alignment.to_dict()) == alignment
    assert Placement.from_dict(placement.to_dict()) == placement


def test_an_alignment_is_trimmed_to_where_the_recording_sounds():
    """인식기는 녹음 앞의 무음을 첫 낱말에 붙인다 (말은 1.16초에 시작하는데 0초라고 한다).

    그대로 자르면 무음까지 장면에 놓여 말이 늦게 시작하고, 줄이 실제보다 길어 보인다 (빠르기를 올리거나 넘친다고 한다).
    """
    heard = LineAlignment("L3", LineStatus.MATCHED, 1.0, "이 집은 월세가 50만 원이다", 0, 3260, 2)

    fitted = fit_alignment(heard, ((1156, 3333),))

    assert (fitted.start_ms, fitted.end_ms) == (1156, 3260)
    assert (fitted.status, fitted.heard, fitted.source_index) == (
        LineStatus.MATCHED,
        heard.heard,
        2,
    )
    # 소리가 나는 곳과 겹치지 않으면(잴 수 없으면) 그대로 둔다. 찾지 못한 줄도 그대로다
    assert fit_alignment(heard, ()) == heard
    assert fit_alignment(heard, ((5000, 6000),)) == heard
    missing = LineAlignment("L1", LineStatus.MISSING)
    assert fit_alignment(missing, ((0, 1000),)) == missing
