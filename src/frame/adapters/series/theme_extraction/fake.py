"""테스트·오프라인용 테마 추출. 관찰값만으로 가장 단순한 테마를 만든다.

샷을 자리로 묶고, 모든 자리에 '주인공' 하나가 나온다. 말소리가 있으면 자리마다 그 구간의 말을 내레이션의 예로 둔다.
"""

from __future__ import annotations

from frame.application.ports.series.theme_extraction import (
    ThemeExtraction,
    ThemeExtractionRequest,
    ThemeExtractor,
)
from frame.domain.enums import EntityKind
from frame.domain.ir import EntitySpec, IntermediateRepresentation, SceneSpec
from frame.domain.series.theme import (
    CastMember,
    FormatSpec,
    NarrationBeat,
    NarrationSpec,
    SceneSlot,
    Theme,
    slot_spans_from_shots,
)


class FakeThemeExtractor(ThemeExtractor):
    """관찰값만으로 만든 가장 단순한 테마: 샷을 자리로 묶고, 모든 자리에 '주인공' 하나가 나온다.

    말소리가 있으면 자리마다 그 구간의 말을 내레이션의 예로 둔다. 화면 문구는 읽지 않는다.
    """

    ENTITY_KEY = "subject"

    def __init__(self, error: Exception | None = None, signature: str = "fake") -> None:
        self._error = error
        self._signature = signature
        self.requests: list[ThemeExtractionRequest] = []

    @property
    def signature(self) -> str:
        return self._signature

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
