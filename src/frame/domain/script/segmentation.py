"""받아쓰기를 대본으로 다듬는다 (ADR-0028). 순수 함수.

- 줄: 인식기가 준 구간이 기본이다. 낱말 시각이 있으면 낱말 사이가 오래 빈 곳(split_pause_ms)에서 더 나눈다.
  나눈 줄의 글은 구간의 글에서 잘라 문장부호를 지킨다. 낱말이 구간의 글과 맞지 않으면 낱말을 이어 쓴다.
- 잰 무음으로 맞춘다 (무음을 잰 것이 있을 때): 인식기의 시각은 무음 쪽으로 어긋난다. 첫 줄을 0초로 당겨 적고,
  문장 안의 멈춤을 다음 낱말 안에 숨긴다 (낱말 사이가 0 이 된다). 그래서
  · 줄의 시작과 끝을 소리가 나는 곳으로 자르고, 소리의 끝과 가까우면(snap_ms) 거기에 맞춘다
  · 줄이 걸친 소리 사이의 무음이 길면(split_pause_ms) 거기서 줄을 나눈다 (낱말은 끝나는 시각으로 가른다)
  · 소리가 없는 곳의 줄은 지어낸 문장으로 보고 버린다
  배경음이 깔려 무음이 없으면 맞출 곳이 없어 인식기의 시각을 그대로 쓴다.
- 지어낸 문장: 인식기는 말이 없는 소리에서 '고맙습니다'·'다음 영상에서 만나요' 같은 문장을 지어낸다. 줄로 삼지 않는 것은
  · 인식기가 '말이 아닐 수 있다' 고 하고 신뢰도도 낮은 구간, 말이 아니라고 거의 확신한 구간
  · 소리가 없는 곳의 구간 (위)
  · 말이라기에는 너무 빠른 구간 (0.14초에 열네 글자)
  줄로 삼지 않은 것은 메모에 남긴다. 잡음 속에서 그럴듯한 길이로 지어낸 문장은 이 방법으로 거르지 못한다.
- 장면: 줄이 겹치는 시간으로 배정한다. 조금(min_scene_overlap_ms 미만) 걸친 장면은 세지 않는다.
  줄은 장면의 경계에서 억지로 자르지 않는다 (문장은 컷을 넘어 이어질 수 있다).
- 신뢰도: 인식기가 구간마다 준 값을 그대로 옮긴다.
- 말이 아닌 소리: 무음도 말도 아닌 시간을 '가르지 못한 소리' 로 남긴다. 줄이 하나도 걸치지 않은 소리는 메모에도 남긴다
  (음악·소음이거나 인식기가 놓친 말이다). 말소리 아래에 깔린 음악은 이 방법으로는 알 수 없다.
- 종류: 말할 사람이 화면에 없는 장면의 줄은 내레이션이다. 사람이 보이는 장면의 줄은 소리만으로 가르지 못한다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import NamedTuple

from frame.domain.script.lines import (
    LineKind,
    SceneSpan,
    SoundKind,
    SoundSpan,
    SpokenLine,
    SpokenScript,
)
from frame.domain.speech import SpeechSegment, Transcript, Word, spoken_characters

Span = tuple[int, int]


@dataclass(frozen=True)
class ScriptPolicy:
    split_pause_ms: int = 600  # 낱말 사이(또는 잰 무음)가 이만큼 비면 줄을 나눈다
    min_scene_overlap_ms: int = 200  # 이만큼은 걸쳐야 그 장면에 걸친 줄이다
    # 인식기가 말이 아닐 가능성을 이 이상으로 보고, 신뢰도도 이보다 낮으면 줄로 삼지 않는다
    # (Whisper 가 무음 구간을 거를 때 쓰는 기준과 같다: no_speech_prob > 0.6, avg_logprob < -1.0)
    no_speech_above: float = 0.6
    confidence_below: float = 0.37
    # 말이 아닐 가능성이 이보다 높으면 신뢰도를 보지 않는다 (실제 말소리 100구간의 최댓값은 0.61 이었다)
    no_speech_alone_above: float = 0.9
    min_sound_ms: int = 300  # 이보다 짧은 무음·소리 조각은 적지 않는다
    min_island_ms: int = 150  # 이보다 짧은 소리는 소리로 치지 않는다 (딸깍 소리)
    snap_ms: int = 400  # 줄의 끝이 소리의 끝과 이만큼 안에 있으면 소리의 끝에 맞춘다
    # 이보다 빠르면 말이 아니다. 빠른 말이 1초에 열 글자쯤이고, 지어낸 문장은 길이가 없다시피 하다
    max_chars_per_second: float = 20.0


DEFAULT_SCRIPT_POLICY = ScriptPolicy()


class _Piece(NamedTuple):
    """줄이 될 조각."""

    start: int
    end: int
    text: str
    words: tuple[Word, ...]
    confidence: float | None


def build_script(
    transcript: Transcript | None,
    scenes: Sequence[SceneSpan] = (),
    *,
    duration_ms: int | None = None,
    silences: Sequence[Span] | None = None,
    policy: ScriptPolicy = DEFAULT_SCRIPT_POLICY,
) -> SpokenScript:
    """받아쓰기 -> 대본. silences 는 소리에서 잰 무음 구간이다 (재지 않았으면 None)."""
    notes: list[str] = []
    pieces: list[_Piece] = []
    for segment in transcript.segments if transcript is not None else ():
        text = " ".join(segment.text.split())
        if not text:
            continue
        if spoken_characters(text) == 0:  # '♪'·'...' — 말이 아닌 소리의 표시다
            notes.append(_dropped(segment.start_ms, segment.end_ms, text, _NO_LETTERS))
            continue
        if _not_speech(segment, policy):
            notes.append(
                _dropped(segment.start_ms, segment.end_ms, text, "말소리가 아닌 것으로 보고")
            )
            continue
        pieces += _split(segment, text, policy)
    sound = audible_spans(duration_ms, silences, policy.min_island_ms)
    if sound is not None and duration_ms is not None:
        pieces = _fit_to_sound(pieces, sound, duration_ms, policy, notes)
    kept: list[_Piece] = []
    for piece in pieces:
        if spoken_characters(piece.text) == 0:  # 낱말 사이에서 나뉜 조각이 표시뿐일 때
            notes.append(_dropped(piece.start, piece.end, piece.text, _NO_LETTERS))
        elif _too_fast(piece, policy):
            notes.append(_dropped(piece.start, piece.end, piece.text, "말이라기에는 너무 짧아"))
        else:
            kept.append(piece)
    lines = tuple(
        SpokenLine(
            line_id=f"S{number}",
            start_ms=piece.start,
            end_ms=piece.end,
            text=piece.text,
            scene_keys=_scenes_of(piece.start, piece.end, scenes, policy),
            confidence=piece.confidence,
            words=piece.words,
        )
        for number, piece in enumerate(kept, start=1)
    )
    for start, end in sound or ():
        if end - start >= policy.min_sound_ms and not any(
            line.start_ms < end and line.end_ms > start for line in lines
        ):
            notes.append(
                f"{start / 1000:.1f}~{end / 1000:.1f}초에 소리가 있는데 받아 적은 말이 없습니다 "
                "(음악·소음이거나 놓친 말일 수 있습니다)"
            )
    return SpokenScript(
        language=transcript.language if transcript is not None else None,
        lines=lines,
        sounds=_sounds(lines, silences, duration_ms, policy),
        duration_ms=duration_ms,
        notes=tuple(notes),
    )


def audible_spans(
    duration_ms: int | None,
    silences: Sequence[Span] | None,
    min_ms: int = DEFAULT_SCRIPT_POLICY.min_island_ms,
) -> tuple[Span, ...] | None:
    """소리가 나는 구간: 길이에서 무음을 뺀 것. 무음을 재지 않았으면 None (알 수 없다).

    min_ms 보다 짧은 소리는 넣지 않는다. 비어 있으면 처음부터 끝까지 무음이다.
    """
    if duration_ms is None or silences is None:
        return None
    spans: list[Span] = []
    cursor = 0
    for start, end in sorted((max(0, a), min(duration_ms, b)) for a, b in silences if b > a):
        if start - cursor >= min_ms:
            spans.append((cursor, start))
        cursor = max(cursor, end)
    if duration_ms - cursor >= min_ms:
        spans.append((cursor, duration_ms))
    return tuple(spans)


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
_NO_LETTERS = "글자가 없는 소리 표시라"  # 줄로 삼지 않은 까닭 (메모에 쓴다)


def _dropped(start: int, end: int, text: str, why: str) -> str:
    return f"{start / 1000:.1f}~{end / 1000:.1f}초의 '{text}' 는 {why} 줄로 삼지 않았습니다"


def _not_speech(segment: SpeechSegment, policy: ScriptPolicy) -> bool:
    if segment.no_speech is None:
        return False
    if segment.no_speech > policy.no_speech_alone_above:
        return True
    return (
        segment.confidence is not None
        and segment.no_speech > policy.no_speech_above
        and segment.confidence < policy.confidence_below
    )


def _too_fast(piece: _Piece, policy: ScriptPolicy) -> bool:
    chars = spoken_characters(piece.text)
    return chars >= 2 and chars * 1000 > policy.max_chars_per_second * max(
        1, piece.end - piece.start
    )


def _split(segment: SpeechSegment, text: str, policy: ScriptPolicy) -> list[_Piece]:
    """낱말 사이가 오래 빈 곳에서 구간을 나눈다."""
    words = tuple(w for w in segment.words if w.text.strip())
    groups: list[list[Word]] = []
    for word in words:
        if groups and word.start_ms - groups[-1][-1].end_ms < policy.split_pause_ms:
            groups[-1].append(word)
        else:
            groups.append([word])
    whole = _Piece(segment.start_ms, segment.end_ms, text, words, segment.confidence)
    if len(groups) < 2:
        return [whole]
    return [
        _Piece(group[0].start_ms, group[-1].end_ms, piece, tuple(group), segment.confidence)
        for group, piece in zip(groups, _texts(text, words, groups), strict=True)
    ]


def _texts(text: str, words: Sequence[Word], groups: Sequence[Sequence[Word]]) -> list[str]:
    """낱말 묶음마다의 글. 구간의 글에서 잘라 문장부호를 지킨다. 낱말이 글과 맞지 않으면 낱말을 이어 쓴다."""
    starts = _positions(text, words)
    if starts is None:
        return [" ".join(w.text.strip() for w in group) for group in groups]
    pieces: list[str] = []
    index = 0
    for number, group in enumerate(groups):
        following = index + len(group)
        piece = text[starts[index] : starts[following] if following < len(words) else None]
        pieces.append(" ".join(((text[: starts[index]] if number == 0 else "") + piece).split()))
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


def _fit_to_sound(
    pieces: Sequence[_Piece],
    sound: Sequence[Span],
    duration_ms: int,
    policy: ScriptPolicy,
    notes: list[str],
) -> list[_Piece]:
    """조각의 시각을 소리가 나는 곳에 맞춘다: 무음을 잘라 내고, 긴 무음에서 나누고, 소리가 없는 조각은 버린다."""
    fitted: list[tuple[_Piece, Span, Span]] = []  # (조각, 걸친 첫 소리, 걸친 마지막 소리)
    for piece in pieces:
        islands = [span for span in sound if span[0] < piece.end and span[1] > piece.start]
        if not islands:
            notes.append(
                _dropped(
                    piece.start,
                    piece.end,
                    piece.text,
                    "소리가 없는 구간이라 (지어낸 문장으로 보고)",
                )
            )
            continue
        for part, group in _split_at_silences(piece, islands, policy):
            start, end = max(part.start, group[0][0]), min(part.end, group[-1][1])
            fitted.append(
                (part._replace(start=start, end=max(start + 1, end)), group[0], group[-1])
            )
    # 소리의 끝에 가까운 줄은 소리의 끝에 맞춘다. 무음과 맞닿은 끝만 본다 (파일의 처음·끝은 말의 끝이 아니다)
    out: list[_Piece] = []
    for index, (piece, first, last) in enumerate(fitted):
        previous_end = out[-1].end if out else 0
        following = fitted[index + 1][0].start if index + 1 < len(fitted) else duration_ms
        start, end = piece.start, piece.end
        if first[0] > 0 and 0 < start - first[0] <= policy.snap_ms and previous_end <= first[0]:
            start = first[0]
        if last[1] < duration_ms and 0 < last[1] - end <= policy.snap_ms and following >= last[1]:
            end = last[1]
        out.append(piece._replace(start=start, end=end))
    return out


def _split_at_silences(
    piece: _Piece, islands: Sequence[Span], policy: ScriptPolicy
) -> list[tuple[_Piece, list[Span]]]:
    """조각이 걸친 소리 사이의 무음이 길면 거기서 나눈다. 낱말은 끝나는 시각이 든 쪽에 넣는다.

    인식기는 멈춤을 다음 낱말의 앞에 붙이므로 낱말의 시작은 믿을 수 없고 끝은 믿을 만하다.
    """
    groups: list[list[Span]] = [[islands[0]]]
    for island in islands[1:]:
        if island[0] - groups[-1][-1][1] >= policy.split_pause_ms:
            groups.append([island])
        else:
            groups[-1].append(island)
    if len(groups) < 2 or not piece.words:
        return [(piece, list(islands))]
    buckets: list[list[Word]] = [[] for _ in groups]
    for word in piece.words:
        owner = max((i for i, group in enumerate(groups) if group[0][0] < word.end_ms), default=0)
        buckets[owner].append(word)
    spoken = [(bucket, group) for bucket, group in zip(buckets, groups, strict=True) if bucket]
    if (
        len(spoken) < 2
    ):  # 낱말이 한쪽에만 있다: 나눌 것이 없고, 낱말이 없는 소리는 이 줄의 것이 아니다
        return [(piece, spoken[0][1])]
    texts = _texts(piece.text, piece.words, [bucket for bucket, _ in spoken])
    return [
        (_Piece(piece.start, piece.end, text, tuple(bucket), piece.confidence), group)
        for (bucket, group), text in zip(spoken, texts, strict=True)
    ]


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
    silences: Sequence[Span] | None,
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
