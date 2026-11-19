"""추적기 테스트 도우미: 움직이는 색 사각형 합성 클립(정답 박스 포함)과 정해진 박스를 돌려주는 위치 찾기.

OpenCV·numpy(tracking extra)가 있어야 클립을 만든다. 쓰는 테스트는 먼저 pytest.importorskip("cv2") 한다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from frame.adapters.shared.media.frames import Box
from frame.adapters.tracker import EntityLocator
from frame.adapters.tracker.locator import Located, Verified
from frame.application.ports.llm import ImageInput
from frame.domain.ir import EntitySpec, SceneSpec

FPS = 30
SIZE = (320, 240)  # 가로 x 세로
BACKGROUND = 90  # 회색 배경


@dataclass(frozen=True)
class Square:
    """클립 안에서 왼쪽에서 오른쪽으로 움직이는 사각형 하나."""

    color: tuple[int, int, int]  # BGR
    y: int
    side: int = 40
    start_x: int = 20
    travel: int = 200  # 클립 동안 오른쪽으로 가는 거리(px)
    visible_from_ms: int = 0
    visible_until_ms: int | None = None


def write_squares(
    path: Path,
    squares: Mapping[str, Square],
    duration_ms: int,
    *,
    cut_at_ms: int | None = None,
) -> dict[str, dict[int, Box]]:
    """클립을 쓰고 정답 박스(키 -> 클립 안 시각 -> 비율 박스)를 돌려준다.

    cut_at_ms: 그 시각부터 배경이 짙은 파랑으로 바뀐다 (한 클립 안의 컷 — 생성 모델이 장면 안에 넣기도 한다).
    """
    import cv2  # noqa: PLC0415 - tracking extra (쓰는 테스트가 importorskip 한다)
    import numpy as np  # noqa: PLC0415

    width, height = SIZE
    frames = duration_ms * FPS // 1000
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, SIZE)
    truth: dict[str, dict[int, Box]] = {key: {} for key in squares}
    for index in range(frames):
        t = round(index * 1000 / FPS)
        image = np.full((height, width, 3), BACKGROUND, np.uint8)
        if cut_at_ms is not None and t >= cut_at_ms:
            image[:] = (120, 40, 20)  # BGR 짙은 파랑
        for key, square in squares.items():
            until = square.visible_until_ms if square.visible_until_ms is not None else duration_ms
            if not square.visible_from_ms <= t < until:
                continue
            x = square.start_x + square.travel * index // frames
            cv2.rectangle(
                image, (x, square.y), (x + square.side, square.y + square.side), square.color, -1
            )
            # 무늬가 있어야 추적기가 붙잡을 특징이 생긴다
            middle = (x + square.side // 2, square.y + square.side // 2)
            cv2.circle(image, middle, square.side // 5, (255, 255, 255), -1)
            truth[key][t] = Box(
                x / width, square.y / height, square.side / width, square.side / height
            )
        writer.write(image)
    writer.release()
    return truth


def iou(a: Box, b: Box) -> float:
    left, top = max(a.x, b.x), max(a.y, b.y)
    right, bottom = min(a.x + a.w, b.x + b.w), min(a.y + a.h, b.y + b.h)
    inter = max(0.0, right - left) * max(0.0, bottom - top)
    union = a.w * a.h + b.w * b.h - inter
    return inter / union if union > 0 else 0.0


@dataclass
class ScriptedLocator(EntityLocator):
    """찾을 때마다 answers 를 앞에서부터 하나씩 돌려준다 (다 쓰면 못 찾음). 확인은 verdicts (없으면 yes)."""

    # 찾을 때마다 하나씩: 객체 -> 박스. None 은 'VLM 이 보이지 않는다고 답함' (absent)
    answers: list[Mapping[str, Box | None]] = field(default_factory=list)
    verdicts: Mapping[str, str] = field(default_factory=dict)
    locate_error: Exception | None = None
    verify_error: Exception | None = None
    located: list[list[str]] = field(default_factory=list)  # 찾을 때마다 물어본 객체
    verified: list[list[str]] = field(default_factory=list)

    def locate(
        self, frame: ImageInput, entities: Sequence[EntitySpec], scene: SceneSpec
    ) -> Located:
        self.located.append([e.entity_key for e in entities])
        if self.locate_error is not None:
            raise self.locate_error
        answer = self.answers.pop(0) if self.answers else {}
        wanted = {e.entity_key for e in entities}
        boxes = {key: box for key, box in answer.items() if key in wanted and box is not None}
        absent = frozenset(key for key, box in answer.items() if key in wanted and box is None)
        return Located(boxes, "scripted", {"input_tokens": 100, "output_tokens": 10}, absent)

    def verify(self, crops: Sequence[tuple[EntitySpec, ImageInput]], scene: SceneSpec) -> Verified:
        self.verified.append([e.entity_key for e, _ in crops])
        if self.verify_error is not None:
            raise self.verify_error
        assert all(image.data[:2] == b"\xff\xd8" for _, image in crops), "잘라 낸 이미지는 JPEG 다"
        verdicts = {e.entity_key: self.verdicts.get(e.entity_key, "yes") for e, _ in crops}
        return Verified(verdicts, dict.fromkeys(verdicts, "test"), "scripted", {"input_tokens": 50})
