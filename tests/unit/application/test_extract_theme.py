"""ExtractTheme: 영상 한 편 -> 숏폼 규격 판정 -> 관찰값 -> 테마 저장 (ADR-0025)."""

from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from frame.adapters.fake_media import fake_video
from frame.adapters.series.theme_extraction import FakeThemeExtractor
from frame.adapters.speech import FakeSpeechRecognizer
from frame.application.ports.series.theme_extraction import ThemeExtraction, ThemeExtractionError
from frame.application.ports.speech import SpeechRecognitionError, SpeechServiceError
from frame.application.use_cases.series import ExtractThemeCommand, NotShortform
from frame.config.theme_settings import ThemeSettings
from frame.domain.errors import ValidationError
from frame.domain.script import LineKind, SoundKind
from frame.domain.series.theme import SourceOrigin
from frame.domain.speech import SpeechSegment, Transcript
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


def test_theme_record_keeps_the_script_of_the_source():
    """뽑은 대본을 테마의 기록에 함께 둔다. 인식은 한 번만 한다 (테마 추출의 받아쓰기를 그대로 쓴다)."""
    container = series_container()
    series = series_of(container)
    record = series.extract_theme.execute(_command()).record

    script = record.script
    assert [(line.line_id, line.scene_key, line.text) for line in script.lines] == [
        ("S1", "slot_1", "안녕하세요, 오늘의 메뉴예요."),
        ("S2", "slot_2", "바닐라 라떼입니다."),
        ("S3", "slot_3", "가격은 4,500원."),
        ("S4", "slot_4", "매장에서 만나요!"),
    ]
    # 가짜 추출기의 원본 IR 에는 사람이 없다: 말할 사람이 화면에 없으니 내레이션이다
    assert {line.kind for line in script.lines} == {LineKind.NARRATION}
    # 말소리 사이의 조용한 시간이 무음으로 남는다
    assert {span.kind for span in script.sounds} == {SoundKind.SILENCE}
    assert series.themes.get(record.theme.theme_id).script == script  # 저장된다
    assert len(series.recognizer.calls) == 1


def test_the_script_becomes_evidence_for_the_theme():
    """대본에서 잰 내레이션 규칙이 테마에 들어가고, 화면과 말을 견준 근거가 기록에 남는다."""
    series = series_of(series_container())

    record = series.extract_theme.execute(_command()).record

    beat = record.theme.narration.beat("slot_3")
    assert (beat.example, beat.chars, beat.spoken_ms, beat.lead_in_ms, beat.line_ids) == (
        "가격은 4,500원.",
        8,
        1800,
        600,
        ("S3",),
    )
    assert record.evidence is not None and record.evidence.discrepancies == ()
    # 말에만 나온 수량은 '빠뜨린 변수일 수 있다' 고 남는다 (가짜 추출기는 변수를 뽑지 않는다)
    assert [(u.line_id, u.text) for u in record.evidence.unlinked] == [("S3", "4500원")]
    assert series.themes.get(record.theme.theme_id).evidence == record.evidence


def test_slot_boundaries_do_not_cut_a_sentence_when_the_cuts_leave_a_choice():
    said = Transcript(
        "ko",
        (
            SpeechSegment(300, 4800, "첫 문장은 컷을 넘어 이어집니다."),
            SpeechSegment(5200, 8800, "둘째 문장."),
        ),
    )
    cuts = {
        "duration_ms": 9000,
        "cuts_ms": (3500, 5000),
    }  # 가운데 1.5초 컷은 혼자 자리가 될 수 없다
    series = series_of(series_container())

    with_speech = series.extract_theme.execute(_command(fake_video(**cuts, transcript=said)))
    without = series.extract_theme.execute(_command(fake_video(**cuts)))

    assert [s.duration_ms for s in with_speech.record.theme.format.slots] == [5000, 4000]
    assert [s.duration_ms for s in without.record.theme.format.slots] == [3500, 5500]
    assert [line.scene_key for line in with_speech.record.script.lines] == ["slot_1", "slot_2"]


def test_a_long_single_shot_is_split_at_pauses_when_a_maximum_is_set():
    said = Transcript(
        "ko",
        (
            SpeechSegment(500, 7000, "첫 문단입니다."),
            SpeechSegment(7600, 15200, "둘째 문단입니다."),
            SpeechSegment(16000, 23000, "셋째 문단입니다."),
        ),
    )
    video = fake_video(duration_ms=24000, cuts_ms=(), transcript=said)
    limited = ThemeSettings(text_reader="fake", max_slot_ms=10000)

    whole = series_of(series_container()).extract_theme.execute(_command(video))
    split = series_of(series_container(theme_settings=limited)).extract_theme.execute(
        _command(video)
    )

    assert [s.duration_ms for s in whole.record.theme.format.slots] == [24000]
    assert [s.duration_ms for s in split.record.theme.format.slots] == [7300, 8300, 8400]


