"""테스트·오프라인용 후반 합성 어댑터. 실제 영상 대신 '무엇을 얹었는지' 를 적은 JSON 을 만든다.

- FakeOverlayRenderer : 문구가 없으면 클립 그대로, 있으면 클립의 지문과 얹은 문구를 적은 산출물.
- FakeScreenTextReader: 그 산출물에서 그 시각·그 영역에 얹힌 문구를 돌려준다. misread 로 '잘못 읽히는 문구' 를
  주입해 글자가 깨져 나온 경우를 재현한다 (overlay_id -> 화면에 실제로 보이는 글).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from frame.application.ports.compositor import OverlayRenderer, ScreenTextReader
from frame.domain.episode import OverlayItem
from frame.domain.screen import Region

FAKE_COMPOSITE_KEY = "fake_composite"
_SAME_REGION_IOU = 0.5


def decode_fake_composite(clip: bytes) -> dict[str, Any] | None:
    try:
        document = json.loads(clip.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    composite = document.get(FAKE_COMPOSITE_KEY) if isinstance(document, dict) else None
    return composite if isinstance(composite, dict) else None


class FakeOverlayRenderer(OverlayRenderer):
    def __init__(self, error: Exception | None = None, signature: str = "fake") -> None:
        self._error = error
        self._signature = signature
        self.renders: list[tuple[OverlayItem, ...]] = []  # 스파이: 합성한 문구 묶음

    @property
    def signature(self) -> str:
        return self._signature

    @signature.setter
    def signature(self, value: str) -> None:
        self._signature = value  # 테스트: 글꼴을 바꾼 것처럼 설정을 바꾼다

    def render(self, clip: bytes, overlays: Sequence[OverlayItem]) -> bytes:
        if not overlays:
            return clip
        if self._error is not None:
            raise self._error
        self.renders.append(tuple(overlays))
        base = decode_fake_composite(clip)
        composite = {
            # 이미 합성한 클립에 다시 얹어도 바탕 클립은 같은 것으로 적는다
            "clip_sha256": base["clip_sha256"] if base else hashlib.sha256(clip).hexdigest(),
            "overlays": [*(base["overlays"] if base else []), *(o.to_dict() for o in overlays)],
        }
        return json.dumps(
            {FAKE_COMPOSITE_KEY: composite}, ensure_ascii=False, sort_keys=True
        ).encode("utf-8")


class FakeScreenTextReader(ScreenTextReader):
    def __init__(
        self, misread: Mapping[str, str] | None = None, error: Exception | None = None
    ) -> None:
        self._misread = dict(misread or {})
        self._error = error
        self.calls: list[tuple[int, tuple[Region, ...]]] = []

    def read(self, clip: bytes, at_ms: int, regions: Sequence[Region]) -> tuple[str, ...]:
        self.calls.append((at_ms, tuple(regions)))
        if self._error is not None:
            raise self._error
        composite = decode_fake_composite(clip)
        items = [OverlayItem.from_dict(o) for o in composite["overlays"]] if composite else []
        return tuple(self._seen(items, at_ms, region) for region in regions)

    def _seen(self, items: Sequence[OverlayItem], at_ms: int, region: Region) -> str:
        shown = [
            item
            for item in items
            if item.start_ms <= at_ms < item.end_ms and item.region.iou(region) >= _SAME_REGION_IOU
        ]
        if not shown:
            return ""
        top = shown[-1]  # 나중에 얹은 것이 위에 보인다
        return self._misread.get(top.overlay_id, top.text)
