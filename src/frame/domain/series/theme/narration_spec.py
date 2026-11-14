"""테마의 내레이션·음악 기록 (ADR-0025·0026).

말소리가 있었는가, 언어·말투·빠르기(초당 글자 수), 자리마다의 구실. 빠르기는 녹음 대본의 길이 예산이 된다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class NarrationBeat:
    """자리 하나에서 하는 말의 구실과 원본의 예."""

    slot_key: str
    purpose: str = ""
    example: str = ""
    # 원본의 대본에서 잰 것 (ADR-0028). 대본 없이 뽑은 테마에는 없다
    chars: int | None = None  # 말한 글자 수 (공백·문장부호 제외)
    spoken_ms: int | None = None  # 말한 시간
    lead_in_ms: int | None = None  # 자리가 시작하고 말이 나오기까지
    line_ids: tuple[str, ...] = ()  # 근거가 된 대본의 줄

    def to_dict(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "slot_key": self.slot_key,
            "purpose": self.purpose,
            "example": self.example,
        }
        # 잰 값이 있을 때만 적는다 (대본 없이 뽑은 옛 테마의 문서·지문이 바뀌지 않게)
        if self.chars is not None:
            document["chars"] = self.chars
        if self.spoken_ms is not None:
            document["spoken_ms"] = self.spoken_ms
        if self.lead_in_ms is not None:
            document["lead_in_ms"] = self.lead_in_ms
        if self.line_ids:
            document["line_ids"] = list(self.line_ids)
        return document

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> NarrationBeat:
        def number(key: str) -> int | None:
            return int(data[key]) if data.get(key) is not None else None

        return cls(
            str(data["slot_key"]),
            str(data.get("purpose", "")),
            str(data.get("example", "")),
            chars=number("chars"),
            spoken_ms=number("spoken_ms"),
            lead_in_ms=number("lead_in_ms"),
            line_ids=tuple(str(i) for i in data.get("line_ids", ())),
        )


@dataclass(frozen=True)
class NarrationSpec:
    present: bool = False  # 원본에 말소리가 있었는가
    language: str | None = None
    tone: str = ""  # 말투 (예: 친근한 존댓말, 빠르고 밝게)
    pace_cps: float | None = None  # 초당 글자 수. 녹음 대본의 길이 예산에 쓴다
    beats: tuple[NarrationBeat, ...] = ()

    def beat(self, slot_key: str) -> NarrationBeat | None:
        return next((b for b in self.beats if b.slot_key == slot_key), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "present": self.present,
            "language": self.language,
            "tone": self.tone,
            "pace_cps": self.pace_cps,
            "beats": [b.to_dict() for b in self.beats],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> NarrationSpec:
        if not data:
            return cls()
        pace = data.get("pace_cps")
        return cls(
            present=bool(data.get("present", False)),
            language=data.get("language"),
            tone=str(data.get("tone", "")),
            pace_cps=float(pace) if pace is not None else None,
            beats=tuple(NarrationBeat.from_dict(b) for b in data.get("beats", ())),
        )


@dataclass(frozen=True)
class MusicSpec:
    present: bool | None = None  # None 이면 알 수 없음 (소리를 분석하지 않았다)
    mood: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"present": self.present, "mood": self.mood, "notes": self.notes}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> MusicSpec:
        if not data:
            return cls()
        return cls(data.get("present"), str(data.get("mood", "")), str(data.get("notes", "")))
