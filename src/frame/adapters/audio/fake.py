"""테스트·오프라인용 소리 편집기. 가짜 영상·녹음(adapters/fake_media.py)에 적힌 말소리를 잘라 옮긴다.

만든 트랙에는 옮겨진 말소리가 그대로 적힌다. 그래서 가짜 인식기로 다시 읽어
'녹음이 제자리에 놓였는지' 를 확인할 수 있다.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Any

from frame.adapters.fake_media import FAKE_MEDIA_TYPE, decode_fake_media, encode_fake_media
from frame.application.ports.audio import AudioEditor, AudioError, AudioInfo, TrackCut
from frame.application.ports.video_analysis import SpeechAudio
from frame.domain.video_analysis import SpeechSegment, Transcript, Word


class FakeAudioEditor(AudioEditor):
    track_suffix = ".json"

    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self.tracks: list[tuple[TrackCut, ...]] = []  # 스파이: 만든 트랙의 조각들

    def probe(self, media: bytes) -> AudioInfo | None:
        part = _audio_part(media)
        if part is None:
            return None
        return AudioInfo(int(part["duration_ms"]), sample_rate=16000, channels=1, codec="fake")

    def speech_audio(self, media: bytes) -> SpeechAudio | None:
        part = _audio_part(media)
        if part is None:
            return None
        return SpeechAudio(
            encode_fake_media({"audio": part}), FAKE_MEDIA_TYPE, int(part["duration_ms"])
        )

    def render_track(
        self, sources: Sequence[bytes], cuts: Sequence[TrackCut], total_ms: int
    ) -> bytes:
        if self._error is not None:
            raise self._error
        self.tracks.append(tuple(cuts))
        segments: list[SpeechSegment] = []
        for cut in sorted(cuts, key=lambda c: c.target_start_ms):
            if not 0 <= cut.source_index < len(sources):
                raise AudioError(f"조각이 가리키는 녹음이 없습니다: {cut.source_index}")
            part = _audio_part(sources[cut.source_index])
            if part is None:
                raise AudioError(f"녹음 {cut.source_index} 에 소리가 없습니다")
            transcript = Transcript.from_dict(part.get("transcript") or {})
            segments += _moved(transcript, cut, total_ms)
        audio: dict[str, Any] = {
            "duration_ms": total_ms,
            "transcript": Transcript("ko", tuple(segments)).to_dict(),
            "cuts": [
                {
                    "source_index": c.source_index,
                    "source_start_ms": c.source_start_ms,
                    "source_end_ms": c.source_end_ms,
                    "target_start_ms": c.target_start_ms,
                    "tempo": round(c.tempo, 4),
                }
                for c in cuts
            ],
        }
        return encode_fake_media({"audio": audio})

    def mux(self, video: bytes, track: bytes) -> bytes:
        if self._error is not None:
            raise self._error
        part = _audio_part(track)
        if part is None:
            raise AudioError("붙일 트랙에 소리가 없습니다")
        media = decode_fake_media(video)
        picture = media.get("video") if media else None
        if not isinstance(picture, dict):  # 가짜 조립기의 목록 같은 다른 산출물: 지문만 남긴다
            picture = {"opaque_sha256": hashlib.sha256(video).hexdigest()}
        elif "duration_ms" in picture:  # 소리의 길이는 영상의 길이에 맞춘다
            part = _fitted(part, int(picture["duration_ms"]))
        return encode_fake_media({"video": picture, "audio": part})


def _audio_part(media: bytes) -> dict[str, Any] | None:
    decoded = decode_fake_media(media)
    part = decoded.get("audio") if decoded else None
    return part if isinstance(part, dict) and "duration_ms" in part else None


def _fitted(part: dict[str, Any], duration_ms: int) -> dict[str, Any]:
    """소리를 duration_ms 길이에 맞춘다: 짧으면 무음으로 늘어나고, 길면 넘는 말소리가 잘린다."""
    transcript = Transcript.from_dict(part.get("transcript") or {})
    kept = tuple(
        SpeechSegment(s.start_ms, min(s.end_ms, duration_ms), s.text, s.words)
        for s in transcript.segments
        if s.start_ms < duration_ms
    )
    fitted = dict(part)
    fitted["duration_ms"] = duration_ms
    fitted["transcript"] = Transcript(transcript.language, kept).to_dict()
    return fitted


def _moved(transcript: Transcript, cut: TrackCut, total_ms: int) -> list[SpeechSegment]:
    """조각 안에 든 말소리를 트랙의 시각으로 옮긴다 (빠르기 반영, 트랙 끝을 넘는 것은 버린다)."""

    def shift(t_ms: int) -> int:
        return cut.target_start_ms + round((t_ms - cut.source_start_ms) / cut.tempo)

    moved: list[SpeechSegment] = []
    for segment in transcript.segments:
        middle = (segment.start_ms + segment.end_ms) / 2
        if not cut.source_start_ms <= middle < cut.source_end_ms:
            continue
        start = shift(max(segment.start_ms, cut.source_start_ms))
        end = shift(min(segment.end_ms, cut.source_end_ms))
        if start >= total_ms:
            continue
        words = tuple(
            Word(shift(w.start_ms), min(total_ms, shift(w.end_ms)), w.text)
            for w in segment.words
            if cut.source_start_ms <= (w.start_ms + w.end_ms) / 2 < cut.source_end_ms
            and shift(w.start_ms) < total_ms
        )
        moved.append(SpeechSegment(start, min(total_ms, end), segment.text, words))
    return moved
