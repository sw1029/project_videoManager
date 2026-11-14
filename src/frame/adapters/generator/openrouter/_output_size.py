"""출력 크기: 해상도 표기와 화면비에서 정규화할 클립의 (가로, 세로)를 정한다. 생성기와 조립기가 같은 값을 쓴다."""

from __future__ import annotations

from frame.application.errors import (
    ConfigurationError,
)

_SHORT_SIDE = {
    "360p": 360,
    "480p": 480,
    "720p": 720,
    "768p": 768,
    "1080p": 1080,
    "1K": 1080,
    "2K": 1440,
    "4K": 2160,
}


def output_size(resolution: str, aspect_ratio: str) -> tuple[int, int]:
    """정규화할 클립 크기. 짧은 변 = 해상도 표기(480p -> 480), 긴 변은 화면비로 계산해 짝수로 맞춘다.

    예) 9:16 480p -> 480x854, 9:16 720p -> 720x1280
    """
    short = _SHORT_SIDE.get(resolution)
    if short is None:
        raise ConfigurationError(
            f"지원하지 않는 해상도 표기입니다: {resolution} (사용 가능: {', '.join(_SHORT_SIDE)})"
        )
    try:
        w, h = (int(part) for part in aspect_ratio.split(":"))
    except ValueError as exc:
        raise ConfigurationError(
            f"화면비 형식이 잘못되었습니다: {aspect_ratio} (예: 9:16)"
        ) from exc
    if w <= 0 or h <= 0:
        raise ConfigurationError(f"화면비 형식이 잘못되었습니다: {aspect_ratio} (예: 9:16)")
    if w < h:
        return short, _even(short * h / w)
    if w > h:
        return _even(short * w / h), short
    return short, short


def _even(value: float) -> int:
    return int(round(value / 2) * 2)
