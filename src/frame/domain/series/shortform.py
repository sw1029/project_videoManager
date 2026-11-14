"""'기준 내 숏폼' 의 판정 (ADR-0025): 플랫폼의 숏폼 규격(YouTube Shorts·Instagram Reels)과 견준다.

- 규격은 플랫폼마다 ShortformSpec 으로 적고, 출처와 확인한 날짜를 함께 둔다 (바뀌는 값이다).
- REQUIRED 를 어기면 그 플랫폼의 숏폼이 아니다. RECOMMENDED 를 어기면 숏폼이지만 경고한다.
- check_platforms 가 고른 플랫폼 전체에 대한 판정을 낸다. within 은 모두의 필수 기준 안이라는 뜻이다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from frame.domain.errors import ValidationError
from frame.domain.media import ASPECT_TOLERANCE, MediaInfo


class ShortformPlatform(StrEnum):
    YOUTUBE_SHORTS = "YOUTUBE_SHORTS"
    INSTAGRAM_REELS = "INSTAGRAM_REELS"


class CriterionLevel(StrEnum):
    REQUIRED = "REQUIRED"
    RECOMMENDED = "RECOMMENDED"


@dataclass(frozen=True)
class ShortformSpec:
    """플랫폼 하나의 숏폼 규격. None 인 항목은 그 플랫폼이 정하지 않은 것이다."""

    platform: ShortformPlatform
    label: str
    max_duration_ms: int  # REQUIRED
    min_duration_ms: int | None = None  # REQUIRED
    vertical_or_square: bool = False  # REQUIRED: 가로 ÷ 세로 가 1 이하
    recommended_aspect: tuple[int, int] | None = None  # RECOMMENDED (가로, 세로)
    fps_range: tuple[float, float] | None = None  # RECOMMENDED
    max_width: int | None = None  # RECOMMENDED
    max_size_bytes: int | None = None  # RECOMMENDED
    sources: tuple[str, ...] = ()
    checked_on: str = ""  # 출처를 확인한 날짜 (YYYY-MM-DD)


PLATFORM_SPECS: Mapping[ShortformPlatform, ShortformSpec] = {
    # "videos ... with a square or vertical aspect ratio up to three minutes in length will be categorized as Shorts"
    ShortformPlatform.YOUTUBE_SHORTS: ShortformSpec(
        platform=ShortformPlatform.YOUTUBE_SHORTS,
        label="YouTube Shorts",
        max_duration_ms=180_000,
        vertical_or_square=True,
        recommended_aspect=(9, 16),
        sources=(
            "https://support.google.com/youtube/answer/15424877",
            "https://support.google.com/google-ads/answer/16041697",
        ),
        checked_on="2026-11-14",
    ),
    # "Reels over 3 minutes won't be recommended to new audiences" / 게시 API: 3초~15분, 9:16 권장,
    # 23~60 fps, 가로 1920 이하, 300MB 이하.
    # 3분을 넘는 릴스도 올릴 수는 있지만 추천 대상이 아니므로 숏폼으로 보지 않는다.
    ShortformPlatform.INSTAGRAM_REELS: ShortformSpec(
        platform=ShortformPlatform.INSTAGRAM_REELS,
        label="Instagram Reels",
        max_duration_ms=180_000,
        min_duration_ms=3_000,
        recommended_aspect=(9, 16),
        fps_range=(23.0, 60.0),
        max_width=1920,
        max_size_bytes=300 * 1024 * 1024,
        sources=(
            "https://about.instagram.com/features/reels",
            "https://creators.instagram.com/blog/the-latest-with-instagram",
            "https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media",
        ),
        checked_on="2026-11-14",
    ),
}


@dataclass(frozen=True)
class CriterionResult:
    name: str
    level: CriterionLevel
    passed: bool
    actual: str
    limit: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "level": str(self.level),
            "passed": self.passed,
            "actual": self.actual,
            "limit": self.limit,
            "message": self.message,
        }


@dataclass(frozen=True)
class ShortformReport:
    platform: ShortformPlatform
    label: str
    results: tuple[CriterionResult, ...]

    @property
    def within(self) -> bool:
        """REQUIRED 기준을 모두 지켰는가."""
        return all(r.passed for r in self.results if r.level is CriterionLevel.REQUIRED)

    @property
    def violations(self) -> tuple[CriterionResult, ...]:
        return tuple(r for r in self.results if not r.passed and r.level is CriterionLevel.REQUIRED)

    @property
    def warnings(self) -> tuple[CriterionResult, ...]:
        return tuple(
            r for r in self.results if not r.passed and r.level is CriterionLevel.RECOMMENDED
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": str(self.platform),
            "label": self.label,
            "within": self.within,
            "results": [r.to_dict() for r in self.results],
        }


@dataclass(frozen=True)
class ShortformVerdict:
    """고른 플랫폼 전체에 대한 판정. within 이 '기준 내 숏폼' 이다."""

    media: MediaInfo
    reports: tuple[ShortformReport, ...]

    @property
    def within(self) -> bool:
        return all(report.within for report in self.reports)

    @property
    def within_any(self) -> bool:
        return any(report.within for report in self.reports)

    def to_dict(self) -> dict[str, Any]:
        return {
            "within": self.within,
            "within_any": self.within_any,
            "media": self.media.to_dict(),
            "reports": [report.to_dict() for report in self.reports],
        }


def check_shortform(info: MediaInfo, spec: ShortformSpec) -> ShortformReport:
    required, recommended = CriterionLevel.REQUIRED, CriterionLevel.RECOMMENDED
    seconds = info.duration_ms / 1000
    results = [
        CriterionResult(
            "max_duration",
            required,
            info.duration_ms <= spec.max_duration_ms,
            f"{seconds:g}s",
            f"<= {spec.max_duration_ms / 1000:g}s",
            f"{spec.label}: 길이 {seconds:g}초 (상한 {spec.max_duration_ms / 1000:g}초)",
        )
    ]
    if spec.min_duration_ms is not None:
        results.append(
            CriterionResult(
                "min_duration",
                required,
                info.duration_ms >= spec.min_duration_ms,
                f"{seconds:g}s",
                f">= {spec.min_duration_ms / 1000:g}s",
                f"{spec.label}: 길이 {seconds:g}초 (하한 {spec.min_duration_ms / 1000:g}초)",
            )
        )
    if spec.vertical_or_square:
        results.append(
            CriterionResult(
                "orientation",
                required,
                info.aspect_ratio <= 1.0 + ASPECT_TOLERANCE,
                f"{info.aspect_label} ({info.orientation})",
                "square or vertical",
                f"{spec.label}: 화면비 {info.aspect_label} (세로 또는 정사각이어야 숏폼으로 분류)",
            )
        )
    if spec.recommended_aspect is not None:
        w, h = spec.recommended_aspect
        results.append(
            CriterionResult(
                "recommended_aspect_ratio",
                recommended,
                abs(info.aspect_ratio - w / h) <= ASPECT_TOLERANCE,
                info.aspect_label,
                f"{w}:{h}",
                f"{spec.label}: 화면비 {info.aspect_label} (권장 {w}:{h}, 아니면 잘리거나 여백이 생김)",
            )
        )
    if spec.fps_range is not None and info.fps is not None:
        low, high = spec.fps_range
        results.append(
            CriterionResult(
                "frame_rate",
                recommended,
                low <= info.fps <= high,
                f"{info.fps:g} fps",
                f"{low:g}-{high:g} fps",
                f"{spec.label}: {info.fps:g} fps (업로드 규격 {low:g}~{high:g} fps)",
            )
        )
    if spec.max_width is not None:
        results.append(
            CriterionResult(
                "max_width",
                recommended,
                info.width <= spec.max_width,
                f"{info.width}px",
                f"<= {spec.max_width}px",
                f"{spec.label}: 가로 {info.width}px (업로드 규격 {spec.max_width}px 이하)",
            )
        )
    if spec.max_size_bytes is not None and info.size_bytes is not None:
        megabytes, limit = info.size_bytes / 1024 / 1024, spec.max_size_bytes / 1024 / 1024
        results.append(
            CriterionResult(
                "max_size",
                recommended,
                info.size_bytes <= spec.max_size_bytes,
                f"{megabytes:.1f}MB",
                f"<= {limit:g}MB",
                f"{spec.label}: 파일 {megabytes:.1f}MB (업로드 규격 {limit:g}MB 이하)",
            )
        )
    return ShortformReport(spec.platform, spec.label, tuple(results))


def check_platforms(
    info: MediaInfo,
    platforms: Sequence[ShortformPlatform] = tuple(ShortformPlatform),
    specs: Mapping[ShortformPlatform, ShortformSpec] = PLATFORM_SPECS,
) -> ShortformVerdict:
    """고른 플랫폼 모두의 규격으로 판정한다. 모두 지켜야 '기준 내' 다."""
    if not platforms:
        raise ValidationError("숏폼 규격을 판정할 플랫폼이 하나 이상 있어야 합니다")
    return ShortformVerdict(
        media=info,
        reports=tuple(check_shortform(info, specs[ShortformPlatform(p)]) for p in platforms),
    )
