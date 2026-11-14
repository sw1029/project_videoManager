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

    def to_dict(self) -> dict[str, str]:
        return {"slot_key": self.slot_key, "purpose": self.purpose, "example": self.example}


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
            beats=tuple(
                NarrationBeat(
                    str(b["slot_key"]), str(b.get("purpose", "")), str(b.get("example", ""))
                )
                for b in data.get("beats", ())
            ),
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