def test_source_without_speech_has_an_empty_script():
    series = series_of(series_container())

    silent = series.extract_theme.execute(_command(fake_video())).record

    assert silent.script is not None and silent.script.is_empty
    assert silent.script.sounds == ()  # 소리 스트림이 없다


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


def test_speech_that_cannot_be_understood_does_not_stop_the_extraction():
    """알아듣지 못한 것은 내용의 문제다: 내레이션 없이 뽑는다. 메모에는 오류 코드만 남긴다 (원문은 로그로)."""
    failing = FakeSpeechRecognizer(
        error=SpeechRecognitionError(
            "음성 인식 API 오류 400: C:\\Users\\someone\\clip.wav 를 읽지 못함"
        )
    )
    series = series_of(series_container(speech_recognizer=failing))
    record = series.extract_theme.execute(_command()).record

    assert record.analysis.transcript is None and not record.theme.narration.present
    assert any("SPEECH_RECOGNITION_ERROR" in note for note in record.notes)
    assert not any("someone" in note or "400" in note for note in record.notes)


def test_speech_service_failure_stops_before_the_paid_extraction():
    """키 없음·인증 실패·한도 초과·연결 실패는 설정·환경의 문제다. 내레이션 없는 테마를 돈 들여 만들지 않는다."""
    extractor = FakeThemeExtractor()
    failing = FakeSpeechRecognizer(error=SpeechServiceError("음성 인식 API 인증 실패 (401)"))
    series = series_of(series_container(speech_recognizer=failing, theme_extractor=extractor))

    with pytest.raises(SpeechServiceError):
        series.extract_theme.execute(_command())
    assert extractor.requests == [] and series.themes.list() == []


def test_silent_video_or_recognizer_off_gives_no_transcript():
    spy = FakeSpeechRecognizer()
    silent = series_of(series_container(speech_recognizer=spy))
    assert silent.extract_theme.execute(_command(fake_video())).record.analysis.transcript is None
    assert spy.calls == []  # 소리가 없는 영상은 인식기를 부르지 않는다

    off = series_of(series_container(theme_settings=ThemeSettings(speech="off")))
    assert off.recognizer is None
    assert off.extract_theme.execute(_command()).record.analysis.transcript is None


def test_sound_that_is_all_silence_is_not_sent_to_the_recognizer():
    """소리 트랙은 있는데 처음부터 끝까지 무음인 영상: 인식기는 여기서 문장을 지어낸다. 부르지 않는다."""
    spy = FakeSpeechRecognizer()
    series = series_of(series_container(speech_recognizer=spy))
    video = fake_video(transcript=Transcript("ko"), silences=((0, 16000),))

    record = series.extract_theme.execute(_command(video)).record

    assert spy.calls == [] and record.analysis.transcript is None
    assert record.script is not None and record.script.is_empty
    assert any("무음" in note for note in record.notes)


def test_speech_the_recognizer_skipped_is_heard_again_and_reaches_the_script():
    """전체를 받아 적을 때 빠진 마지막 한마디를 그 구간만 다시 받아 적어 대본에 넣는다."""
    spy = FakeSpeechRecognizer()
    series = series_of(series_container(speech_recognizer=spy))
    said = Transcript("ko", SAID.segments[:3])
    last = Transcript("ko", (SpeechSegment(12500, 14000, "매장에서 만나요!", confidence=0.8),))
    silences = ((0, 400), (2600, 4500), (6200, 8600), (10400, 12500), (14000, 16000))
    video = fake_video(transcript=said, silences=silences, missed=last)

    record = series.extract_theme.execute(_command(video)).record

    assert [line.text for line in record.script.lines][-1] == "매장에서 만나요!"
    assert len(record.analysis.transcript.segments) == 4 and len(spy.calls) == 2
    assert any("다시 받아 적었습니다" in note for note in record.notes)


