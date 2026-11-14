"""원본 영상에서 읽어 낸 것 (모델을 부르기 전·후의 관찰값). 테마 추출의 재료이자 근거다 (ADR-0025).

- Shot: 컷과 컷 사이의 구간. 0 ms 에서 시작해 빈틈 없이 영상 끝까지 이어진다.
- ScreenText: 화면에 보인 문구 하나 (보인 시간·영역·종류).
- VideoAnalysis: 모델 없이 얻는 관찰값 묶음 (겉모습·컷·말소리). 테마 추출기의 입력이다.
받아 적은 말소리(Transcript)는 일반 타입이라 domain/speech.py 에 있다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from frame.domain.errors import ValidationError
from frame.domain.media import MediaInfo
from frame.domain.screen import Region, TextRole, TextSource, region_or_none
from frame.domain.speech import Transcript


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
