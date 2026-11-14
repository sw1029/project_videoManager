"""OpenRouter 영상 모델의 정보: 받는 길이 · 화면비 · 해상도, seed · 오디오 지원, 단가.

GET /api/v1/videos/models 의 항목에서 이 어댑터가 쓰는 것만 담는다. 요청하기 전에 지원 여부를 확인하고
(생성기의 _check_supported), 장면 길이에 맞는 길이를 고르고, 어림 비용을 계산한다.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from frame.application.errors import (
    GenerationError,
)


@dataclass(frozen=True)
class VideoModelInfo:
    """GET /api/v1/videos/models 의 항목 중 이 어댑터가 쓰는 필드."""

    id: str
    durations: tuple[int, ...] = ()
    aspect_ratios: tuple[str, ...] = ()
    resolutions: tuple[str, ...] = ()
    seed: bool = False
    audio: bool = False  # 오디오를 만들 수 있는 모델인가 (그렇다면 generate_audio=false 로 끈다)
    pricing: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> VideoModelInfo:
        return cls(
            id=str(data["id"]),
            durations=tuple(sorted(int(d) for d in data.get("supported_durations") or ())),
            aspect_ratios=tuple(str(a) for a in data.get("supported_aspect_ratios") or ()),
            resolutions=tuple(str(r) for r in data.get("supported_resolutions") or ()),
            seed=bool(data.get("seed")),
            audio=bool(data.get("generate_audio")),
            pricing={str(k): str(v) for k, v in (data.get("pricing_skus") or {}).items()},
        )

    def pick_duration(self, duration_ms: int) -> int:
        """장면 길이 이상인 가장 짧은 허용 길이(정수 초). 남는 부분은 정규화에서 잘라 낸다."""
        if not self.durations:
            return max(1, math.ceil(duration_ms / 1000))
        for seconds in self.durations:
            if seconds * 1000 >= duration_ms:
                return seconds
        raise GenerationError(
            f"장면 길이 {duration_ms} ms 가 모델 {self.id} 의 최대 길이 {self.durations[-1]}초를 넘습니다. "
            "장면을 나누세요",
            retryable=False,
        )

    def price_per_second(self, resolution: str) -> float | None:
        for sku in (f"duration_seconds_{resolution}", "duration_seconds"):
            raw = self.pricing.get(sku)
            if raw is not None:
                try:
                    return float(raw)
                except ValueError:
                    return None
        # 초당 센트로 적는 모델 (편집·업스케일 모델 등)
        for sku in (
            f"cents_per_second_output_{resolution}",
            "cents_per_second_output",
            f"cents_per_video_output_second_{resolution}",
        ):
            raw = self.pricing.get(sku)
            if raw is not None:
                try:
                    return float(raw) / 100
                except ValueError:
                    return None
        return None  # 토큰 단가 등 초 단가가 없는 모델
