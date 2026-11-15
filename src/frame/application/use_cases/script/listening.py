"""소리를 받아 적는다 — 인식기를 부르기 전과 뒤에 소리를 본다 (ADR-0028).

대본 추출(ExtractScript)과 테마 추출(ExtractTheme)이 함께 쓴다. 인식기는 말이 없는 소리에서 문장을 지어내고,
긴 무음 뒤의 말을 통째로 빠뜨리기도 한다 (오픈 데이터의 말소리로 잰 것: docs/research/theme-series.md §8.7).

1. 처음부터 끝까지 무음이면 받아 적지 않는다. 지어낸 문장도 과금도 없다.
2. 전체를 한 번 받아 적는다.
3. 줄이 하나도 걸치지 않은 소리가 있으면 그 구간만 잘라 다시 받아 적는다 (놓친 말을 되찾는다).
   - 그 구간이 소리의 대부분이면 다시 받지 않는다 (음악이 내내 깔린 영상: 같은 소리를 두 번 받을 뿐이다).
   - 다시 받은 말은 믿을 만할 때만 쓴다 (신뢰도). 말이 없는 소리만 잘라 주면 인식기는 또 지어낸다.
   - 구간 수에 상한이 있다. 과금은 그 구간의 길이만큼만 는다.
   - 다시 받아 적기는 덤이다. 그 구간을 자르거나 받아 적다 실패하면 메모만 남기고 처음 받은 받아쓰기를 돌려준다
     (전체의 실패로 올리면 부른 쪽이 멀쩡한 받아쓰기를 버리고 내레이션 없이 유료 단계로 간다 — 감사 BTS-D12·D31).
     인식 서비스를 쓸 수 없다는 오류(SpeechServiceError)는 그대로 올린다.
무음을 재지 못했으면(profile 이 None) 2 만 한다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace

from frame.application.errors import AdapterError
from frame.application.ports.sound import SoundProfile, SoundReader
from frame.application.ports.speech import SpeechRecognizer, SpeechServiceError
from frame.domain.script import DEFAULT_SCRIPT_POLICY, ScriptPolicy, audible_spans, build_script
from frame.domain.speech import SpeechSegment, Transcript


@dataclass(frozen=True)
class ListeningPolicy:
    again_min_ms: int = 700  # 줄이 없는 소리가 이만큼은 돼야 다시 받아 적는다
    again_pad_ms: int = 200  # 잘라 낼 때 앞뒤에 남기는 여유
    again_limit: int = 3  # 다시 받아 적는 구간 수의 상한 (긴 것부터)
    again_max_share: float = 0.8  # 그 구간이 소리 길이의 이 비율을 넘으면 다시 받지 않는다
    # 다시 받은 말을 믿는 기준. 실제 말소리의 신뢰도는 0.52 이상(중앙값 0.77), 지어낸 문장은 0.45 안팎이었다
    again_confidence: float = 0.6


log = logging.getLogger(__name__)
DEFAULT_LISTENING_POLICY = ListeningPolicy()
ALL_SILENT = "소리가 처음부터 끝까지 무음이라 받아 적지 않았습니다"


@dataclass(frozen=True)
class Heard:
    transcript: Transcript | None  # 받아 적지 않았으면 None (처음부터 끝까지 무음)
    notes: tuple[str, ...] = ()


def listen(
    sound: SoundReader,
    recognizer: SpeechRecognizer,
    media: bytes,
    profile: SoundProfile | None,
    *,
    language: str | None = None,
    hint: str = "",
    script_policy: ScriptPolicy = DEFAULT_SCRIPT_POLICY,
    policy: ListeningPolicy = DEFAULT_LISTENING_POLICY,
) -> Heard:
    """media 의 말소리를 받아 적는다. profile 은 잰 소리의 겉모습이다 (재지 못했으면 None).

    소리가 없는 파일이면 받아쓰기가 None 이다. 인식 서비스의 오류는 그대로 올린다.
    """
    sounding = (
        audible_spans(profile.duration_ms, profile.silences, script_policy.min_island_ms)
        if profile is not None
        else None
    )
    if sounding is not None and not sounding:
        return Heard(None, (ALL_SILENT,))
    speech = sound.speech_audio(media)
    if speech is None:
        return Heard(None)
    transcript = recognizer.transcribe(speech, language=language, hint=hint)
    transcript.validate(profile.duration_ms if profile is not None else None)
    if profile is None or sounding is None:
        return Heard(transcript)
    lines = build_script(
        transcript, duration_ms=profile.duration_ms, silences=profile.silences, policy=script_policy
    ).lines
    unheard = [
        (start, end)
        for start, end in sounding
        if policy.again_min_ms <= end - start <= profile.duration_ms * policy.again_max_share
        and not any(line.start_ms < end and line.end_ms > start for line in lines)
    ]
    notes: list[str] = []
    found: list[SpeechSegment] = []
    for start, end in sorted(unheard, key=lambda span: span[0] - span[1])[: policy.again_limit]:
        cut = max(0, start - policy.again_pad_ms)
        span = f"{start / 1000:.1f}~{end / 1000:.1f}초"
        try:
            part = sound.speech_audio(
                media, start_ms=cut, end_ms=min(profile.duration_ms, end + policy.again_pad_ms)
            )
            if part is None:
                continue
            again = recognizer.transcribe(part, language=language, hint=hint)
        except SpeechServiceError:
            raise  # 설정·환경의 문제는 덮지 않는다
        except AdapterError as exc:
            # 메모는 저장되는 문서로 간다. 오류 코드만 적고 원문(서버가 준 글·경로)은 로그로 보낸다
            log.warning(
                "%s 의 소리를 다시 받아 적지 못했습니다: %s: %s", span, type(exc).__name__, exc
            )
            notes.append(f"{span}의 소리를 다시 받아 적지 못했습니다 ({exc.code})")
            continue
        believed = [
            _moved(segment, cut, profile.duration_ms)
            for segment in again.segments
            if segment.text.strip()
            and (segment.confidence is None or segment.confidence >= policy.again_confidence)
        ]
        if believed:
            found += believed
            notes.append(
                f"{span}의 소리는 따로 잘라 다시 받아 적었습니다 (전체를 받아 적을 때 빠졌습니다)"
            )
    if not found:
        return Heard(transcript, tuple(notes))
    return Heard(Transcript(transcript.language, _merged(transcript.segments, found)), tuple(notes))


def _moved(segment: SpeechSegment, offset_ms: int, duration_ms: int) -> SpeechSegment:
    """잘라 낸 소리 안의 시각을 원래 소리의 시각으로 옮긴다."""
    words = tuple(
        replace(word, start_ms=word.start_ms + offset_ms, end_ms=word.end_ms + offset_ms)
        for word in segment.words
    )
    start = min(duration_ms, segment.start_ms + offset_ms)
    return replace(
        segment, start_ms=start, end_ms=min(duration_ms, segment.end_ms + offset_ms), words=words
    )


def _merged(
    first: tuple[SpeechSegment, ...], again: list[SpeechSegment]
) -> tuple[SpeechSegment, ...]:
    """처음 받은 구간에 다시 받은 구간을 시간 순서로 끼운다. 겹치면 뒤의 것을 앞의 끝에서 시작하게 한다."""
    merged: list[SpeechSegment] = []
    for segment in sorted([*first, *again], key=lambda s: (s.start_ms, s.end_ms)):
        start = max(segment.start_ms, merged[-1].end_ms) if merged else segment.start_ms
        if segment.end_ms <= start:
            continue  # 앞의 구간에 다 덮였다
        words = tuple(w for w in segment.words if w.start_ms >= start)
        merged.append(replace(segment, start_ms=start, words=words))
    return tuple(merged)