class _FailsAfterTheFirstCall(FakeSpeechRecognizer):
    """전체는 받아 적고, 그 뒤의 호출(빠진 구간을 다시 받아 적기)은 실패하는 인식기."""

    def __init__(self, later: Exception) -> None:
        super().__init__()
        self._later = later

    def transcribe(self, audio, *, language=None, hint=""):
        if self.calls:
            self.calls.append({"bytes": len(audio.data), "language": language, "hint": hint})
            raise self._later
        return super().transcribe(audio, language=language, hint=hint)


def _video_with_a_missed_last_line() -> bytes:
    said = Transcript("ko", SAID.segments[:3])
    last = Transcript("ko", (SpeechSegment(12500, 14000, "매장에서 만나요!", confidence=0.8),))
    silences = ((0, 400), (2600, 4500), (6200, 8600), (10400, 12500), (14000, 16000))
    return fake_video(transcript=said, silences=silences, missed=last)


def test_failing_to_hear_a_gap_again_keeps_the_first_transcript():
    """다시 받아 적기는 덤이다. 그것이 실패해도 처음 받은 받아쓰기는 버리지 않는다 (감사 BTS-D12·D31).

    버리면 내레이션 없는 테마를 유료로 뽑아 저장하고, 같은 영상은 그 테마를 계속 돌려준다.
    """
    spy = _FailsAfterTheFirstCall(
        SpeechRecognitionError("그 구간을 알아듣지 못했습니다: C:\\somewhere\\part.wav")
    )
    series = series_of(series_container(speech_recognizer=spy))

    record = series.extract_theme.execute(_command(_video_with_a_missed_last_line())).record

    assert len(spy.calls) == 2
    assert len(record.analysis.transcript.segments) == 3 and record.theme.narration.present
    assert [line.text for line in record.script.lines][-1] == "가격은 4,500원."
    note = next(note for note in record.notes if "다시 받아 적지 못했습니다" in note)
    assert "SPEECH_RECOGNITION_ERROR" in note and "somewhere" not in note  # 원문은 로그로만


def test_service_failure_while_hearing_a_gap_again_still_stops_before_the_paid_extraction():
    extractor = FakeThemeExtractor()
    spy = _FailsAfterTheFirstCall(SpeechServiceError("음성 인식 API 요청 한도 초과 (429)"))
    series = series_of(series_container(speech_recognizer=spy, theme_extractor=extractor))

    with pytest.raises(SpeechServiceError):
        series.extract_theme.execute(_command(_video_with_a_missed_last_line()))
    assert extractor.requests == [] and series.themes.list() == []


@pytest.mark.parametrize("marks", ["♪", "...", "♪♪ ♪"])
def test_transcript_with_no_letters_gives_a_valid_theme_without_narration(marks):
    """글자 없는 받아쓰기('♪'·'...')는 말이 없는 것이다 (감사 BTS-D30·D62).

    추출은 한 번만 부르고, 저장된 테마는 규칙을 통과하는 '내레이션 없는 테마' 다. 다시 부르면 그 테마를 돌려준다.
    """
    said = Transcript("ko", (SpeechSegment(400, 2600, marks), SpeechSegment(4500, 6200, marks)))
    series = series_of(series_container())

    record = series.extract_theme.execute(_command(fake_video(transcript=said))).record

    record.theme.validate()
    assert not record.theme.narration.present and record.theme.narration.pace_cps is None
    assert record.script.is_empty and len(series.extractor.requests) == 1
    assert series.themes.get(record.theme.theme_id) == record
    again = series.extract_theme.execute(_command(fake_video(transcript=said)))
    assert again.reused and len(series.extractor.requests) == 1


def test_extraction_is_saved_even_when_the_script_cannot_be_linked(monkeypatch):
    """대본을 근거로 옮기는 단계는 모델을 부르지 않는다. 그 단계가 실패해도 돈 들여 받은 추출 결과는 저장한다.

    저장하지 않으면 다시 부를 때 추출이 다시 과금된다 (감사 BTS-D62). 무슨 일이 있었는지는 메모로 남긴다.
    """
    from frame.application.use_cases.series import extract_theme as module

    def broken(theme, script, evidence):
        raise ValidationError("대본을 옮길 수 없습니다")

    monkeypatch.setattr(module, "apply_script", broken)
    series = series_of(series_container())

    record = series.extract_theme.execute(_command()).record

    record.theme.validate()
    assert series.themes.get(record.theme.theme_id) == record
    assert any("SCRIPT_LINK_FAILED" in note for note in record.notes)
    assert len(series.extractor.requests) == 1
    assert series.extract_theme.execute(_command()).reused  # 다시 과금하지 않는다


