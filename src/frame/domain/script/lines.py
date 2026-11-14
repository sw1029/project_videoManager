"""뽑은 대본의 타입 (ADR-0028): 말한 줄, 말이 아닌 소리, 대본.

받아쓰기(Transcript)는 인식기가 준 그대로이고, 대본은 그것을 줄 단위로 다듬은 것이다.
- SpokenLine: 줄 하나. 시작·끝, 글, 걸치는 장면, 신뢰도, 종류(내레이션·대사·모름).
  사람이 글을 고치면 text 만 바뀌고 인식기가 적은 글은 heard 에 남는다.
- SoundSpan: 말이 아닌 소리 (무음 · 음악 · 소음 · 가르지 못한 소리).
- SpokenScript: 줄과 소리의 묶음. 말이 없으면 줄이 비어 있다 ('대본 없음' 이지 오류가 아니다).

편에서 사람이 읽을 '녹음 대본'(ScriptLine)은 계획이고 domain/series/episode 에 있다.
이름이 비슷해 뽑은 쪽을 Spoken 이라 부른다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from frame.domain.errors import NotFound, ValidationError
from frame.domain.speech import Word, spoken_characters


class LineKind(StrEnum):
    NARRATION = "NARRATION"  # 화면 밖 목소리·진행자의 말: 녹음을 붙여 다시 만들 수 있다
    DIALOGUE = "DIALOGUE"  # 화면 속 인물의 대사: 입 모양과 맞물려 있다
    UNKNOWN = "UNKNOWN"  # 소리만으로는 가르지 못했다


class SoundKind(StrEnum):
    SILENCE = "SILENCE"
    MUSIC = "MUSIC"
    NOISE = "NOISE"
    OTHER = "OTHER"  # 말도 무음도 아닌 소리 (음악인지 소음인지 가르지 못했다)


@dataclass(frozen=True)
class SceneSpan:
    """장면(자리) 하나의 시간. 줄이 어느 장면에 걸치는지 정할 때 쓴다."""

    key: str
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class SoundSpan:
    start_ms: int
    end_ms: int
    kind: SoundKind
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "kind": str(self.kind),
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SoundSpan:
        return cls(
            int(data["start_ms"]),
            int(data["end_ms"]),
            SoundKind(data["kind"]),
            str(data.get("note", "")),
        )


@dataclass(frozen=True)
class SpokenLine:
    line_id: str  # "S1", "S2" … 글을 고쳐도 바뀌지 않는다
    start_ms: int
    end_ms: int
    text: str  # 지금의 글 (사람이 고쳤으면 고친 글)
    scene_keys: tuple[str, ...] = ()  # 걸치는 장면. 가장 많이 걸친 장면이 앞이다
    confidence: float | None = None  # 0..1. 인식기가 주지 않으면 None
    kind: LineKind = LineKind.UNKNOWN
    heard: str = ""  # 인식기가 적은 글. 비어 있으면 text 와 같다 (고친 적이 없다)
    words: tuple[Word, ...] = ()

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms

    @property
    def scene_key(self) -> str | None:
        """가장 많이 걸친 장면."""
        return self.scene_keys[0] if self.scene_keys else None

    @property
    def edited(self) -> bool:
        return bool(self.heard) and self.heard != self.text

    @property
    def chars(self) -> int:
        return spoken_characters(self.text)

    def with_text(self, text: str) -> SpokenLine:
        """글을 고친 줄. 인식기가 적은 글은 heard 에 남고, 낱말 시각은 옛 글의 것이라 버린다."""
        cleaned = " ".join(text.split())
        if not cleaned:
            raise ValidationError(f"대본 줄 '{self.line_id}' 의 글이 비어 있습니다")
        original = self.heard or self.text
        if cleaned == original:
            return replace(self, text=cleaned, heard="")
        return replace(self, text=cleaned, heard=original, words=())

    def with_kind(self, kind: LineKind) -> SpokenLine:
        return replace(self, kind=kind)

    def to_dict(self) -> dict[str, Any]:
        return {
            "line_id": self.line_id,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "text": self.text,
            "scene_keys": list(self.scene_keys),
            "confidence": self.confidence,
            "kind": str(self.kind),
            "heard": self.heard,
            "words": [w.to_dict() for w in self.words],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SpokenLine:
        confidence = data.get("confidence")
        return cls(
            line_id=str(data["line_id"]),
            start_ms=int(data["start_ms"]),
            end_ms=int(data["end_ms"]),
            text=str(data["text"]),
            scene_keys=tuple(str(key) for key in data.get("scene_keys", ())),
            confidence=float(confidence) if confidence is not None else None,
            kind=LineKind(data.get("kind", LineKind.UNKNOWN)),
            heard=str(data.get("heard", "")),
            words=tuple(
                Word(int(w["start_ms"]), int(w["end_ms"]), str(w.get("text", "")))
                for w in data.get("words", ())
            ),
        )


@dataclass(frozen=True)
class SpokenScript:
    language: str | None = None
    lines: tuple[SpokenLine, ...] = ()
    sounds: tuple[SoundSpan, ...] = ()
    duration_ms: int | None = None  # 소리 전체의 길이 (알 때만)
    notes: tuple[str, ...] = ()  # 줄로 삼지 않은 것과 그 까닭

    @property
    def is_empty(self) -> bool:
        """말소리가 없다 ('대본 없음')."""
        return not self.lines

    @property
    def text(self) -> str:
        return " ".join(line.text for line in self.lines)

    @property
    def chars(self) -> int:
        return sum(line.chars for line in self.lines)

    @property
    def speech_ms(self) -> int:
        return sum(max(0, line.duration_ms) for line in self.lines)

    @property
    def pace_cps(self) -> float | None:
        """말 빠르기: 초당 글자 수. 말이 없으면 (글자가 없으면) None. 0 이 되는 일은 없다."""
        if self.speech_ms <= 0 or self.chars <= 0:
            return None
        return self.chars / (self.speech_ms / 1000)

    def line(self, line_id: str) -> SpokenLine:
        for line in self.lines:
            if line.line_id == line_id:
                return line
        raise NotFound(f"대본 줄 '{line_id}' 을 찾을 수 없습니다", line_id=line_id)

    def in_scene(self, scene_key: str) -> tuple[SpokenLine, ...]:
        """그 장면이 주된 장면인 줄들."""
        return tuple(line for line in self.lines if line.scene_key == scene_key)

    def with_line(self, line: SpokenLine) -> SpokenScript:
        """같은 id 의 줄을 바꾼 대본. 없는 줄이면 NotFound."""
        self.line(line.line_id)
        return replace(
            self, lines=tuple(line if old.line_id == line.line_id else old for old in self.lines)
        )

    def validate(self) -> None:
        ids = [line.line_id for line in self.lines]
        if len(ids) != len(set(ids)):
            raise ValidationError(f"대본 줄의 id 가 중복됩니다: {ids}")
        previous_end = 0
        for line in self.lines:
            if not line.text.strip():
                raise ValidationError(f"대본 줄 '{line.line_id}' 의 글이 비어 있습니다")
            if line.start_ms < 0 or line.end_ms <= line.start_ms:
                raise ValidationError(
                    f"대본 줄 '{line.line_id}' 의 시간이 잘못되었습니다: "
                    f"{line.start_ms}~{line.end_ms} ms"
                )
            if line.start_ms < previous_end:
                raise ValidationError(
                    f"대본 줄 '{line.line_id}' 이 앞 줄과 겹치거나 시간 순서가 아닙니다"
                )
            previous_end = line.end_ms
        if self.duration_ms is not None and previous_end > self.duration_ms:
            raise ValidationError(
                f"대본 줄이 소리 길이({self.duration_ms} ms)를 넘습니다: {previous_end} ms"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "lines": [line.to_dict() for line in self.lines],
            "sounds": [span.to_dict() for span in self.sounds],
            "duration_ms": self.duration_ms,
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SpokenScript:
        duration = data.get("duration_ms")
        return cls(
            language=data.get("language"),
            lines=tuple(SpokenLine.from_dict(line) for line in data.get("lines", ())),
            sounds=tuple(SoundSpan.from_dict(span) for span in data.get("sounds", ())),
            duration_ms=int(duration) if duration is not None else None,
            notes=tuple(str(note) for note in data.get("notes", ())),
        )
