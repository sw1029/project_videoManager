"""영상 한 편에서 테마를 뽑아 저장한다 (US-06, ADR-0025).

순서
1. 겉모습을 읽고 숏폼 규격을 판정한다. 기준 밖이면 NotShortform 으로 거절한다 (require_shortform 이 참일 때).
2. 컷을 찾아 샷으로 나눈다.
3. 소리가 있으면 무음 구간을 재고 말소리를 받아 적는다 (use_cases/script 의 listen: 처음부터 끝까지 무음이면
   인식기를 부르지 않고, 줄이 없는 소리는 그 구간만 다시 받아 적는다). 인식하지 못해도 추출은 계속한다
   (메모만 남긴다): 말소리는 테마의 한 부분이지 전제 조건이 아니다.
4. 자리의 경계를 정한다: 컷을 묶되 문장 중간을 자르지 않고, 상한이 있으면 긴 자리를 말 멈춤에서 나눈다 (ADR-0028).
5. 추출기가 테마와 원본 IR 을 만든다. 계약을 다시 확인한 뒤에만 저장한다.
6. 받아쓰기를 대본으로 다듬어 화면에서 읽은 것과 견준다: 변수·화면 문구의 근거, 어긋남, 내레이션 규칙,
   말을 따르는 자막 규칙 (ADR-0028). 모델을 더 부르지 않는다. 대본과 근거는 테마의 기록에 함께 둔다.
   대본에서 옮긴 테마는 다시 검사한다. 이 단계가 실패하면 5의 추출 결과를 그대로 저장하고 메모를 남긴다
   (유료 추출 뒤의 순수 단계가 그 결과를 버리지 않는다 — 감사 BTS-D62).

같은 입력의 유료 호출은 한 번 (감사 BTS-12)
- 같은 영상(sha256)을 같은 설정(추출기의 지문·인식기의 정체·언어·자리 길이의 하한과 상한)으로 뽑은 테마가 있으면
  그것을 돌려준다.
  인식도 추출도 다시 부르지 않고 테마가 둘 생기지 않는다. 다시 뽑으려면 force=True.
- 인식 결과는 인식기 쪽에서 남긴다 (CachingSpeechRecognizer). 추출이 실패해 다시 불러도 인식은 다시 과금되지 않는다.
- 인식 서비스를 쓸 수 없으면(키 없음·인증·한도·연결) 추출로 넘어가지 않고 멈춘다. 알아듣지 못한 경우에만
  내레이션 없이 계속한다 (감사 BTS-29).

영상의 보관 (ADR-0025)
- 외부 영상은 저장소에 사본을 두지 않는다. 지문(sha256)·겉모습·출처·라이선스만 기록한다.
- FRAME 이 만든 영상은 사본을 둘 수 있다 (keep_source). 기본은 두지 않는다.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace

from frame.application.errors import AdapterError
from frame.application.ports.artifact_store import ArtifactStore
from frame.application.ports.common import Clock, IdGenerator
from frame.application.ports.series.audio import AudioEditor
from frame.application.ports.series.repositories import ThemeRecord, ThemeRepository
from frame.application.ports.series.source_analysis import MediaProber, ShotDetector
from frame.application.ports.series.theme_extraction import (
    ThemeExtraction,
    ThemeExtractionError,
    ThemeExtractionRequest,
    ThemeExtractor,
)
from frame.application.ports.sound import SoundProfile
from frame.application.ports.speech import SpeechRecognizer, SpeechServiceError
from frame.application.use_cases.script import MAX_MEDIA_BYTES, MAX_MEDIA_MS, listen
from frame.domain.enums import EntityKind
from frame.domain.errors import DomainError, ValidationError
from frame.domain.ir import IntermediateRepresentation
from frame.domain.script import SceneSpan, SpokenScript, build_script, classify_kinds
from frame.domain.series.script_evidence import ScriptEvidence, apply_script, link_script
from frame.domain.series.shortform import ShortformPlatform, ShortformVerdict, check_platforms
from frame.domain.series.source_analysis import VideoAnalysis
from frame.domain.series.theme import (
    DEFAULT_MIN_SLOT_MS,
    SourceOrigin,
    SourceVideo,
    Theme,
    slot_spans_from_shots,
)
from frame.domain.speech import Transcript

log = logging.getLogger(__name__)


# 겉모습을 읽기 전에 보는 크기 상한과, 숏폼 판정을 꺼도 넘지 못하는 길이 상한 (감사 BTS-30).
# 대본 추출(use_cases/script)과 같은 값이다. 숏폼의 길이는 숏폼 규격 판정이 따로 본다
MAX_SOURCE_BYTES = MAX_MEDIA_BYTES
MAX_SOURCE_MS = MAX_MEDIA_MS


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
    force: bool = False  # 같은 영상·같은 설정의 테마가 있어도 다시 뽑는다 (과금)


@dataclass(frozen=True)
class ExtractThemeResult:
    record: ThemeRecord
    verdict: ShortformVerdict
    reused: bool = False  # 앞서 뽑은 테마를 돌려줬다 (이번에는 인식·추출을 부르지 않았다)


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
        max_slot_ms: int | None = None,
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
        self._max_slot_ms = max_slot_ms

    def execute(self, command: ExtractThemeCommand) -> ExtractThemeResult:
        if not command.video:
            raise ValidationError("영상이 비어 있습니다")
        if len(command.video) > MAX_SOURCE_BYTES:
            raise ValidationError(
                f"영상이 너무 큽니다: {len(command.video) / 1_048_576:.0f}MB "
                f"(상한 {MAX_SOURCE_BYTES // 1_048_576}MB)"
            )
        if command.keep_source and command.origin is SourceOrigin.EXTERNAL:
            raise ValidationError(
                "외부 영상은 저장소에 사본을 두지 않습니다 (keep_source 를 끄세요)"
            )
        media = self._prober.probe(command.video)
        if media.duration_ms > MAX_SOURCE_MS:  # require_shortform 을 꺼도 지킨다
            raise ValidationError(
                f"영상이 너무 깁니다: {media.duration_ms / 60_000:.0f}분 "
                f"(상한 {MAX_SOURCE_MS // 60_000}분)"
            )
        verdict = check_platforms(media, self._platforms)
        if command.require_shortform and not verdict.within:
            reasons = [v.message for report in verdict.reports for v in report.violations]
            raise NotShortform(
                "숏폼 규격 밖의 영상입니다: " + "; ".join(reasons), report=verdict.to_dict()
            )
        sha256 = hashlib.sha256(command.video).hexdigest()
        if not command.force:
            known = self._known(sha256, command)
            if known is not None:
                if command.keep_source and known.theme.source.stored_uri is None:
                    known = self._with_copy(known, command.video)
                return ExtractThemeResult(known, verdict, reused=True)
        shots = self._shot_detector.detect(command.video, media)
        notes: list[str] = []
        profile = self._profile(command, media.has_audio, notes)
        transcript = self._transcribe(command, media.has_audio, profile, notes)
        analysis = VideoAnalysis(media, shots, transcript)
        analysis.validate()

        theme_id = self._ids.new_id("thm")
        source = SourceVideo(
            sha256=sha256,
            media=media,
            origin=command.origin,
            title=command.title.strip(),
            source_url=command.source_url,
            license=command.license,
            attribution=command.attribution,
        )
        request = ThemeExtractionRequest(
            theme_id=theme_id,
            source=source,
            video=command.video,
            analysis=analysis,
            title=command.title.strip(),
            min_slot_ms=self._min_slot_ms,
            slot_spans=self._slot_spans(analysis, profile),
        )
        extraction = self._extractor.extract(request)
        _check_contract(extraction.theme, extraction.source_ir, request)
        theme, script, evidence = _linked(extraction, analysis.transcript, profile, notes)
        if evidence is not None and evidence.discrepancies:
            notes.append(
                f"화면에서 읽은 것과 말이 어긋난 곳이 {len(evidence.discrepancies)}건 있습니다"
            )
        if command.keep_source:  # 사본은 추출이 끝난 뒤에 둔다 (실패하면 주인 없는 사본이 남는다)
            stored_uri = self._store.put(
                f"themes/{theme_id}/source.mp4", command.video, "video/mp4"
            )
            theme = replace(theme, source=replace(theme.source, stored_uri=stored_uri))
        record = ThemeRecord(
            theme=theme,
            source_ir=extraction.source_ir,
            created_at=self._clock.now(),
            analysis=analysis,
            screen_texts=tuple(extraction.screen_texts),
            shortform=verdict.to_dict(),
            script=script,
            evidence=evidence,
            notes=(*notes, *extraction.notes),
            detail={**extraction.detail, **self._settings_of(command)},
        )
        self._themes.save(record)
        return ExtractThemeResult(record, verdict)

    def _with_copy(self, record: ThemeRecord, video: bytes) -> ThemeRecord:
        """이미 뽑은 테마에 영상의 사본만 붙인다. 다시 뽑지 않는다 (감사 BTS-D09).

        사본이 없다는 까닭으로 다시 뽑으면 추출이 다시 과금되고 테마가 둘 생긴다.
        """
        stored_uri = self._store.put(
            f"themes/{record.theme.theme_id}/source.mp4", video, "video/mp4"
        )
        source = replace(record.theme.source, stored_uri=stored_uri)
        updated = replace(record, theme=replace(record.theme, source=source))
        self._themes.save(updated)
        return updated

    def _settings_of(self, command: ExtractThemeCommand) -> dict[str, object]:
        """결과를 바꿀 수 있는 설정. 같은 영상을 다시 뽑을지 정할 때 견준다."""
        return {
            "extractor": self._extractor.signature,
            "language": command.language,
            "min_slot_ms": self._min_slot_ms,
            "max_slot_ms": self._max_slot_ms,
            # 인식기의 정체 (모델·주소, 로컬은 모델의 지문). 켬/끔만 견주면 가짜로 뽑은 테마가 실제 인식기로
            # 바꾼 뒤에도 돌아온다 (감사 BTS-D65)
            "speech": self._recognizer.signature if self._recognizer is not None else None,
        }

    def _known(self, sha256: str, command: ExtractThemeCommand) -> ThemeRecord | None:
        """같은 영상을 같은 설정으로 뽑은 테마 (가장 나중 것). 없으면 None."""
        wanted = self._settings_of(command)
        for record in reversed(self._themes.list()):
            source = record.theme.source
            if source.sha256 != sha256 or source.origin is not command.origin:
                continue
            if all(record.detail.get(key) == value for key, value in wanted.items()):
                return record
        return None

    def _slot_spans(
        self, analysis: VideoAnalysis, profile: SoundProfile | None
    ) -> tuple[tuple[int, int], ...]:
        """자리의 구간: 컷을 묶되 문장 중간을 자르지 않고, 상한이 있으면 긴 자리를 말 멈춤에서 나눈다.

        말한 구간은 잰 무음에 맞춘 것을 쓴다 (인식기의 시각은 무음 쪽으로 어긋난다).
        """
        spoken = build_script(
            analysis.transcript,
            duration_ms=profile.duration_ms if profile is not None else None,
            silences=profile.silences if profile is not None else None,
        ).lines
        return slot_spans_from_shots(
            analysis.shots,
            min_slot_ms=self._min_slot_ms,
            speech=[(line.start_ms, line.end_ms) for line in spoken],
            max_slot_ms=self._max_slot_ms,
        )

    def _profile(
        self, command: ExtractThemeCommand, has_audio: bool, notes: list[str]
    ) -> SoundProfile | None:
        """소리의 길이와 무음 구간 (무료). 재지 못해도 추출은 계속한다."""
        if not has_audio:
            return None
        try:
            return self._audio.sound_profile(command.video)
        except AdapterError as exc:
            log.warning("소리를 훑지 못했습니다: %s: %s", type(exc).__name__, exc)
            notes.append(f"무음 구간을 재지 못했습니다 ({exc.code})")
            return None

    def _transcribe(
        self,
        command: ExtractThemeCommand,
        has_audio: bool,
        profile: SoundProfile | None,
        notes: list[str],
    ) -> Transcript | None:
        """말소리를 받아 적는다. 무음뿐이면 부르지 않고, 줄이 없는 소리는 다시 받아 적는다 (listen)."""
        if not has_audio or self._recognizer is None:
            return None
        try:
            heard = listen(
                self._audio, self._recognizer, command.video, profile, language=command.language
            )
            notes += heard.notes
            return heard.transcript
        except SpeechServiceError:
            raise  # 설정·환경의 문제: 내레이션 없는 테마를 돈 들여 만들지 않는다
        except (AdapterError, ValidationError) as exc:
            # 메모는 테마 문서에 저장된다. 오류 코드만 남기고 원문(서버가 준 글·경로)은 로그로 보낸다
            code = getattr(exc, "code", type(exc).__name__)
            log.warning("말소리를 인식하지 못했습니다: %s: %s", type(exc).__name__, exc)
            notes.append(f"말소리를 인식하지 못해 내레이션 없이 뽑았습니다 ({code})")
            return None


def _script(
    transcript: Transcript | None,
    profile: SoundProfile | None,
    theme: Theme,
    source_ir: IntermediateRepresentation,
) -> SpokenScript:
    """받아쓰기를 테마의 자리에 맞춘 대본으로 다듬는다. 말이 없으면 줄이 빈 대본이다."""
    scenes: list[SceneSpan] = []
    cursor = 0
    for slot in theme.format.slots:
        scenes.append(SceneSpan(slot.slot_key, cursor, cursor + slot.duration_ms))
        cursor += slot.duration_ms
    people = {e.entity_key for e in source_ir.entities if e.kind is EntityKind.PERSON}
    # 말할 사람이 화면에 없는 자리의 줄은 내레이션이다. 사람이 보이는 자리는 소리만으로 가르지 못한다
    visible = {s.scene_key: bool(people & set(s.entity_keys)) for s in source_ir.scenes}
    script = build_script(
        transcript,
        scenes,
        duration_ms=profile.duration_ms if profile is not None else None,
        silences=profile.silences if profile is not None else None,
    )
    return classify_kinds(script, visible)


# 대본을 옮기는 단계(순수 계산)에서 날 수 있는 오류. 이 단계가 실패해도 추출 결과는 저장한다
_LINK_ERRORS = (DomainError, ArithmeticError, LookupError, TypeError, ValueError)


def _linked(
    extraction: ThemeExtraction,
    transcript: Transcript | None,
    profile: SoundProfile | None,
    notes: list[str],
) -> tuple[Theme, SpokenScript | None, ScriptEvidence | None]:
    """대본을 근거로 쓴 테마: 화면에서 읽은 것과 견주고 내레이션 규칙을 잰다 (모델을 부르지 않는다).

    옮긴 테마는 다시 검사한다 (추출기의 결과만 검사하면 대본에서 온 값이 규칙을 깨도 그대로 저장된다).
    이 단계가 실패하면 검사를 통과한 추출 결과를 그대로 돌려주고 메모를 남긴다: 저장하지 않으면 다시 부를 때
    추출이 다시 과금된다 (감사 BTS-D62).
    """
    theme = extraction.theme
    script: SpokenScript | None = None
    evidence: ScriptEvidence | None = None
    try:
        script = _script(transcript, profile, theme, extraction.source_ir)
        evidence = link_script(theme, script, extraction.screen_texts)
        if not script.is_empty:
            linked = apply_script(theme, script, evidence)
            linked.validate()
            theme = linked
    except _LINK_ERRORS as exc:
        log.warning("대본을 테마에 옮기지 못했습니다: %s: %s", type(exc).__name__, exc)
        notes.append(
            "대본을 테마에 옮기지 못해 추출 결과만 저장했습니다 "
            f"(SCRIPT_LINK_FAILED: {type(exc).__name__})"
        )
    return theme, script, evidence


def _check_contract(
    theme: Theme, source_ir: IntermediateRepresentation, request: ThemeExtractionRequest
) -> None:
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
