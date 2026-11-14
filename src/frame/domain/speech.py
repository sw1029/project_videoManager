"""받아 적은 말소리: 구간(SpeechSegment)과 낱말(Word)의 시각이 붙은 글 (Transcript).

음성 인식의 결과다. 테마 추출(원본 영상의 말소리)과 녹음 붙이기(사람의 녹음)가 함께 쓰는 일반 타입이라
시리즈 도메인 밖에 둔다. 말이 없으면 구간이 비어 있다. 빠르기는 초당 글자 수(pace_cps)로 잰다.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from frame.domain.errors import ValidationError


@dataclass(frozen=True)
class Word:
    start_ms: int
    end_ms: int
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {"start_ms": self.start_ms, "end_ms": self.end_ms, "text": self.text}


@dataclass(frozen=True)
class SpeechSegment:
    start_ms: int
    end_ms: int
    text: str
    words: tuple[Word, ...] = ()  # 낱말 시각 (인식기가 줄 때만)

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "text": self.text,
            "words": [w.to_dict() for w in self.words],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SpeechSegment:
        return cls(
            start_ms=int(data["start_ms"]),
            end_ms=int(data["end_ms"]),
            text=str(data.get("text", "")),
            words=tuple(
                Word(int(w["start_ms"]), int(w["end_ms"]), str(w.get("text", "")))
                for w in data.get("words", ())
            ),
        )


def spoken_characters(text: str) -> int:
    """말한 분량: 글자·숫자의 수 (공백·문장부호 제외). 말 빠르기와 대본 길이 예산의 단위다."""
    return sum(1 for ch in unicodedata.normalize("NFC", text) if ch.isalnum())


@dataclass(frozen=True)
class Transcript:
    language: str | None = None
    segments: tuple[SpeechSegment, ...] = ()

    @property
    def text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments if s.text.strip())

    @property
    def is_empty(self) -> bool:
        return not self.text

    @property
    def speech_ms(self) -> int:
        return sum(max(0, s.duration_ms) for s in self.segments if s.text.strip())

    @property
    def pace_cps(self) -> float | None:
        """말 빠르기: 초당 글자 수 (공백·문장부호 제외). 말이 없으면 None."""
        if self.speech_ms <= 0:
            return None
        return spoken_characters(self.text) / (self.speech_ms / 1000)

    def within(self, start_ms: int, end_ms: int) -> tuple[SpeechSegment, ...]:
        """구간의 가운데가 [start_ms, end_ms) 안에 드는 말소리 구간."""
        return tuple(s for s in self.segments if start_ms <= (s.start_ms + s.end_ms) / 2 < end_ms)

    def validate(self, duration_ms: int | None = None) -> None:
        previous_end = 0
        for segment in self.segments:
            if segment.start_ms < 0 or segment.end_ms < segment.start_ms:
                raise ValidationError(
                    f"말소리 구간이 잘못되었습니다: {segment.start_ms}~{segment.end_ms} ms"
                )
            if segment.start_ms < previous_end:
                raise ValidationError(
                    f"말소리 구간이 시간 순서가 아니거나 겹칩니다: {segment.start_ms} ms"
                )
            previous_end = segment.end_ms
        if duration_ms is not None and previous_end > duration_ms:
            raise ValidationError(
                f"말소리 구간이 소리 길이({duration_ms} ms)를 넘습니다: {previous_end} ms"
            )

    def to_dict(self) -> dict[str, Any]:
        return {"language": self.language, "segments": [s.to_dict() for s in self.segments]}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Transcript:
        return cls(
            language=data.get("language"),
            segments=tuple(SpeechSegment.from_dict(s) for s in data.get("segments", ())),
        )
