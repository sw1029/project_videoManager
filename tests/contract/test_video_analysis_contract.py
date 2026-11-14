"""영상 분석 포트의 계약 테스트 (ADR-0025): MediaProber·ShotDetector·SpeechRecognizer·ThemeExtractor.

Fake 와 실제 구현이 같은 규약을 지킨다. ffmpeg 구현은 진짜 ffmpeg 로 합성한 영상을 읽는다 (없으면 건너뜀).
음성 인식·테마 추출의 실제 구현은 가짜 전송·재생 응답으로 돌린다 (네트워크·과금 없음).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

from frame.adapters.analysis import (
    FakeMediaProber,
    FakeShotDetector,
    FakeSpeechRecognizer,
    FakeThemeExtractor,
)
from frame.adapters.analysis.ffmpeg_media import FfmpegMediaProber, FfmpegShotDetector
from frame.adapters.fake_media import fake_recording, fake_video
from frame.application.ports.video_analysis import (
    MediaProber,
    ShotDetector,
    SpeechAudio,
    SpeechRecognitionError,
    ThemeExtractionError,
    ThemeExtractionRequest,
    VideoAnalysisError,
)
from frame.domain.shortform import check_platforms
from frame.domain.theme import SourceOrigin
from frame.domain.video_analysis import (
    SpeechSegment,
    Transcript,
    VideoAnalysis,
    shots_from_cuts,
    validate_shots,
)
from tests.support.media import make_cut_clip
from tests.support.themes import cafe_source
from tests.support.video import find_ffmpeg, make_clip

FFMPEG = find_ffmpeg()
SAID = Transcript(
    "ko",
    (
        SpeechSegment(300, 1800, "안녕하세요, 오늘의 메뉴예요."),
        SpeechSegment(2300, 3700, "바닐라 라떼입니다."),
    ),
)


# ---- MediaProber · ShotDetector ----
@dataclass
class Kit:
    name: str
    prober: MediaProber
    detector: ShotDetector
    video: bytes  # 64x114, 10 fps, 2초씩 세 구간 (컷 2000·4000 ms)
    sounding: bytes  # 같은 영상에 소리가 든 것
    uncut: bytes  # 컷이 없는 4초 영상


def _fake_kit() -> Kit:
    shape = {"width": 64, "height": 114, "fps": 10.0}
    return Kit(
        "fake",
        FakeMediaProber(),
        FakeShotDetector(),
        fake_video(duration_ms=6000, cuts_ms=(2000, 4000), **shape),
        fake_video(duration_ms=6000, cuts_ms=(2000, 4000), transcript=SAID, **shape),
        fake_video(duration_ms=4000, cuts_ms=(), **shape),
    )


def _ffmpeg_kit(folder) -> Kit:
    return Kit(
        "ffmpeg",
        FfmpegMediaProber(FFMPEG),
        FfmpegShotDetector(FFMPEG),
        make_cut_clip(FFMPEG, folder / "cuts.mp4"),
        make_cut_clip(FFMPEG, folder / "sounding.mp4", tone_hz=440),
        make_clip(FFMPEG, folder / "uncut.mp4", source="color=c=red"),
    )


@pytest.fixture(scope="module", params=["fake", "ffmpeg"])
def kit(request, tmp_path_factory) -> Kit:
    if request.param == "fake":
        return _fake_kit()
    if FFMPEG is None:
        pytest.skip("ffmpeg 없음 (video extra 미설치)")
    return _ffmpeg_kit(tmp_path_factory.mktemp("analysis"))


def test_probe_reports_shape_length_and_size(kit):
    info = kit.prober.probe(kit.video)

    assert (info.width, info.height) == (64, 114)
    assert info.duration_ms == 6000 and info.fps == 10.0
    assert info.has_audio is False and info.size_bytes == len(kit.video)
    assert kit.prober.probe(kit.sounding).has_audio is True
    assert info.orientation == "vertical"
    assert check_platforms(info).within  # 겉모습은 그대로 숏폼 규격 판정에 들어간다


def test_probe_rejects_what_is_not_a_video(kit):
    with pytest.raises(VideoAnalysisError):
        kit.prober.probe(b"not a video at all")


def test_shots_are_contiguous_and_end_at_the_video_end(kit):
    media = kit.prober.probe(kit.video)
    shots = kit.detector.detect(kit.video, media)

    validate_shots(shots, media.duration_ms)
    assert [(s.start_ms, s.end_ms) for s in shots] == [(0, 2000), (2000, 4000), (4000, 6000)]


def test_video_without_cuts_is_one_shot(kit):
    media = kit.prober.probe(kit.uncut)
    shots = kit.detector.detect(kit.uncut, media)

    assert [(s.start_ms, s.end_ms) for s in shots] == [(0, 4000)]


def test_shot_detector_rejects_what_is_not_a_video(kit):
    media = kit.prober.probe(kit.video)
    with pytest.raises(VideoAnalysisError):
        kit.detector.detect(b"not a video at all", media)


# ---- SpeechRecognizer ----
@pytest.fixture(params=["fake"])
def recognizer_case(request):
    """(인식기, 말이 든 음성, 말이 없는 음성, 실패하는 인식기)."""
    recording = fake_recording(SAID, 4000)
    silent = fake_recording(Transcript(), 2000)
    return (
        FakeSpeechRecognizer(),
        SpeechAudio(recording, "application/json", 4000),
        SpeechAudio(silent, "application/json", 2000),
        FakeSpeechRecognizer(error=SpeechRecognitionError("주입된 오류")),
    )


def test_transcript_is_ordered_and_inside_the_audio(recognizer_case):
    recognizer, audio, _, _ = recognizer_case
    transcript = recognizer.transcribe(audio, language="ko", hint="바닐라 라떼")

    transcript.validate(audio.duration_ms)
    assert transcript.text == SAID.text and transcript.language == "ko"
    assert not transcript.is_empty and transcript.pace_cps is not None


def test_silence_is_an_empty_transcript_not_an_error(recognizer_case):
    recognizer, _, silent, _ = recognizer_case
    transcript = recognizer.transcribe(silent)

    assert transcript.is_empty and transcript.segments == ()


def test_recognizer_failure_is_a_speech_recognition_error(recognizer_case):
    _, audio, _, failing = recognizer_case
    with pytest.raises(SpeechRecognitionError):
        failing.transcribe(audio)


# ---- ThemeExtractor ----
def _request(video: bytes, *, origin=SourceOrigin.FRAME_GENERATED, cuts=(4000, 8000, 12000)):
    source = cafe_source(origin)
    analysis = VideoAnalysis(
        media=source.media,
        shots=shots_from_cuts(cuts, source.media.duration_ms),
        transcript=Transcript(
            "ko",
            (
                SpeechSegment(400, 2600, "안녕하세요, 오늘의 메뉴예요."),
                SpeechSegment(4500, 6200, "바닐라 라떼입니다."),
                SpeechSegment(8600, 10400, "가격은 4,500원."),
                SpeechSegment(12500, 14000, "매장에서 만나요!"),
            ),
        ),
    )
    return ThemeExtractionRequest("thm_1", source, video, analysis, title="카페 신메뉴 소개")


@pytest.fixture(params=["fake"])
def extractor_case(request):
    """(추출기를 만드는 함수, 영상 바이트). 추출기는 요청마다 새로 만든다."""
    return (lambda: FakeThemeExtractor(), fake_video())


def _assert_extraction_contract(extraction, request) -> None:
    theme, ir = extraction.theme, extraction.source_ir
    theme.validate()
    ir.validate()
    assert theme.theme_id == request.theme_id and theme.source == request.source
    # 원본 IR 의 장면 = 테마의 자리 (키·순서·길이)
    assert [s.scene_key for s in ir.scenes] == [s.slot_key for s in theme.format.slots]
    assert [s.duration_ms for s in ir.scenes] == [s.duration_ms for s in theme.format.slots]
    total = request.analysis.media.duration_ms
    assert abs(theme.format.total_duration_ms - total) < 100
    assert theme.format.total_duration_ms % 100 == 0
    if total >= request.min_slot_ms:
        assert all(slot.duration_ms >= request.min_slot_ms for slot in theme.format.slots)
    assert {c.entity_key for c in theme.cast} <= {e.entity_key for e in ir.entities}
    for text in extraction.screen_texts:
        assert 0 <= text.start_ms < text.end_ms <= total + 100


def test_extraction_gives_a_valid_theme_and_source_ir(extractor_case):
    make, video = extractor_case
    request = _request(video)
    extraction = make().extract(request)

    _assert_extraction_contract(extraction, request)
    assert extraction.theme.title == "카페 신메뉴 소개"
    assert len(extraction.theme.format.slots) == 4
    assert extraction.theme.format.observed_shot_ms == (4000, 4000, 4000, 4000)


def test_short_shots_are_merged_into_reproducible_slots(extractor_case):
    make, video = extractor_case
    request = _request(video, cuts=(1000, 2000, 3000, 9000, 15000))
    extraction = make().extract(request)

    _assert_extraction_contract(extraction, request)
    assert [s.duration_ms for s in extraction.theme.format.slots] == [3000, 6000, 7000]
    assert extraction.theme.format.observed_shot_ms == (1000, 1000, 1000, 6000, 6000, 1000)


def test_narration_of_the_source_is_kept_in_the_theme(extractor_case):
    make, video = extractor_case
    request = _request(video)
    narration = make().extract(request).theme.narration

    assert narration.present and narration.language == "ko"
    assert narration.pace_cps == pytest.approx(request.analysis.transcript.pace_cps)
    assert {b.slot_key for b in narration.beats} == {
        s.slot_key for s in make().extract(request).theme.format.slots
    }


def test_silent_source_has_no_narration(extractor_case):
    make, video = extractor_case
    request = _request(video)
    silent = replace(request, analysis=replace(request.analysis, transcript=None))
    narration = make().extract(silent).theme.narration

    assert not narration.present and narration.pace_cps is None and narration.beats == ()


def test_external_source_gets_no_reference_images(extractor_case):
    make, video = extractor_case
    request = _request(video, origin=SourceOrigin.EXTERNAL)
    extraction = make().extract(request)

    _assert_extraction_contract(extraction, request)
    assert extraction.theme.references() == ()


def test_extractor_failure_is_an_adapter_error():
    failing = FakeThemeExtractor(error=ThemeExtractionError("주입된 오류"))
    with pytest.raises(ThemeExtractionError):
        failing.extract(_request(fake_video()))
