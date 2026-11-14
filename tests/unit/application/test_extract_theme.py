"""ExtractTheme: 영상 한 편 -> 숏폼 규격 판정 -> 관찰값 -> 테마 저장 (ADR-0025)."""

from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from frame.adapters.analysis import FakeSpeechRecognizer, FakeThemeExtractor
from frame.adapters.fake_media import fake_video
from frame.application.ports.video_analysis import (
    SpeechRecognitionError,
    ThemeExtraction,
    ThemeExtractionError,
)
from frame.application.series import ExtractThemeCommand, NotShortform
from frame.config.theme_wiring import ThemeSettings
from frame.domain.errors import ValidationError
from frame.domain.theme import SourceOrigin
from frame.domain.video_analysis import SpeechSegment, Transcript
from tests.support.series import series_container, series_of

SAID = Transcript(
    "ko",
    (
        SpeechSegment(400, 2600, "안녕하세요, 오늘의 메뉴예요."),
        SpeechSegment(4500, 6200, "바닐라 라떼입니다."),
        SpeechSegment(8600, 10400, "가격은 4,500원."),
        SpeechSegment(12500, 14000, "매장에서 만나요!"),
    ),
)
VIDEO = fake_video(transcript=SAID)


def _command(video: bytes = VIDEO, **changes) -> ExtractThemeCommand:
    base = {
        "video": video,
        "title": "카페 신메뉴 소개",
        "source_url": "https://example.com/v/1",
        "license": "CC0",
    }
    return ExtractThemeCommand(**{**base, **changes})


def test_theme_is_extracted_and_saved_with_its_evidence():
    series = series_of(series_container())
    result = series.extract_theme.execute(_command())
    record = result.record

    assert result.verdict.within
    assert series.themes.get(record.theme.theme_id) == record
    theme = record.theme
    assert theme.title == "카페 신메뉴 소개" and len(theme.format.slots) == 4
    assert theme.narration.present and theme.narration.pace_cps == pytest.approx(SAID.pace_cps)
    # 근거: 겉모습·컷·말소리와 숏폼 규격 판정
    assert [(s.start_ms, s.end_ms) for s in record.analysis.shots] == [
        (0, 4000),
        (4000, 8000),
        (8000, 12000),
        (12000, 16000),
    ]
    assert record.analysis.transcript.text == SAID.text
    assert record.shortform["within"] is True and record.notes == ()
    assert [s.scene_key for s in record.source_ir.scenes] == [
        s.slot_key for s in theme.format.slots
    ]


def test_source_is_recorded_by_fingerprint_not_by_copy():
    """외부 영상은 사본을 두지 않는다: 지문·겉모습·출처·라이선스만 남는다."""
    container = series_container()
    source = series_of(container).extract_theme.execute(_command()).record.theme.source

    assert source.sha256 == hashlib.sha256(VIDEO).hexdigest()
    assert source.origin is SourceOrigin.EXTERNAL and source.stored_uri is None
    assert (source.source_url, source.license) == ("https://example.com/v/1", "CC0")
    assert source.media.duration_ms == 16000 and source.media.aspect_label == "9:16"
    with pytest.raises(ValidationError, match="사본"):
        series_of(container).extract_theme.execute(_command(keep_source=True))


def test_frame_generated_source_may_keep_a_copy():
    container = series_container()
    command = _command(origin=SourceOrigin.FRAME_GENERATED, keep_source=True)
    source = series_of(container).extract_theme.execute(command).record.theme.source

    assert source.stored_uri is not None
    assert container.artifact_store.get(source.stored_uri) == VIDEO


@pytest.mark.parametrize(
    ("shape", "reason"),
    [
        ({"width": 1920, "height": 1080}, "화면비"),
        ({"duration_ms": 200_000, "cuts_ms": ()}, "길이"),
    ],
)
def test_video_outside_the_shortform_criteria_is_rejected(shape, reason):
    series = series_of(series_container())
    video = fake_video(**shape)

    with pytest.raises(NotShortform, match=reason) as caught:
        series.extract_theme.execute(_command(video))
    assert caught.value.context["report"]["within"] is False
    assert series.themes.list() == []
    # 규격 확인을 끄면 뽑는다 (판정은 기록에 남는다)
    record = series.extract_theme.execute(_command(video, require_shortform=False)).record
    assert record.shortform["within"] is False


def test_only_selected_platforms_decide_the_criteria():
    """릴스만 기준으로 삼으면 가로 영상도 기준 안이다."""
    settings = ThemeSettings(platforms=("INSTAGRAM_REELS",))
    series = series_of(series_container(theme_settings=settings))

    assert series.extract_theme.execute(
        _command(fake_video(width=1920, height=1080))
    ).verdict.within


def test_speech_failure_does_not_stop_the_extraction():
    failing = FakeSpeechRecognizer(error=SpeechRecognitionError("인식 서버 응답 없음"))
    series = series_of(series_container(speech_recognizer=failing))
    record = series.extract_theme.execute(_command()).record

    assert record.analysis.transcript is None and not record.theme.narration.present
    assert any("인식 서버 응답 없음" in note for note in record.notes)


def test_silent_video_or_recognizer_off_gives_no_transcript():
    spy = FakeSpeechRecognizer()
    silent = series_of(series_container(speech_recognizer=spy))
    assert silent.extract_theme.execute(_command(fake_video())).record.analysis.transcript is None
    assert spy.calls == []  # 소리가 없는 영상은 인식기를 부르지 않는다

    off = series_of(series_container(theme_settings=ThemeSettings(speech="off")))
    assert off.recognizer is None
    assert off.extract_theme.execute(_command()).record.analysis.transcript is None


def test_language_hint_reaches_the_recognizer():
    recognizer = FakeSpeechRecognizer()
    series = series_of(series_container(speech_recognizer=recognizer))
    series.extract_theme.execute(_command(language="ko"))

    assert recognizer.calls[0]["language"] == "ko"


class _BrokenExtractor(FakeThemeExtractor):
    """계약을 어기는 추출기: 자리와 원본 IR 의 장면이 다르다."""

    def __init__(self, mode: str) -> None:
        super().__init__()
        self.mode = mode

    def extract(self, request):
        good = super().extract(request)
        if self.mode == "other_id":
            return ThemeExtraction(replace(good.theme, theme_id="thm_other"), good.source_ir)
        if self.mode == "scene_mismatch":
            ir = replace(good.source_ir, scenes=good.source_ir.scenes[:1])
            return ThemeExtraction(good.theme, ir)
        raise ThemeExtractionError("모델 응답을 해석하지 못함")


@pytest.mark.parametrize("mode", ["other_id", "scene_mismatch", "error"])
def test_result_that_breaks_the_contract_is_not_saved(mode):
    series = series_of(series_container(theme_extractor=_BrokenExtractor(mode)))

    with pytest.raises(ThemeExtractionError):
        series.extract_theme.execute(_command())
    assert series.themes.list() == []


def test_empty_video_is_rejected():
    with pytest.raises(ValidationError):
        series_of(series_container()).extract_theme.execute(_command(b""))
