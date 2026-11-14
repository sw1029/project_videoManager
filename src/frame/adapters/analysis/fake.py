"""테스트·오프라인용 영상 분석 어댑터. 실제 영상 대신 '무엇이 들어 있는지' 를 적은 JSON 을 영상·음성처럼 다룬다.

- fake_video(...) / fake_recording(...) 가 만든 바이트(adapters/fake_media.py)에는 겉모습·컷 시각·말소리가 적혀 있다.
  가짜 분석기·인식기는 그것을 읽는다.
- 결정적: 같은 입력 -> 같은 결과.
- 실패 주입: error 를 주면 그 오류를 던진다. 스파이: calls 에 호출을 남긴다.
"""

from __future__ import annotations

from typing import Any

from frame.adapters.fake_media import decode_fake_media
from frame.application.ports.video_analysis import (
    MediaProber,
    ShotDetector,
    SpeechAudio,
    SpeechRecognizer,
    ThemeExtraction,
    ThemeExtractionRequest,
    ThemeExtractor,
    VideoAnalysisError,
)
from frame.domain.enums import EntityKind
from frame.domain.ir import EntitySpec, IntermediateRepresentation, SceneSpec
from frame.domain.shortform import MediaInfo
from frame.domain.theme import (
    CastMember,
    FormatSpec,
    NarrationBeat,
    NarrationSpec,
    SceneSlot,
    Theme,
    slot_spans_from_shots,
)
from frame.domain.video_analysis import Shot, Transcript, shots_from_cuts


def _video_part(video: bytes) -> dict[str, Any]:
    media = decode_fake_media(video)
    part = media.get("video") if media else None
    if not isinstance(part, dict):
        raise VideoAnalysisError("가짜 분석기는 fake_video() 로 만든 영상만 읽습니다")
    return part


class FakeMediaProber(MediaProber):
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self.calls = 0

    def probe(self, video: bytes) -> MediaInfo:
        self.calls += 1
        if self._error is not None:
            raise self._error
        part = _video_part(video)
        media = decode_fake_media(video) or {}
        return MediaInfo(
            width=int(part["width"]),
            height=int(part["height"]),
            duration_ms=int(part["duration_ms"]),
            fps=float(part["fps"]) if part.get("fps") is not None else None,
            has_audio=isinstance(media.get("audio"), dict),
            size_bytes=len(video),
            video_codec="fake",
        )


class FakeShotDetector(ShotDetector):
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self.calls = 0

    def detect(self, video: bytes, media: MediaInfo) -> tuple[Shot, ...]:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return shots_from_cuts(_video_part(video).get("cuts_ms") or (), media.duration_ms)


class FakeSpeechRecognizer(SpeechRecognizer):
    """가짜 음성에 적힌 말소리를 그대로 돌려준다. 그 밖의 바이트는 '말이 없음' 이다."""

    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self.calls: list[dict[str, Any]] = []

    def transcribe(
        self, audio: SpeechAudio, *, language: str | None = None, hint: str = ""
    ) -> Transcript:
        self.calls.append({"bytes": len(audio.data), "language": language, "hint": hint})
        if self._error is not None:
            raise self._error
        media = decode_fake_media(audio.data)
        part = media.get("audio") if media else None
        if not isinstance(part, dict) or not isinstance(part.get("transcript"), dict):
            return Transcript(language=language)
        transcript = Transcript.from_dict(part["transcript"])
        return Transcript(transcript.language or language, transcript.segments)


class FakeThemeExtractor(ThemeExtractor):
    """관찰값만으로 만든 가장 단순한 테마: 샷을 자리로 묶고, 모든 자리에 '주인공' 하나가 나온다.

    말소리가 있으면 자리마다 그 구간의 말을 내레이션의 예로 둔다. 화면 문구는 읽지 않는다.
    """

    ENTITY_KEY = "subject"

    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self.requests: list[ThemeExtractionRequest] = []

    def extract(self, request: ThemeExtractionRequest) -> ThemeExtraction:
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        analysis = request.analysis
        spans = slot_spans_from_shots(analysis.shots, min_slot_ms=request.min_slot_ms)
        keys = [f"slot_{i}" for i in range(1, len(spans) + 1)]
        slots = tuple(
            SceneSlot(key, end - start, f"장면 {i}", (self.ENTITY_KEY,))
            for i, (key, (start, end)) in enumerate(zip(keys, spans, strict=True), start=1)
        )
        transcript = analysis.transcript
        beats: list[NarrationBeat] = []
        if transcript is not None:
            for key, (start, end) in zip(keys, spans, strict=True):
                said = " ".join(s.text.strip() for s in transcript.within(start, end))
                if said:
                    beats.append(NarrationBeat(key, example=said))
        spoken = transcript is not None and not transcript.is_empty
        theme = Theme(
            theme_id=request.theme_id,
            title=request.title.strip() or request.source.title.strip() or "테마",
            source=request.source,
            format=FormatSpec(
                slots=slots,
                aspect_ratio=analysis.media.aspect_label,
                observed_shot_ms=tuple(s.duration_ms for s in analysis.shots),
            ),
            cast=(CastMember(self.ENTITY_KEY, EntityKind.OTHER, "주인공"),),
            narration=NarrationSpec(
                present=spoken,
                language=transcript.language if transcript is not None else None,
                pace_cps=transcript.pace_cps if transcript is not None else None,
                beats=tuple(beats),
            ),
        )
        source_ir = IntermediateRepresentation(
            entities=(EntitySpec(self.ENTITY_KEY, EntityKind.OTHER, "주인공"),),
            scenes=tuple(
                SceneSpec(key, start, end, slot.purpose, (self.ENTITY_KEY,))
                for key, (start, end), slot in zip(keys, spans, slots, strict=True)
            ),
        )
        return ThemeExtraction(theme, source_ir, detail={"method": "fake"})