def test_theme_changed_by_the_script_is_checked_before_it_is_saved(monkeypatch):
    """대본에서 옮긴 것이 테마의 규칙을 깨면 (말 빠르기 0 등) 그 테마를 저장하지 않고 추출 결과를 저장한다."""
    from frame.application.use_cases.series import extract_theme as module

    def zero_pace(theme, script, evidence):
        return replace(theme, narration=replace(theme.narration, pace_cps=0.0))

    monkeypatch.setattr(module, "apply_script", zero_pace)
    series = series_of(series_container())

    record = series.extract_theme.execute(_command()).record

    record.theme.validate()
    assert record.theme.narration.pace_cps == pytest.approx(SAID.pace_cps)  # 추출기가 잰 값 그대로
    assert any("SCRIPT_LINK_FAILED" in note for note in record.notes)


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


# ---- 같은 영상은 다시 뽑지 않는다 (감사 BTS-12) ----
def test_same_video_is_not_extracted_twice():
    """같은 영상·같은 추출기면 앞서 뽑은 테마를 돌려준다. 인식도 추출도 다시 부르지 않고, 테마가 둘 생기지 않는다."""
    recognizer, extractor = FakeSpeechRecognizer(), FakeThemeExtractor()
    series = series_of(series_container(speech_recognizer=recognizer, theme_extractor=extractor))
    first = series.extract_theme.execute(_command())
    assert first.reused is False and first.record.detail["extractor"] == extractor.signature

    again = series.extract_theme.execute(_command(title="다른 제목으로 다시"))

    assert again.reused is True and again.record == first.record and again.verdict.within
    assert len(recognizer.calls) == 1 and len(extractor.requests) == 1
    assert len(series.themes.list()) == 1


def test_forced_or_differently_configured_extraction_runs_again():
    extractor = FakeThemeExtractor()
    series = series_of(series_container(theme_extractor=extractor))
    first = series.extract_theme.execute(_command()).record

    forced = series.extract_theme.execute(_command(force=True))
    assert forced.reused is False and forced.record.theme.theme_id != first.theme.theme_id
    assert len(extractor.requests) == 2

    # 다른 영상, 다른 언어는 다른 입력이다
    series.extract_theme.execute(_command(fake_video(transcript=SAID, duration_ms=20000)))
    series.extract_theme.execute(_command(language="en"))
    assert len(extractor.requests) == 4

    # 추출기의 설정이 바뀌면(모델·프롬프트 판) 예전 결과를 쓰지 않는다
    other = FakeThemeExtractor(signature="fake:2")
    changed = series_of(series_container(theme_extractor=other))
    changed.themes.save(first)
    assert changed.extract_theme.execute(_command()).reused is False


def test_failed_extraction_leaves_no_copy_of_the_source():
    """추출이 실패하면 영상의 사본도 남기지 않는다 (주인 없는 사본)."""
    container = series_container(theme_extractor=_BrokenExtractor("error"))
    command = _command(origin=SourceOrigin.FRAME_GENERATED, keep_source=True)

    with pytest.raises(ThemeExtractionError):
        series_of(container).extract_theme.execute(command)
    assert not any("source" in uri for uri in container.artifact_store._blobs)


def test_video_longer_than_the_limit_is_refused_even_without_the_shortform_check():
    """숏폼 판정을 꺼도 길이 상한은 있다: 긴 영상의 대본과 샷이 통째로 들어가지 않는다 (감사 BTS-30 의 남은 것)."""
    extractor = FakeThemeExtractor()
    recognizer = FakeSpeechRecognizer()
    series = series_of(series_container(theme_extractor=extractor, speech_recognizer=recognizer))
    long = fake_video(duration_ms=21 * 60 * 1000, transcript=SAID)

    with pytest.raises(ValidationError, match="너무 깁니다"):
        series.extract_theme.execute(_command(long, require_shortform=False))
    assert extractor.requests == [] and recognizer.calls == []


def test_oversized_video_is_refused_before_anything_reads_it(monkeypatch):
    """터무니없이 큰 파일은 겉모습을 읽기도 전에 끊는다 (단계마다 임시 파일로 다시 쓰인다)."""
    from frame.application.use_cases.series import extract_theme

    monkeypatch.setattr(extract_theme, "MAX_SOURCE_BYTES", len(VIDEO) - 1)
    series = series_of(series_container())

    with pytest.raises(ValidationError, match="너무 큽니다"):
        series.extract_theme.execute(_command())
    assert series.prober.calls == 0 and series.themes.list() == []
