"""숏폼 규격 판정 (플랫폼 기준). '기준 내 숏폼' 인지 먼저 가린 뒤에 테마를 뽑는다 (ADR-0025)."""

from __future__ import annotations

import pytest

from frame.domain.errors import ValidationError
from frame.domain.shortform import (
    PLATFORM_SPECS,
    CriterionLevel,
    MediaInfo,
    ShortformPlatform,
    check_platforms,
    check_shortform,
)

SHORTS = PLATFORM_SPECS[ShortformPlatform.YOUTUBE_SHORTS]
REELS = PLATFORM_SPECS[ShortformPlatform.INSTAGRAM_REELS]


def _media(width=1080, height=1920, seconds=30.0, fps=30.0, **extra) -> MediaInfo:
    return MediaInfo(width=width, height=height, duration_ms=int(seconds * 1000), fps=fps, **extra)


def _failed(report, level=None) -> set[str]:
    return {r.name for r in report.results if not r.passed and (level is None or r.level is level)}


def test_vertical_thirty_second_video_is_within_both_platforms():
    verdict = check_platforms(_media())

    assert verdict.within and verdict.within_any
    assert [r.platform for r in verdict.reports] == list(ShortformPlatform)
    assert all(report.within and not report.warnings for report in verdict.reports)


def test_frame_output_size_counts_as_nine_by_sixteen():
    """FRAME 이 만드는 480x854 (9:16 480p) 도 9:16 권장 비율로 본다 (픽셀 반올림 오차 허용)."""
    verdict = check_platforms(_media(480, 854, seconds=16))

    assert verdict.within
    assert all(not report.warnings for report in verdict.reports)


@pytest.mark.parametrize(("seconds", "within"), [(180.0, True), (180.5, False), (600.0, False)])
def test_three_minutes_is_the_upper_bound(seconds, within):
    verdict = check_platforms(_media(seconds=seconds))

    assert verdict.within is within
    if not within:
        assert all("max_duration" in _failed(report) for report in verdict.reports)


def test_landscape_video_is_not_a_short_but_is_accepted_as_a_reel_with_a_warning():
    info = _media(1920, 1080)
    shorts, reels = check_shortform(info, SHORTS), check_shortform(info, REELS)

    assert not shorts.within and _failed(shorts, CriterionLevel.REQUIRED) == {"orientation"}
    assert reels.within and "recommended_aspect_ratio" in _failed(reels, CriterionLevel.RECOMMENDED)
    verdict = check_platforms(info)
    assert not verdict.within and verdict.within_any


def test_square_video_is_a_short():
    report = check_shortform(_media(1080, 1080, seconds=60), SHORTS)

    assert report.within
    assert _failed(report) == {"recommended_aspect_ratio"}  # 세로 9:16 이 권장이라 경고만 남는다
    assert [w.name for w in report.warnings] == ["recommended_aspect_ratio"]


def test_reels_need_at_least_three_seconds():
    info = _media(seconds=2)

    assert check_shortform(info, SHORTS).within
    assert _failed(check_shortform(info, REELS), CriterionLevel.REQUIRED) == {"min_duration"}


def test_recommended_limits_only_warn():
    info = _media(2160, 3840, fps=15.0, size_bytes=400 * 1024 * 1024)
    report = check_shortform(info, REELS)

    assert report.within
    assert _failed(report, CriterionLevel.RECOMMENDED) == {"frame_rate", "max_width", "max_size"}


def test_unknown_values_are_not_judged():
    """fps·파일 크기를 모르면 그 기준은 판정하지 않는다 (통과도 실패도 아니다)."""
    report = check_shortform(_media(fps=None), REELS)

    assert "frame_rate" not in {r.name for r in report.results}
    assert "max_size" not in {r.name for r in report.results}


def test_only_selected_platforms_are_checked():
    verdict = check_platforms(_media(1920, 1080), platforms=(ShortformPlatform.INSTAGRAM_REELS,))

    assert verdict.within and len(verdict.reports) == 1
    with pytest.raises(ValidationError):
        check_platforms(_media(), platforms=())


def test_media_info_describes_shape_and_rejects_nonsense():
    assert _media(1080, 1920).orientation == "vertical"
    assert _media(1080, 1080).orientation == "square"
    assert _media(1920, 1080).orientation == "horizontal"
    assert _media(1080, 1920).aspect_ratio == pytest.approx(0.5625)
    assert _media(1080, 1920).aspect_label == "9:16"
    assert _media(480, 854).aspect_label == "9:16"
    assert _media(1920, 1080).aspect_label == "16:9"
    assert _media(1000, 1100).aspect_label == "10:11"
    for bad in ({"width": 0}, {"height": -1}, {"seconds": 0}):
        with pytest.raises(ValidationError):
            _media(**bad)


def test_media_info_and_reports_round_trip_as_plain_data():
    info = _media(has_audio=True, size_bytes=1234, video_codec="h264")
    assert MediaInfo.from_dict(info.to_dict()) == info

    data = check_platforms(_media(1920, 1080)).to_dict()
    assert data["within"] is False and data["within_any"] is True
    shorts = data["reports"][0]
    assert shorts["platform"] == "YOUTUBE_SHORTS" and shorts["within"] is False
    failed = [r for r in shorts["results"] if not r["passed"]]
    assert failed[0]["name"] == "orientation" and failed[0]["level"] == "REQUIRED"
    assert failed[0]["message"]  # 사람이 읽는 설명이 있다


def test_specs_record_where_the_numbers_come_from():
    """규격 숫자는 플랫폼이 바꾼다. 출처와 확인한 날짜를 함께 둔다."""
    for spec in PLATFORM_SPECS.values():
        assert spec.max_duration_ms == 180_000
        assert all(url.startswith("https://") for url in spec.sources) and spec.sources
        assert spec.checked_on == "2026-11-14"
