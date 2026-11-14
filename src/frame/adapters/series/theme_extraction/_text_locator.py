"""화면의 글자가 어디에 있는지 이미지를 읽는 LLM 에 묻는다 (ADR-0025·0027). 어댑터들이 함께 쓰는 도구.

프레임 한 장에 글자 하나만 묻는다. 문구 대조군(정답 영역이 있는 영상)에서 재 본 결과다 (docs/research/theme-series.md).
- 프레임 여러 장을 주고 문구 여러 개의 상자를 한 번에 물으면 상자가 어긋난다 (위·아래 구분만 맞는 정도).
- 프레임 한 장에 문구 하나를 물으면 훨씬 낫다.
- 프레임에 눈금자·격자를 그려 눈금을 읽게 하는 것과, 한 번 찾은 곳을 확대해 다시 묻는 것은 그보다 낫지 않았다.

돌려주는 영역은 화면 안의 정규화 좌표다. 찾지 못했거나 상자가 말이 되지 않으면 None 이다 (예외가 아니다).
호출 실패는 LLMError 로 올라간다. 부른 쪽이 정한다 (추출은 처음 받은 영역을 그대로 둔다).
"""

from __future__ import annotations

import json
import math
from typing import Any

from frame.application.errors import LLMBadResponse
from frame.application.ports.llm import ImageInput, JsonCompletion, LLMClient
from frame.domain.screen import Region

LOCATE_SCHEMA_NAME = "text_box"
MIN_SIDE = 0.01  # 이보다 좁거나 낮은 상자는 글자의 영역으로 보지 않는다
_SLACK = 0.1  # 화면 밖으로 이만큼까지 나간 값은 화면 가장자리로 당긴다. 더 나가면 믿지 않는다

LOCATE_SYSTEM_PROMPT = (
    "You locate text on a video frame. You are given one frame and a description of one piece of "
    "text that is visible on it. Report the bounding box of that text. If it is a caption drawn "
    "over the picture, report the caption band: the text together with the box or strip drawn "
    "behind it. If it is text that is part of the scene (printed on an object, a sign), report "
    "the box of the text itself. Use fractions of the frame: top and bottom from 0 (top edge) to "
    "1 (bottom edge), left and right from 0 (left edge) to 1 (right edge). If the text is not "
    "visible in this frame, set found to false."
)

LOCATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["found", "top", "bottom", "left", "right"],
    "properties": {
        "found": {"type": "boolean"},
        "top": {"type": "number"},
        "bottom": {"type": "number"},
        "left": {"type": "number"},
        "right": {"type": "number"},
    },
}


def locate_text(
    llm: LLMClient, frame: ImageInput, target: str, *, timeout_s: float | None = None
) -> tuple[Region | None, JsonCompletion]:
    """frame 에서 target(찾을 글자의 설명: 'caption "오늘의 메뉴"', 'the text printed on the cup')의 영역.

    (영역 또는 None, 모델의 응답)을 돌려준다. 응답은 토큰 수를 적는 데 쓴다.
    """
    completion = llm.complete_json(
        LOCATE_SYSTEM_PROMPT,
        f"Text to locate: {target}. Report its box.",
        LOCATE_SCHEMA,
        schema_name=LOCATE_SCHEMA_NAME,
        timeout_s=timeout_s,
        images=[frame],
    )
    try:
        answer = json.loads(completion.text)
    except ValueError as exc:
        raise LLMBadResponse(f"글자 영역 응답을 해석하지 못했습니다: {exc}") from exc
    return _region(answer), completion


def _region(answer: Any) -> Region | None:
    if not isinstance(answer, dict) or answer.get("found") is not True:
        return None
    given = [answer.get(name) for name in ("top", "bottom", "left", "right")]
    edges = [float(v) for v in given if isinstance(v, int | float) and not isinstance(v, bool)]
    if len(edges) != len(given):
        return None  # 가장자리가 빠졌거나 수가 아니다
    if not all(math.isfinite(v) and -_SLACK <= v <= 1.0 + _SLACK for v in edges):
        return None
    top, bottom = sorted(min(1.0, max(0.0, v)) for v in edges[:2])
    left, right = sorted(min(1.0, max(0.0, v)) for v in edges[2:])
    if bottom - top < MIN_SIDE or right - left < MIN_SIDE:
        return None
    return Region(left, top, right - left, bottom - top)
