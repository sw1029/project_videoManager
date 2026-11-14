"""영상 한 편에서 테마를 뽑아 저장한다 (US-06, ADR-0025).

순서
1. 겉모습을 읽고 숏폼 규격을 판정한다. 기준 밖이면 NotShortform 으로 거절한다 (require_shortform 이 참일 때).
2. 컷을 찾아 샷으로 나눈다.
3. 소리가 있으면 말소리를 받아 적는다. 인식하지 못해도 추출은 계속한다 (메모만 남긴다):
   말소리는 테마의 한 부분이지 전제 조건이 아니다.
4. 추출기가 테마와 원본 IR 을 만든다. 계약을 다시 확인한 뒤에만 저장한다.

영상의 보관 (ADR-0025)
- 외부 영상은 저장소에 사본을 두지 않는다. 지문(sha256)·겉모습·출처·라이선스만 기록한다.
- FRAME 이 만든 영상은 사본을 둘 수 있다 (keep_source). 기본은 두지 않는다.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass

from frame.domain.errors import ValidationError
from frame.domain.shortform import ShortformPlatform, ShortformVerdict, check_platforms
from frame.domain.theme import DEFAULT_MIN_SLOT_MS, SourceOrigin, SourceVideo
from frame.domain.video_analysis import Transcript, VideoAnalysis

from ..errors import AdapterError
from ..ports.artifact_store import ArtifactStore
from ..ports.audio import AudioEditor
from ..ports.common import Clock, IdGenerator
from ..ports.theme_store import ThemeRecord, ThemeRepository
from ..ports.video_analysis import (
    MediaProber,
    ShotDetector,
    SpeechRecognizer,
    ThemeExtractionError,
    ThemeExtractionRequest,
    ThemeExtractor,
)

log = logging.getLogger(__name__)


class NotShortform(ValidationError):
    """영상이 숏폼 규격 밖이다 (API 422). context 의 report 에 기준별 판정이 있다."""

    code = "NOT_SHORTFORM"


@dataclass(frozen=True)
class ExtractThemeCommand:
    video: bytes
    title: str = ""
    origin: SourceOrigin = SourceOrigin.EXTERNAL
    source_url: str | None = None
    license: str | None = None
    attribution: str | None = None
    language: str | None = None  # 말소리의 언어 (알면 인식이 안정된다)
    require_shortform: bool = True  # 기준 밖의 영상은 거절한다
    keep_source: bool = False  # 저장소에 영상의 사본을 둔다 (FRAME 이 만든 영상만)


@dataclass(frozen=True)
class ExtractThemeResult:
    record: ThemeRecord
    verdict: ShortformVerdict


class ExtractTheme:
    def __init__(
        self,
        themes: ThemeRepository,
        store: ArtifactStore,
        prober: MediaProber,
        shot_detector: ShotDetector,
        audio: AudioEditor,
        recognizer: SpeechRecognizer | None,
        extractor: ThemeExtractor,
        ids: IdGenerator,
        clock: Clock,
        *,
        platforms: Sequence[ShortformPlatform] = tuple(ShortformPlatform),
        min_slot_ms: int = DEFAULT_MIN_SLOT_MS,
    ) -> None:
        self._themes = themes
        self._store = store
        self._prober = prober
        self._shot_detector = shot_detector
        self._audio = audio
        self._recognizer = recognizer
        self._extractor = extractor
        self._ids = ids
        self._clock = clock
        self._platforms = tuple(platforms)
        self._min_slot_ms = min_slot_ms

    def execute(self, command: ExtractThemeCommand) -> ExtractThemeResult:
        if not command.video:
            raise ValidationError("영상이 비어 있습니다")
        if command.keep_source and command.origin is SourceOrigin.EXTERNAL:
            raise ValidationError(
                "외부 영상은 저장소에 사본을 두지 않습니다 (keep_source 를 끄세요)"
            )
        media = self._prober.probe(command.video)
        verdict = check_platforms(media, self._platforms)
        if command.require_shortform and not verdict.within:
            reasons = [v.message for report in verdict.reports for v in report.violations]
            raise NotShortform(
                "숏폼 규격 밖의 영상입니다: " + "; ".join(reasons), report=verdict.to_dict()
            )
        shots = self._shot_detector.detect(command.video, media)
        notes: list[str] = []
        analysis = VideoAnalysis(media, shots, self._transcribe(command, media.has_audio, notes))
        analysis.validate()

        theme_id = self._ids.new_id("thm")
        stored_uri = None
        if command.keep_source:
            stored_uri = self._store.put(
                f"themes/{theme_id}/source.mp4", command.video, "video/mp4"
            )
        source = SourceVideo(
            sha256=hashlib.sha256(command.video).hexdigest(),
            media=media,
            origin=command.origin,
            title=command.title.strip(),
            source_url=command.source_url,
            license=command.license,
            attribution=command.attribution,
            stored_uri=stored_uri,
        )
        request = ThemeExtractionRequest(
            theme_id=theme_id,
            source=source,
            video=command.video,
            analysis=analysis,
            title=command.title.strip(),
            min_slot_ms=self._min_slot_ms,
        )
        extraction = self._extractor.extract(request)
        _check_contract(extraction.theme, extraction.source_ir, request)
        record = ThemeRecord(
            theme=extraction.theme,
            source_ir=extraction.source_ir,
            created_at=self._clock.now(),
            analysis=analysis,
            screen_texts=tuple(extraction.screen_texts),
            shortform=verdict.to_dict(),
            notes=(*notes, *extraction.notes),
            detail=dict(extraction.detail),
        )
        self._themes.save(record)
        return ExtractThemeResult(record, verdict)

    def _transcribe(
        self, command: ExtractThemeCommand, has_audio: bool, notes: list[str]
    ) -> Transcript | None:
        if not has_audio or self._recognizer is None:
            return None
        try:
            speech = self._audio.speech_audio(command.video)
            if speech is None:
                return None
            transcript = self._recognizer.transcribe(speech, language=command.language)
            transcript.validate()
            return transcript
        except (AdapterError, ValidationError) as exc:
            log.warning("말소리를 인식하지 못했습니다: %s: %s", type(exc).__name__, exc)
            notes.append(f"말소리를 인식하지 못해 내레이션 없이 뽑았습니다: {exc}")
            return None


def _check_contract(theme, source_ir, request: ThemeExtractionRequest) -> None:  # type: ignore[no-untyped-def]
    """추출기가 포트의 계약을 지켰는지 본다. 어긴 결과는 저장하지 않는다."""
    try:
        theme.validate()
        source_ir.validate()
    except ValidationError as exc:
        raise ThemeExtractionError(f"추출 결과가 규칙에 맞지 않습니다: {exc.message}") from exc
    if theme.theme_id != request.theme_id or theme.source != request.source:
        raise ThemeExtractionError("추출 결과의 id·출처가 요청과 다릅니다")
    slots = [(s.slot_key, s.duration_ms) for s in theme.format.slots]
    scenes = [(s.scene_key, s.duration_ms) for s in source_ir.scenes]
    if slots != scenes:
        raise ThemeExtractionError("원본 IR 의 장면이 테마의 자리와 다릅니다")
    known = {e.entity_key for e in source_ir.entities}
    unknown = sorted(c.entity_key for c in theme.cast if c.entity_key not in known)
    if unknown:
        raise ThemeExtractionError(f"고정 출연이 원본 IR 에 없습니다: {unknown}")
