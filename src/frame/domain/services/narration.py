"""녹음 붙이기 규칙 (ADR-0026): 사람이 녹음한 음성을 대본 줄에 맞추고 장면에 놓는다. 순수 함수.

음성은 합성하지 않는다. 사용자가 대본을 읽어 녹음한 파일을 받는다
(편 전체를 한 번에 녹음한 파일 하나, 또는 줄마다 하나).

1. 맞추기 (align_script): 녹음을 인식한 말소리 조각을 대본 줄에 순서대로 배정한다.
   - 조각은 낱말 시각이 있으면 낱말, 없으면 인식기가 준 구간이다.
   - 줄마다 이어진 조각 묶음 하나. 줄의 순서는 바뀌지 않는다. 남는 조각(다시 읽은 부분·군말)은 건너뛴다.
   - 줄과 묶음의 글자 유사도의 합이 가장 큰 배정을 고른다 (동적 계획법).
   - 유사도가 기준에 못 미치면 MISMATCH (대본과 다르게 읽음), 조각을 받지 못하면 MISSING (읽지 않음).
   - 숫자는 읽는 소리로 바꿔 견준다: 인식기는 '오천 원' 을 '5,000원' 으로 적곤 한다.
   - 핵심어(메뉴 이름·가격 같은 편의 변수 값)가 대본 줄에 있으면 녹음에도 있어야 한다. 짧은 줄에서 한 글자만
     틀려도 유사도는 높게 나오므로(오천 원 / 육천 원 = 0.83), 사실이 틀린 것은 유사도가 아니라 핵심어로 잡는다.
2. 놓기 (place_lines): 줄의 음성을 그 줄의 장면 안에 놓는다.
   - 장면 시작에서 lead_in 뒤에 시작한다. 음성이 말할 수 있는 시간보다 길면 빠르기를 올린다 (max_tempo 까지).
   - 그래도 넘치면 OVERFLOW: 자르지 않고 넘친 길이를 알린다 (다시 녹음하거나 대본을 줄일 일이다).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from enum import StrEnum
from typing import Any

from ..episode import ScriptLine
from ..errors import ValidationError
from ..video_analysis import Transcript

_DIGITS = re.compile(r"\d[\d,]*")
_SINO = ("", "일", "이", "삼", "사", "오", "육", "칠", "팔", "구")
_SMALL_UNITS = ("", "십", "백", "천")
_BIG_UNITS = ("", "만", "억", "조")


class LineStatus(StrEnum):
    MATCHED = "MATCHED"  # 대본대로 읽었다
    MISMATCH = "MISMATCH"  # 읽었지만 대본과 다르다
    MISSING = "MISSING"  # 이 줄의 음성을 찾지 못했다


class PlacementStatus(StrEnum):
    PLACED = "PLACED"  # 그대로 놓았다
    SPED_UP = "SPED_UP"  # 빠르기를 올려 장면 안에 넣었다
    OVERFLOW = "OVERFLOW"  # 빠르기를 올려도 장면보다 길다
    MISSING = "MISSING"  # 놓을 음성이 없다


@dataclass(frozen=True)
class NarrationPolicy:
    lead_in_ms: int = 200  # 장면이 시작하고 말이 나오기까지
    tail_ms: int = 200  # 말이 끝나고 장면이 바뀌기까지
    max_tempo: float = 1.15  # 빠르기를 올리는 상한 (이보다 빠르면 말이 부자연스럽다)
    pad_ms: int = 80  # 잘라 낸 음성 앞뒤에 남기는 여유 (낱말 시각이 말소리를 조금 깎는다)
    min_similarity: float = 0.6  # 이보다 낮으면 대본과 다르게 읽은 것으로 본다

    def __post_init__(self) -> None:
        if self.max_tempo < 1.0:
            raise ValidationError(f"빠르기 상한은 1 이상이어야 합니다: {self.max_tempo}")
        if not 0 <= self.min_similarity <= 1:
            raise ValidationError(f"유사도 기준은 0~1 이어야 합니다: {self.min_similarity}")


DEFAULT_NARRATION_POLICY = NarrationPolicy()


@dataclass(frozen=True)
class SpeechUnit:
    """맞추기의 최소 단위: 낱말 또는 인식 구간."""

    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True)
class LineAlignment:
    """대본 줄 하나가 녹음의 어디에 있는가."""

    line_key: str
    status: LineStatus
    similarity: float = 0.0
    heard: str = ""  # 그 구간에서 인식된 말
    start_ms: int | None = None  # 녹음 안에서의 시각
    end_ms: int | None = None
    source_index: int = 0  # 어느 녹음 파일인가 (줄마다 녹음했을 때)
    missing_terms: tuple[str, ...] = ()  # 대본 줄에는 있는데 녹음에서 들리지 않은 핵심어

    def to_dict(self) -> dict[str, Any]:
        return {
            "line_key": self.line_key,
            "status": str(self.status),
            "similarity": round(self.similarity, 4),
            "heard": self.heard,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "source_index": self.source_index,
            "missing_terms": list(self.missing_terms),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> LineAlignment:
        return cls(
            line_key=str(data["line_key"]),
            status=LineStatus(data["status"]),
            similarity=float(data.get("similarity", 0.0)),
            heard=str(data.get("heard", "")),
            start_ms=data.get("start_ms"),
            end_ms=data.get("end_ms"),
            source_index=int(data.get("source_index", 0)),
            missing_terms=tuple(str(t) for t in data.get("missing_terms", ())),
        )


@dataclass(frozen=True)
class Placement:
    """줄 하나의 음성을 편의 타임라인 어디에 어떻게 놓는가."""

    line_key: str
    scene_key: str
    status: PlacementStatus
    source_index: int = 0
    source_start_ms: int = 0  # 녹음에서 잘라 낼 구간
    source_end_ms: int = 0
    target_start_ms: int = 0  # 편의 타임라인에서 시작하는 시각
    tempo: float = 1.0  # 1 보다 크면 그만큼 빠르게
    duration_ms: int = 0  # 놓인 뒤의 길이
    overflow_ms: int = 0  # 말할 수 있는 시간을 넘긴 길이

    @property
    def target_end_ms(self) -> int:
        return self.target_start_ms + self.duration_ms

    def to_dict(self) -> dict[str, Any]:
        return {
            "line_key": self.line_key,
            "scene_key": self.scene_key,
            "status": str(self.status),
            "source_index": self.source_index,
            "source_start_ms": self.source_start_ms,
            "source_end_ms": self.source_end_ms,
            "target_start_ms": self.target_start_ms,
            "tempo": round(self.tempo, 4),
            "duration_ms": self.duration_ms,
            "overflow_ms": self.overflow_ms,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Placement:
        return cls(
            line_key=str(data["line_key"]),
            scene_key=str(data["scene_key"]),
            status=PlacementStatus(data["status"]),
            source_index=int(data.get("source_index", 0)),
            source_start_ms=int(data.get("source_start_ms", 0)),
            source_end_ms=int(data.get("source_end_ms", 0)),
            target_start_ms=int(data.get("target_start_ms", 0)),
            tempo=float(data.get("tempo", 1.0)),
            duration_ms=int(data.get("duration_ms", 0)),
            overflow_ms=int(data.get("overflow_ms", 0)),
        )


# ---- 글자 견주기 ---------------------------------------------------------------
def read_number(digits: str) -> str:
    """숫자를 읽는 소리로: '5,000' -> '오천', '4500' -> '사천오백', '10' -> '십'. 한자어 수로만 읽는다."""
    value = int(digits.replace(",", "") or 0)
    if value == 0:
        return "영"
    groups: list[str] = []
    index = 0
    while value > 0 and index < len(_BIG_UNITS):
        value, chunk = divmod(value, 10_000)
        if chunk:
            text = ""
            for position in range(3, -1, -1):
                digit = chunk // 10**position % 10
                if digit == 0:
                    continue
                # 십·백·천 앞의 '일' 은 읽지 않는다 (만 단위 이상의 묶음에서도 같다)
                text += ("" if digit == 1 and position > 0 else _SINO[digit]) + _SMALL_UNITS[
                    position
                ]
            # '일만' 은 '만' 으로 읽는다
            groups.append(("" if text == "일" and index == 1 else text) + _BIG_UNITS[index])
        index += 1
    return "".join(reversed(groups))


def normalize_speech(text: str) -> str:
    """견줄 수 있게: 숫자는 읽는 소리로, 글자·숫자만 남기고 소문자로."""
    spoken = _DIGITS.sub(
        lambda match: read_number(match.group(0)), unicodedata.normalize("NFKC", text)
    )
    return "".join(ch for ch in spoken.lower() if ch.isalnum())


def similarity(expected: str, heard: str) -> float:
    """두 문장의 글자 유사도 (0..1). 띄어쓰기·문장부호·숫자 표기는 보지 않는다."""
    a, b = normalize_speech(expected), normalize_speech(heard)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


# ---- 맞추기 -------------------------------------------------------------------
def speech_units(transcript: Transcript) -> tuple[SpeechUnit, ...]:
    """맞추기에 쓸 조각. 모든 구간에 낱말 시각이 있으면 낱말, 아니면 구간."""
    spoken = [s for s in transcript.segments if s.text.strip()]
    if spoken and all(s.words for s in spoken):
        return tuple(
            SpeechUnit(w.start_ms, w.end_ms, w.text)
            for s in spoken
            for w in s.words
            if w.text.strip()
        )
    return tuple(SpeechUnit(s.start_ms, s.end_ms, s.text) for s in spoken)


def align_script(
    script: Sequence[ScriptLine],
    transcript: Transcript,
    policy: NarrationPolicy = DEFAULT_NARRATION_POLICY,
    *,
    key_terms: Sequence[str] = (),
) -> tuple[LineAlignment, ...]:
    """편 전체를 한 번에 녹음한 파일의 인식 결과를 대본 줄에 맞춘다 (줄마다 결과 하나, 대본 순서).

    key_terms 는 틀리면 안 되는 말이다 (편의 변수 값 등). 대본 줄에 든 핵심어가 녹음에 없으면 MISMATCH 다.
    """
    units = speech_units(transcript)
    lines = list(script)
    if not lines:
        return ()
    texts = [normalize_speech(u.text) for u in units]
    targets = [normalize_speech(line.text) for line in lines]
    n, m = len(lines), len(units)
    # best[i][b]: 앞의 i 줄을 조각 0..b-1 안에 배정했을 때의 유사도 합. back 은 (i 번째 줄의 묶음 시작, 끝) 또는 None
    best = [[0.0] * (m + 1) for _ in range(n + 1)]
    back: list[list[tuple[int, int] | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        target = targets[i - 1]
        limit = int(len(target) * 2.5) + 4  # 줄보다 훨씬 긴 묶음은 보지 않는다
        for b in range(m + 1):
            value, choice = best[i - 1][b], None  # 이 줄을 건너뜀
            if b > 0 and best[i][b - 1] > value:
                value, choice = best[i][b - 1], back[i][b - 1]  # 조각 b-1 을 쓰지 않음
            joined = ""
            for a in range(b - 1, -1, -1):
                joined = texts[a] + joined
                if len(joined) > limit:
                    break
                score = _ratio(target, joined)
                if score > 0 and best[i - 1][a] + score > value:
                    value, choice = best[i - 1][a] + score, (a, b)
            best[i][b], back[i][b] = value, choice
    # 뒤에서부터 따라가며 줄마다 고른 묶음을 꺼낸다
    chosen: list[tuple[int, int] | None] = [None] * n
    b = m
    for i in range(n, 0, -1):
        run = back[i][b]
        chosen[i - 1] = run
        if run is not None:
            b = run[0]
    return tuple(
        _alignment(line, units, run, policy, key_terms)
        for line, run in zip(lines, chosen, strict=True)
    )


def check_line(
    line: ScriptLine,
    transcript: Transcript,
    *,
    source_index: int = 0,
    policy: NarrationPolicy = DEFAULT_NARRATION_POLICY,
    key_terms: Sequence[str] = (),
) -> LineAlignment:
    """줄마다 따로 녹음한 파일: 파일 전체가 그 줄이다. 인식 결과로 대본과 맞는지만 본다."""
    units = speech_units(transcript)
    if not units:
        return LineAlignment(line.line_key, LineStatus.MISSING, source_index=source_index)
    result = _alignment(line, units, (0, len(units)), policy, key_terms)
    return replace(result, source_index=source_index)


def _ratio(target: str, joined: str) -> float:
    if not target or not joined:
        return 0.0
    return SequenceMatcher(None, target, joined, autojunk=False).ratio()


def missing_key_terms(expected: str, heard: str, key_terms: Sequence[str]) -> tuple[str, ...]:
    """대본 줄(expected)에 든 핵심어 가운데 녹음(heard)에서 들리지 않은 것."""
    said, wanted = normalize_speech(heard), normalize_speech(expected)
    missing: list[str] = []
    for term in key_terms:
        needle = normalize_speech(term)
        if needle and needle in wanted and needle not in said and term not in missing:
            missing.append(term)
    return tuple(missing)


def _alignment(
    line: ScriptLine,
    units: Sequence[SpeechUnit],
    run: tuple[int, int] | None,
    policy: NarrationPolicy,
    key_terms: Sequence[str] = (),
) -> LineAlignment:
    if run is None:
        return LineAlignment(line.line_key, LineStatus.MISSING)
    picked = units[run[0] : run[1]]
    heard = " ".join(u.text.strip() for u in picked)
    score = similarity(line.text, heard)
    missing = missing_key_terms(line.text, heard, key_terms)
    matched = score >= policy.min_similarity and not missing
    return LineAlignment(
        line.line_key,
        LineStatus.MATCHED if matched else LineStatus.MISMATCH,
        score,
        heard,
        picked[0].start_ms,
        picked[-1].end_ms,
        missing_terms=missing,
    )


# ---- 놓기 --------------------------------------------------------------------
def place_lines(
    script: Sequence[ScriptLine],
    alignments: Sequence[LineAlignment],
    scene_spans: Mapping[str, tuple[int, int]],
    source_durations_ms: Sequence[int],
    policy: NarrationPolicy = DEFAULT_NARRATION_POLICY,
) -> tuple[Placement, ...]:
    """맞춘 결과를 편의 타임라인에 놓는다 (줄마다 하나, 대본 순서)."""
    by_key = {a.line_key: a for a in alignments}
    ordered = [by_key.get(line.line_key) for line in script]
    placements: list[Placement] = []
    for index, line in enumerate(script):
        if line.scene_key not in scene_spans:
            raise ValidationError(
                f"대본 줄 '{line.line_key}' 의 장면 '{line.scene_key}' 이 없습니다"
            )
        alignment = ordered[index]
        if alignment is None or alignment.start_ms is None or alignment.end_ms is None:
            placements.append(Placement(line.line_key, line.scene_key, PlacementStatus.MISSING))
            continue
        start, end = _cut_span(alignment, ordered, index, source_durations_ms, policy)
        scene_start, scene_end = scene_spans[line.scene_key]
        budget = max(1, scene_end - scene_start - policy.lead_in_ms - policy.tail_ms)
        natural = end - start
        if natural <= budget:
            status, tempo = PlacementStatus.PLACED, 1.0
        elif natural <= budget * policy.max_tempo:
            status, tempo = PlacementStatus.SPED_UP, natural / budget
        else:
            status, tempo = PlacementStatus.OVERFLOW, policy.max_tempo
        duration = round(natural / tempo)
        placements.append(
            Placement(
                line_key=line.line_key,
                scene_key=line.scene_key,
                status=status,
                source_index=alignment.source_index,
                source_start_ms=start,
                source_end_ms=end,
                target_start_ms=scene_start + policy.lead_in_ms,
                tempo=tempo,
                duration_ms=duration,
                overflow_ms=max(0, duration - budget),
            )
        )
    return tuple(placements)


def _cut_span(
    alignment: LineAlignment,
    ordered: Sequence[LineAlignment | None],
    index: int,
    source_durations_ms: Sequence[int],
    policy: NarrationPolicy,
) -> tuple[int, int]:
    """녹음에서 잘라 낼 구간: 말소리 앞뒤에 여유를 두되, 같은 파일의 이웃 줄과 겹치지 않게 한다."""
    assert alignment.start_ms is not None and alignment.end_ms is not None
    if not 0 <= alignment.source_index < len(source_durations_ms):
        raise ValidationError(f"녹음 파일 번호가 잘못되었습니다: {alignment.source_index}")
    low, high = 0, source_durations_ms[alignment.source_index]
    for other in ordered[:index]:
        if other is not None and other.end_ms is not None and _same_file(other, alignment):
            low = max(low, (other.end_ms + alignment.start_ms) // 2)
    for other in ordered[index + 1 :]:
        if other is not None and other.start_ms is not None and _same_file(other, alignment):
            high = min(high, (alignment.end_ms + other.start_ms) // 2)
            break
    start = max(low, alignment.start_ms - policy.pad_ms)
    end = min(high, alignment.end_ms + policy.pad_ms)
    return (start, max(start + 1, end))


def _same_file(a: LineAlignment, b: LineAlignment) -> bool:
    return a.source_index == b.source_index
