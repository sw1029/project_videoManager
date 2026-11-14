"""영상 관찰값 (영역·샷·말소리·화면 문구) 의 규칙과 직렬화."""

from __future__ import annotations

import pytest

from frame.domain.errors import ValidationError
from frame.domain.screen import Region, TextRole, TextSource
from frame.domain.series.shortform import MediaInfo
from frame.domain.series.source_analysis import (
    ScreenText,
    Shot,
    SpeechSegment,
    Transcript,
    VideoAnalysis,
    Word,
    shots_from_cuts,
    spoken_characters,
    validate_shots,
)


# ---- 영역 ----
def test_region_must_stay_inside_the_screen():
    Region(0.0, 0.0, 1.0, 1.0)
    for bad in ((0.9, 0.0, 0.2, 0.1), (0.0, 0.0, 0.0, 0.5), (-0.1, 0.0, 0.5, 0.5)):
        with pytest.raises(ValidationError):
            Region(*bad)
    with pytest.raises(ValidationError):
        Region(float("nan"), 0.0, 0.5, 0.5)


def test_clamped_cuts_a_model_box_to_the_screen():
    cut = Region.clamped(0.8, 0.9, 0.5, 0.5)

    assert cut is not None
    assert (cut.x, cut.y) == (0.8, 0.9)
    assert (cut.w, cut.h) == (pytest.approx(0.2), pytest.approx(0.1))
    assert Region.clamped(1.2, 0.0, 0.5, 0.5) is None
    assert Region.clamped(float("nan"), 0.0, 0.5, 0.5) is None
    assert Region.clamped("x", 0.0, 0.5, 0.5) is None


def test_region_helpers():
    title = Region(0.1, 0.05, 0.8, 0.1)
    assert title.anchor == "top" and Region(0.1, 0.8, 0.8, 0.1).anchor == "bottom"
    assert Region(0.1, 0.45, 0.8, 0.1).anchor == "middle"
    assert title.center == (pytest.approx(0.5), pytest.approx(0.1))
    assert title.iou(title) == pytest.approx(1.0)
    assert title.iou(Region(0.1, 0.5, 0.8, 0.1)) == 0.0
    padded = title.padded(0.5)
    assert padded.x == pytest.approx(0.0) and padded.y == pytest.approx(0.0)  # 화면 안으로 잘린다
    assert Region.from_dict(title.to_dict()) == title


# ---- 샷 ----
def test_shots_from_cuts_covers_the_whole_video():
    shots = shots_from_cuts([4000, 8000, 12000], 16000)

    assert [(s.start_ms, s.end_ms) for s in shots] == [
        (0, 4000),
        (4000, 8000),
        (8000, 12000),
        (12000, 16000),
    ]
    assert [s.index for s in shots] == [0, 1, 2, 3]
    validate_shots(shots, 16000)
    assert shots_from_cuts([], 5000) == (Shot(0, 0, 5000),)


def test_cuts_outside_the_video_or_too_close_are_dropped():
    shots = shots_from_cuts([0, 300, 4000, 4200, 15800, 16000, 99999], 16000, min_shot_ms=500)

    assert [(s.start_ms, s.end_ms) for s in shots] == [(0, 4000), (4000, 16000)]


def test_shots_must_be_contiguous():
    with pytest.raises(ValidationError):
        validate_shots([], 1000)
    with pytest.raises(ValidationError):
        validate_shots([Shot(0, 0, 1000), Shot(1, 1500, 2000)])
    with pytest.raises(ValidationError):
        validate_shots([Shot(0, 0, 1000)], 2000)


# ---- 말소리 ----
def _transcript() -> Transcript:
    return Transcript(
        language="ko",
        segments=(
            SpeechSegment(500, 2500, "오늘의 메뉴는 바닐라 라떼!", (Word(500, 1200, "오늘의"),)),
            SpeechSegment(4500, 6500, "가격은 4,500원입니다."),
        ),
    )


def test_transcript_text_and_pace():
    transcript = _transcript()

    assert transcript.text == "오늘의 메뉴는 바닐라 라떼! 가격은 4,500원입니다."
    assert transcript.speech_ms == 4000
    # 공백·문장부호를 뺀 글자 수 ÷ 말한 시간
    assert spoken_characters("오늘의 메뉴는 바닐라 라떼!") == 11
    assert spoken_characters("가격은 4,500원입니다.") == 11
    assert transcript.pace_cps == pytest.approx(22 / 4.0)
    assert [s.text for s in transcript.within(4000, 8000)] == ["가격은 4,500원입니다."]


def test_empty_transcript_has_no_pace():
    silent = Transcript(language=None, segments=())
    assert silent.is_empty and silent.pace_cps is None and silent.text == ""
    assert Transcript(segments=(SpeechSegment(0, 1000, "  "),)).is_empty


def test_transcript_segments_must_be_ordered_and_inside_the_audio():
    _transcript().validate(7000)
    with pytest.raises(ValidationError):
        _transcript().validate(6000)
    with pytest.raises(ValidationError):
        Transcript(
            segments=(SpeechSegment(1000, 2000, "a"), SpeechSegment(1500, 2500, "b"))
        ).validate()
    with pytest.raises(ValidationError):
        Transcript(segments=(SpeechSegment(2000, 1000, "a"),)).validate()


# ---- 묶음 ----
def test_analysis_round_trips_and_validates():
    analysis = VideoAnalysis(
        media=MediaInfo(480, 854, 16000, fps=30.0, has_audio=True),
        shots=shots_from_cuts([4000, 8000, 12000], 16000),
        transcript=_transcript(),
    )

    analysis.validate()
    assert VideoAnalysis.from_dict(analysis.to_dict()) == analysis
    without_speech = VideoAnalysis(analysis.media, analysis.shots)
    assert VideoAnalysis.from_dict(without_speech.to_dict()).transcript is None
    with pytest.raises(ValidationError):
        VideoAnalysis(analysis.media, shots_from_cuts([4000], 8000)).validate()


def test_screen_text_round_trips():
    text = ScreenText(
        "오늘의 메뉴",
        0,
        4000,
        Region(0.1, 0.05, 0.8, 0.1),
        TextRole.TITLE,
        TextSource.OVERLAY,
        "white bold on dark band",
    )

    assert ScreenText.from_dict(text.to_dict()) == text
    bare = ScreenText.from_dict({"text": "FRAME", "start_ms": 0, "end_ms": 1000})
    assert bare.region is None and bare.role is TextRole.OTHER and bare.source is TextSource.OVERLAY
