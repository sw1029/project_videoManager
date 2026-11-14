"""테마를 뽑은 원본 영상의 기록 (ADR-0025).

외부 영상은 사본을 두지 않는다: 지문(sha256)·겉모습·출처·라이선스만 남긴다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from frame.domain.series.shortform import MediaInfo


class SourceOrigin(StrEnum):
    FRAME_GENERATED = "FRAME_GENERATED"  # FRAME 이 만든 영상 (나오는 인물은 합성 인물)
    EXTERNAL = "EXTERNAL"  # 밖에서 온 영상 (실존 인물이 나올 수 있다)


@dataclass(frozen=True)
class SourceVideo:
    """테마를 뽑은 영상의 출처 기록. 영상 자체는 담지 않는다 (지문과 겉모습만)."""

    sha256: str
    media: MediaInfo
    origin: SourceOrigin = SourceOrigin.EXTERNAL
    title: str = ""
    source_url: str | None = None
    license: str | None = None
    attribution: str | None = None
    stored_uri: str | None = None  # 저장소에 사본을 둔 경우 (외부 영상은 기본으로 두지 않는다)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sha256": self.sha256,
            "media": self.media.to_dict(),
            "origin": str(self.origin),
            "title": self.title,
            "source_url": self.source_url,
            "license": self.license,
            "attribution": self.attribution,
            "stored_uri": self.stored_uri,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SourceVideo:
        return cls(
            sha256=str(data["sha256"]),
            media=MediaInfo.from_dict(data["media"]),
            origin=SourceOrigin(data.get("origin", SourceOrigin.EXTERNAL)),
            title=str(data.get("title", "")),
            source_url=data.get("source_url"),
            license=data.get("license"),
            attribution=data.get("attribution"),
            stored_uri=data.get("stored_uri"),
        )
