"""받아쓰기를 대본으로 다듬는다 (ADR-0028). 순수 함수.

- 줄: 인식기가 준 구간이 기본이다. 낱말 시각이 있으면 낱말 사이가 오래 빈 곳(split_pause_ms)에서 더 나눈다.
  나눈 줄의 글은 구간의 글에서 잘라 문장부호를 지킨다. 낱말이 구간의 글과 맞지 않으면 낱말을 이어 쓴다.
- 장면: 줄이 겹치는 시간으로 배정한다. 조금(min_scene_overlap_ms 미만) 걸친 장면은 세지 않는다.
  줄은 장면의 경계에서 억지로 자르지 않는다 (문장은 컷을 넘어 이어질 수 있다).
- 신뢰도: 인식기가 구간마다 준 값을 그대로 옮긴다. 인식기가 '말이 아닐 수 있다' 고 하고 신뢰도도 낮은 구간은
  줄로 삼지 않고 메모에 남긴다 (음악·무음에서 지어낸 문장을 거른다).
- 말이 아닌 소리: 무음 구간을 잰 것이 있으면, 무음도 말도 아닌 시간을 '가르지 못한 소리' 로 남긴다.
  말소리 아래에 깔린 음악은 이 방법으로는 알 수 없다.
- 종류: 말할 사람이 화면에 없는 장면의 줄은 내레이션이다. 사람이 보이는 장면의 줄은 소리만으로 가르지 못한다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from frame.domain.script.lines import (
    LineKind,
    SceneSpan,
    SoundKind,
    SoundSpan,
    SpokenLine,
    SpokenScript,
)
from frame.domain.speech import SpeechSegment, Transcript, Word


@dataclass(frozen=True)
class ScriptPolicy:
    split_pause_ms: int = 600  # 낱말 사이가 이만큼 비면 줄을 나눈다
    min_scene_overlap_ms: int = 200  # 이만큼은 걸쳐야 그 장면에 걸친 줄이다
    # 인식기가 말이 아닐 가능성을 이 이상으로 보고, 신뢰도도 이보다 낮으면 줄로 삼지 않는다
    # (Whisper 가 무음 구간을 거를 때 쓰는 기준과 같다: no_speech_prob > 0.6, avg_logprob < -1.0)
    no_speech_above: float = 0.6
    confidence_below: float = 0.37
    min_sound_ms: int = 300  # 이보다 짧은 무음·소리 조각은 적지 않는다


DEFAULT_SCRIPT_POLICY = ScriptPolicy()


def build_script(
    transcript: Transcript | None,
    scenes: Sequence[SceneSpan] = (),
    *,
    duration_ms: int | None = None,
    silences: Sequence[tuple[int, int]] | None = None,
    policy: ScriptPolicy = DEFAULT_SCRIPT_POLICY,
) -> SpokenScript:
    """받아쓰기 -> 대본. silences 는 소리에서 잰 무음 구간이다 (재지 않았으면 None)."""
    notes: list[str] = []
    pieces: list[tuple[int, int, str, tuple[Word, ...], float | None]] = []
    for segment in transcript.segments if transcript is not None else ():
        text = " ".join(segment.text.split())
        if not text:
            continue
        if _not_speech(segment, policy):
            notes.append(
                f"{segment.start_ms / 1000:.1f}~{segment.end_ms / 1000:.1f}초의 '{text}' 는 "
                "말소리가 아닌 것으로 보고 줄로 삼지 않았습니다"
            )
            continue
        pieces += [(*piece, segment.confidence) for piece in _split(segment, text, policy)]
    lines = tuple(
        SpokenLine(
            line_id=f"S{number}",
            start_ms=start,
            end_ms=end,
            text=text,
            scene_keys=_scenes_of(start, end, scenes, policy),
            confidence=confidence,
            words=words,
        )
        for number, (start, end, text, words, confidence) in enumerate(pieces, start=1)
    )
    return SpokenScript(
        language=transcript.language if transcript is not None else None,
        lines=lines,
        sounds=_sounds(lines, silences, duration_ms, policy),
        duration_ms=duration_ms,
        notes=tuple(notes),
    )


def classify_kinds(script: SpokenScript, speaker_visible: Mapping[str, bool]) -> SpokenScript:
    """말할 사람이 화면에 없는 장면의 줄을 내레이션으로 정한다. 이미 정해진 종류는 바꾸지 않는다.

    speaker_visible: 장면 키 -> 그 장면에 말할 수 있는 사람이 보이는가. 없는 장면은 모르는 것으로 본다.
    """
    lines = []
    for line in script.lines:
        seen = [speaker_visible.get(key) for key in line.scene_keys]
        if line.kind is LineKind.UNKNOWN and seen and all(value is False for value in seen):
            line = line.with_kind(LineKind.NARRATION)
        lines.append(line)
    return replace(script, lines=tuple(lines))


# ---- 안쪽 ----------------------------------------------------------------------
def _not_speech(segment: SpeechSegment, policy: ScriptPolicy) -> bool:
    if segment.no_speech is None or segment.confidence is None:
        return False
    return (
        segment.no_speech > policy.no_speech_above and segment.confidence < policy.confidence_below
    )


def _split(
    segment: SpeechSegment, text: str, policy: ScriptPolicy
) -> list[tuple[int, int, str, tuple[Word, ...]]]:
    words = tuple(w for w in segment.words if w.text.strip())
    groups: list[list[Word]] = []
    for word in words:
        if groups and word.start_ms - groups[-1][-1].end_ms < policy.split_pause_ms:
            groups[-1].append(word)
        else:
            groups.append([word])
    if len(groups) < 2:
        return [(segment.start_ms, segment.end_ms, text, words)]
    starts = _positions(text, words)
    pieces: list[tuple[int, int, str, tuple[Word, ...]]] = []
    index = 0
    for number, group in enumerate(groups):
        following = index + len(group)
        if starts is None:
            piece = " ".join(w.text.strip() for w in group)
        else:
            piece = text[starts[index] : starts[following] if following < len(words) else None]
            piece = " ".join(((text[: starts[index]] if number == 0 else "") + piece).split())
        pieces.append((group[0].start_ms, group[-1].end_ms, piece, tuple(group)))
        index = following
    return pieces


def _positions(text: str, words: Sequence[Word]) -> list[int] | None:
    """낱말마다 구간의 글에서 시작하는 자리. 하나라도 찾지 못하면 None (글과 낱말이 다르게 적혔다)."""
    starts: list[int] = []
    cursor = 0
    for word in words:
        found = text.find(word.text.strip(), cursor)
        if found < 0:
            return None
        starts.append(found)
        cursor = found + len(word.text.strip())
    return starts


def _scenes_of(
    start: int, end: int, scenes: Sequence[SceneSpan], policy: ScriptPolicy
) -> tuple[str, ...]:
    overlaps = [
        (min(end, scene.end_ms) - max(start, scene.start_ms), scene.key) for scene in scenes
    ]
    enough = min(policy.min_scene_overlap_ms, max(1, (end - start) // 2))
    spanned = [(overlap, key) for overlap, key in overlaps if overlap >= enough]
    spanned.sort(key=lambda item: -item[0])  # 많이 걸친 장면이 앞 (같으면 먼저 나온 장면)
    return tuple(key for _, key in spanned)


def _sounds(
    lines: Sequence[SpokenLine],
    silences: Sequence[tuple[int, int]] | None,
    duration_ms: int | None,
    policy: ScriptPolicy,
) -> tuple[SoundSpan, ...]:
    if silences is None or duration_ms is None:
        return ()  # 무음을 재지 않았으면 말이 아닌 소리를 지어내지 않는다
    quiet = sorted((max(0, a), min(duration_ms, b)) for a, b in silences if b > a)
    spans = [SoundSpan(a, b, SoundKind.SILENCE) for a, b in quiet if b - a >= policy.min_sound_ms]
    taken = sorted([*quiet, *((line.start_ms, line.end_ms) for line in lines)])
    cursor = 0
    for a, b in [*taken, (duration_ms, duration_ms)]:
        if a - cursor >= policy.min_sound_ms:
            spans.append(SoundSpan(cursor, a, SoundKind.OTHER))
        cursor = max(cursor, b)
    return tuple(sorted(spans, key=lambda span: (span.start_ms, span.end_ms)))
