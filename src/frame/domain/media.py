"""영상의 겉모습: 크기·길이·fps·소리 유무·파일 크기·코덱.

영상 분석·테마·숏폼 규격 판정이 함께 쓰는 일반 타입이다. '숏폼 규격' 모듈에 두면 영상을 다루는 코드가
규격 판정에 기대게 된다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import gcd
from typing import Any

from frame.domain.errors import ValidationError

# 화면비를 견줄 때의 허용 오차 (480x854 = 0.5621 도 9:16 = 0.5625 로 본다)
ASPECT_TOLERANCE = 0.01


_COMMON_ASPECTS: tuple[tuple[int, int], ...] = (
    (9, 16),
    (16, 9),
    (1, 1),
    (4, 5),
    (5, 4),
    (3, 4),
    (4, 3),
)


@dataclass(frozen=True)
class MediaInfo:
    """영상 파일의 겉모습: 크기·길이·fps·오디오 유무. 규격 판정과 테마의 형식(FormatSpec)이 쓴다."""

    width: int
    height: int
    duration_ms: int
    fps: float | None = None
    has_audio: bool = False
    size_bytes: int | None = None
    video_codec: str | None = None

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValidationError(f"영상 크기가 잘못되었습니다: {self.width}x{self.height}")
        if self.duration_ms <= 0:
            raise ValidationError(f"영상 길이가 잘못되었습니다: {self.duration_ms} ms")

    @property
    def aspect_ratio(self) -> float:
        """가로 ÷ 세로. 9:16 이면 0.5625."""
        return self.width / self.height

    @property
    def orientation(self) -> str:
        if abs(self.aspect_ratio - 1.0) <= ASPECT_TOLERANCE:
            return "square"
        return "vertical" if self.aspect_ratio < 1.0 else "horizontal"

    @property
    def aspect_label(self) -> str:
        """'9:16' 같은 표기. 흔한 비율에 가까우면 그 이름을, 아니면 약분한 값을 쓴다."""
        for w, h in _COMMON_ASPECTS:
            if abs(self.aspect_ratio - w / h) <= ASPECT_TOLERANCE:
                return f"{w}:{h}"
        divisor = gcd(self.width, self.height)
        return f"{self.width // divisor}:{self.height // divisor}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "duration_ms": self.duration_ms,
            "fps": self.fps,
            "has_audio": self.has_audio,
            "size_bytes": self.size_bytes,
            "video_codec": self.video_codec,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MediaInfo:
        return cls(
            width=int(data["width"]),
            height=int(data["height"]),
            duration_ms=int(data["duration_ms"]),
            fps=float(data["fps"]) if data.get("fps") is not None else None,
            has_audio=bool(data.get("has_audio", False)),
            size_bytes=int(data["size_bytes"]) if data.get("size_bytes") is not None else None,
            video_codec=data.get("video_codec"),
        )
