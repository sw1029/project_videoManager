"""영상에서 읽어 낸 것 (모델을 부르기 전·후의 관찰값). 테마 추출의 재료이자 근거다 (ADR-0025).

- Shot: 컷과 컷 사이의 구간. 0 ms 에서 시작해 빈틈 없이 영상 끝까지 이어진다.
- Transcript: 말소리를 받아 적은 것 (구간·낱말 시각 포함). 말이 없으면 구간이 비어 있다.
- ScreenText: 화면에 보인 문구 하나 (보인 시간·영역·종류).
- VideoAnalysis: 모델 없이 얻는 관찰값 묶음 (겉모습·컷·말소리). 테마 추출기의 입력이다.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .errors import ValidationError
from .screen import Region, TextRole, TextSource, region_or_none
from .shortform import MediaInfo


@dataclass(frozen=True)
class Shot:
    index: int
    start_ms: int
    end_ms: int

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms

    def to_dict(self) -> dict[str, int]:
        return {"index": self.index, "start_ms": self.start_ms, "end_ms": self.end_ms}


def shots_from_cuts(
    cut_times_ms: Sequence[int], duration_ms: int, *, min_shot_ms: int = 0
) -> tuple[Shot, ...]:
    """컷 시각 목록으로 샷을 만든다. min_shot_ms 보다 짧게 남는 컷은 버린다 (앞 샷에 붙인다).

    결과는 0 ms 에서 시작해 duration_ms 에서 끝나고 서로 이어진다. 컷이 없으면 영상 전체가 한 샷이다.
    """
    if duration_ms <= 0:
        raise ValidationError(f"영상 길이가 잘못되었습니다: {duration_ms} ms")
    kept: list[int] = []
    previous = 0
    for cut in sorted({int(c) for c in cut_times_ms}):
        if cut <= 0 or cut >= duration_ms:
            continue
        if cut - previous < min_shot_ms:
            continue
        kept.append(cut)
        previous = cut
    if kept and duration_ms - kept[-1] < min_shot_ms:
        kept.pop()  # 마지막 샷이 너무 짧으면 앞 샷에 붙인다
    edges = [0, *kept, duration_ms]
    return tuple(Shot(i, edges[i], edges[i + 1]) for i in range(len(edges) - 1))


def validate_shots(shots: Sequence[Shot], duration_ms: int | None = None) -> None:
    """샷은 0 ms 에서 시작해 빈틈·겹침 없이 이어져야 한다 (duration_ms 를 주면 거기서 끝나야 한다)."""
    if not shots:
        raise ValidationError("샷이 하나 이상 있어야 합니다")
    expected = 0
    for shot in shots:
        if shot.start_ms != expected or shot.end_ms <= shot.start_ms:
            raise ValidationError(
                f"샷 {shot.index} 의 구간이 잘못되었습니다: {shot.start_ms}~{shot.end_ms} ms "
                f"(시작은 {expected} ms 여야 합니다)"
            )
        expected = shot.end_ms
    if duration_ms is not None and expected != duration_ms:
        raise ValidationError(
            f"샷이 영상 끝({duration_ms} ms)이 아니라 {expected} ms 에서 끝납니다"
        )


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


@dataclass(frozen=True)
class ScreenText:
    """화면에 보인 문구 하나."""

    text: str
    start_ms: int
    end_ms: int
    region: Region | None = None
    role: TextRole = TextRole.OTHER
    source: TextSource = TextSource.OVERLAY
    style: str = ""  # 글자 색·배경·굵기 같은 겉모습 (글)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "region": self.region.to_dict() if self.region else None,
            "role": str(self.role),
            "source": str(self.source),
            "style": self.style,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ScreenText:
        return cls(
            text=str(data["text"]),
            start_ms=int(data["start_ms"]),
            end_ms=int(data["end_ms"]),
            region=region_or_none(data.get("region")),
            role=TextRole(data.get("role", TextRole.OTHER)),
            source=TextSource(data.get("source", TextSource.OVERLAY)),
            style=str(data.get("style", "")),
        )


@dataclass(frozen=True)
class VideoAnalysis:
    """모델 없이 얻는 관찰값: 겉모습·컷·말소리. 말소리를 인식하지 않았으면 transcript 는 None 이다."""

    media: MediaInfo
    shots: tuple[Shot, ...]
    transcript: Transcript | None = None

    def validate(self) -> None:
        validate_shots(self.shots, self.media.duration_ms)
        if self.transcript is not None:
            self.transcript.validate()

    def to_dict(self) -> dict[str, Any]:
        return {
            "media": self.media.to_dict(),
            "shots": [s.to_dict() for s in self.shots],
            "transcript": self.transcript.to_dict() if self.transcript is not None else None,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> VideoAnalysis:
        transcript = data.get("transcript")
        return cls(
            media=MediaInfo.from_dict(data["media"]),
            shots=tuple(
                Shot(int(s["index"]), int(s["start_ms"]), int(s["end_ms"])) for s in data["shots"]
            ),
            transcript=Transcript.from_dict(transcript) if transcript is not None else None,
        )
