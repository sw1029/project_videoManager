"""AudioEditor 계약 테스트 (ADR-0026). Fake 와 ffmpeg 구현이 같은 규약을 지킨다.

- probe·speech_audio: 소리가 없으면 None.
- render_track: 길이가 total_ms 인 트랙. 조각이 없으면 무음. 없는 녹음을 가리키면 AudioError.
- mux: 영상의 그림은 그대로, 소리만 바뀐다. 결과의 길이는 영상의 길이다.
ffmpeg 구현은 진짜 ffmpeg 로 합성한 영상·순음을 쓴다 (없으면 건너뜀).
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from frame.adapters.fake_media import fake_recording, fake_video
from frame.adapters.media_tools import detect_silences
from frame.adapters.series.audio import FakeAudioEditor
from frame.adapters.series.audio.ffmpeg import FfmpegAudioEditor
from frame.adapters.series.source_analysis import FakeMediaProber
from frame.adapters.series.source_analysis.ffmpeg import FfmpegMediaProber
from frame.adapters.speech import FakeSpeechRecognizer
from frame.application.ports.series.audio import AudioEditor, AudioError, TrackCut
from frame.application.ports.series.source_analysis import MediaProber
from frame.domain.speech import SpeechSegment, Transcript
from tests.support.media import make_cut_clip, make_tone, make_tone_with_gap
from tests.support.video import find_ffmpeg

FFMPEG = find_ffmpeg()
TOLERANCE_MS = 120  # 인코딩이 만드는 길이 오차 (AAC 프레임·컨테이너 반올림)


@dataclass
class Kit:
    name: str
    editor: AudioEditor
    prober: MediaProber
    silent_video: bytes  # 6초, 소리 없음
    sounding_video: bytes  # 6초, 소리 있음
    recording: bytes  # 3초 녹음
    other_recording: bytes  # 2초 녹음
    gapped: bytes  # 1초 소리 - 1.5초 무음 - 1초 소리


def _fake_kit() -> Kit:
    said = Transcript("ko", (SpeechSegment(200, 2600, "안녕하세요 오늘의 메뉴예요"),))
    return Kit(
        "fake",
        FakeAudioEditor(),
        FakeMediaProber(),
        fake_video(duration_ms=6000, cuts_ms=(2000, 4000)),
        fake_video(duration_ms=6000, cuts_ms=(2000, 4000), transcript=said),
        fake_recording(said, 3000),
        fake_recording(Transcript("ko", (SpeechSegment(100, 1800, "말차 라떼입니다"),)), 2000),
        fake_recording(
            Transcript("ko", (SpeechSegment(0, 1000, "하나"), SpeechSegment(2500, 3500, "둘"))),
            3500,
        ),
    )


def _ffmpeg_kit(folder) -> Kit:
    return Kit(
        "ffmpeg",
        FfmpegAudioEditor(FFMPEG),
        FfmpegMediaProber(FFMPEG),
        make_cut_clip(FFMPEG, folder / "silent.mp4"),
        make_cut_clip(FFMPEG, folder / "sounding.mp4", tone_hz=330),
        make_tone(FFMPEG, folder / "recording.wav", seconds=3.0, hz=440),
        make_tone(FFMPEG, folder / "other.m4a", seconds=2.0, hz=660),
        make_tone_with_gap(FFMPEG, folder / "gapped.wav"),
    )


@pytest.fixture(scope="module", params=["fake", "ffmpeg"])
def kit(request, tmp_path_factory) -> Kit:
    if request.param == "fake":
        return _fake_kit()
    if FFMPEG is None:
        pytest.skip("ffmpeg 없음 (video extra 미설치)")
    return _ffmpeg_kit(tmp_path_factory.mktemp("audio"))


def test_probe_reports_audio_or_none(kit):
    assert kit.editor.probe(kit.silent_video) is None
    info = kit.editor.probe(kit.recording)
    assert info is not None and abs(info.duration_ms - 3000) <= TOLERANCE_MS
    with_sound = kit.editor.probe(kit.sounding_video)
    assert with_sound is not None and abs(with_sound.duration_ms - 6000) <= TOLERANCE_MS


def test_sound_profile_reports_length_and_silences(kit):
    """소리 읽기 포트(SoundReader)의 계약: 길이와 무음 구간. 소리가 없으면 None 이다."""
    assert kit.editor.sound_profile(kit.silent_video) is None
    profile = kit.editor.sound_profile(kit.gapped)

    assert profile is not None and abs(profile.duration_ms - 3500) <= TOLERANCE_MS
    (silence,) = profile.silences
    assert abs(silence[0] - 1000) <= TOLERANCE_MS and abs(silence[1] - 2500) <= TOLERANCE_MS
    # 내내 소리가 나는 파일에는 무음 구간이 없다
    steady = kit.editor.sound_profile(kit.other_recording)
    assert steady is not None and all(end - start < 500 for start, end in steady.silences)


def test_speech_audio_is_ready_for_a_recognizer(kit):
    assert kit.editor.speech_audio(kit.silent_video) is None
    speech = kit.editor.speech_audio(kit.sounding_video)

    assert speech is not None and speech.data and speech.media_type
    assert speech.duration_ms is not None and abs(speech.duration_ms - 6000) <= TOLERANCE_MS
    again = kit.editor.probe(speech.data)  # 뽑은 소리도 소리 파일로 읽힌다
    assert again is not None and abs(again.duration_ms - 6000) <= TOLERANCE_MS


def test_speech_audio_of_a_span_is_only_that_span(kit):
    """구간을 주면 그 구간의 소리만 꺼낸다 (줄이 없는 소리만 다시 받아 적을 때 쓴다). 끝을 넘으면 잘라 맞춘다."""
    part = kit.editor.speech_audio(kit.gapped, start_ms=2300, end_ms=3300)

    assert part is not None and part.data and part.duration_ms is not None
    assert abs(part.duration_ms - 1000) <= TOLERANCE_MS
    tail = kit.editor.speech_audio(kit.gapped, start_ms=3000, end_ms=9000)
    assert tail is not None and abs(tail.duration_ms - 500) <= TOLERANCE_MS
    assert kit.editor.speech_audio(kit.silent_video, start_ms=0, end_ms=1000) is None


def test_fake_span_carries_the_speech_inside_it_from_time_zero():
    kit = _fake_kit()

    part = kit.editor.speech_audio(kit.gapped, start_ms=2300, end_ms=3500)

    heard = FakeSpeechRecognizer().transcribe(part)
    assert [(s.start_ms, s.end_ms, s.text) for s in heard.segments] == [(200, 1200, "둘")]


def test_track_has_the_requested_length(kit):
    cuts = [TrackCut(0, 500, 1500, 2000), TrackCut(1, 0, 1000, 4500, tempo=1.1)]
    track = kit.editor.render_track([kit.recording, kit.other_recording], cuts, 6000)
    info = kit.editor.probe(track)

    assert info is not None and abs(info.duration_ms - 6000) <= TOLERANCE_MS


def test_track_without_cuts_is_silence_of_the_requested_length(kit):
    info = kit.editor.probe(kit.editor.render_track([], [], 4000))

    assert info is not None and abs(info.duration_ms - 4000) <= TOLERANCE_MS


def test_cut_past_the_end_is_trimmed_to_the_track(kit):
    track = kit.editor.render_track([kit.recording], [TrackCut(0, 0, 3000, 2500)], 4000)
    info = kit.editor.probe(track)

    assert info is not None and abs(info.duration_ms - 4000) <= TOLERANCE_MS


def test_unknown_source_is_an_error(kit):
    with pytest.raises(AudioError):
        kit.editor.render_track([kit.recording], [TrackCut(3, 0, 1000, 0)], 3000)
    with pytest.raises(AudioError):
        kit.editor.render_track([kit.silent_video], [TrackCut(0, 0, 1000, 0)], 3000)


def test_mux_keeps_the_picture_and_the_video_length(kit):
    track = kit.editor.render_track([kit.recording], [TrackCut(0, 0, 3000, 500)], 6000)
    before = kit.prober.probe(kit.silent_video)
    result = kit.editor.mux(kit.silent_video, track)
    after = kit.prober.probe(result)

    assert (after.width, after.height, after.fps) == (before.width, before.height, before.fps)
    assert abs(after.duration_ms - before.duration_ms) <= TOLERANCE_MS and after.has_audio
    sound = kit.editor.probe(result)
    assert sound is not None and abs(sound.duration_ms - before.duration_ms) <= TOLERANCE_MS


def test_mux_replaces_existing_sound_and_fits_a_short_or_long_track(kit):
    short = kit.editor.render_track([kit.recording], [TrackCut(0, 0, 1000, 0)], 2000)
    long = kit.editor.render_track([kit.recording], [TrackCut(0, 0, 3000, 0)], 9000)

    for track in (short, long):
        info = kit.editor.probe(kit.editor.mux(kit.sounding_video, track))
        assert info is not None and abs(info.duration_ms - 6000) <= TOLERANCE_MS
    with pytest.raises(AudioError):
        kit.editor.mux(kit.silent_video, kit.silent_video)  # 소리가 없는 것은 트랙이 아니다


# ---- 구현별: 조각이 제자리에 놓이는가 ----
def test_fake_track_carries_the_moved_speech():
    kit = _fake_kit()
    cut = TrackCut(0, 100, 2700, 4200)
    track = kit.editor.render_track([kit.recording], [cut], 8000)
    speech = kit.editor.speech_audio(track)
    from frame.adapters.speech import FakeSpeechRecognizer

    heard = FakeSpeechRecognizer().transcribe(speech)

    assert [(s.start_ms, s.end_ms, s.text) for s in heard.segments] == [
        (4300, 6700, "안녕하세요 오늘의 메뉴예요")
    ]
    assert kit.editor.tracks == [(cut,)]


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg 없음 (video extra 미설치)")
def test_ffmpeg_places_the_cut_at_its_target_time(tmp_path):
    """1초짜리 소리를 2초 위치에 놓으면 0~2초와 3~5초가 조용하다."""
    kit = _ffmpeg_kit(tmp_path)
    track = kit.editor.render_track([kit.recording], [TrackCut(0, 1000, 2000, 2000)], 5000)
    path = tmp_path / "track.wav"
    path.write_bytes(track)
    silences = detect_silences(FFMPEG, path, noise_db=-50.0, min_ms=300)

    assert len(silences) == 2
    (first_start, first_end), (second_start, _) = silences
    assert first_start <= 50 and abs(first_end - 2000) <= 60
    assert abs(second_start - 3000) <= 60


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg 없음 (video extra 미설치)")
def test_ffmpeg_tempo_shortens_the_cut(tmp_path):
    """2초짜리 소리를 1.25배 빠르게 놓으면 1.6초만 소리가 난다."""
    kit = _ffmpeg_kit(tmp_path)
    track = kit.editor.render_track([kit.recording], [TrackCut(0, 0, 2000, 0, tempo=1.25)], 4000)
    path = tmp_path / "track.wav"
    path.write_bytes(track)
    silences = detect_silences(FFMPEG, path, noise_db=-50.0, min_ms=300)

    assert len(silences) == 1 and abs(silences[0][0] - 1600) <= 80


@pytest.mark.parametrize(
    "cut",
    [
        {"tempo": 0.0},
        {"tempo": float("nan")},
        {"tempo": 3.0},
        {"source_end_ms": 500},  # 끝이 시작보다 앞
        {"source_start_ms": -1},
        {"target_start_ms": -100},
        {"source_index": -1},
    ],
)
def test_track_cut_is_checked_before_it_reaches_any_editor(cut):
    """조각의 검증은 포트에 있다. 가짜와 실제 편집기가 같은 입력에 다른 오류를 내지 않는다."""
    from frame.domain.errors import ValidationError

    base = {"source_index": 0, "source_start_ms": 1000, "source_end_ms": 2000, "target_start_ms": 0}
    with pytest.raises(ValidationError):
        TrackCut(**{**base, **cut})
