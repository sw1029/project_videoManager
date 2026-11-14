"""locate_text: 프레임 한 장에서 글자의 영역을 묻는다 (재생 응답, 과금 없음)."""

from __future__ import annotations

import json

import pytest

from frame.adapters.llm import ReplayLLMClient
from frame.adapters.text_locator import LOCATE_SCHEMA, LOCATE_SCHEMA_NAME, locate_text
from frame.application.errors import LLMBadResponse
from frame.application.ports.llm import ImageInput

FRAME = ImageInput(b"png-bytes", "image/png", "frame")


def _locate(answer, target='caption "오늘의 메뉴"'):
    llm = ReplayLLMClient([answer if isinstance(answer, str) else json.dumps(answer)])
    region, completion = locate_text(llm, FRAME, target, timeout_s=30.0)
    return region, completion, llm.calls[0]


def test_box_is_returned_as_a_region_inside_the_frame():
    region, completion, call = _locate(
        {"found": True, "top": 0.07, "bottom": 0.15, "left": 0.1, "right": 0.9}
    )

    assert (region.x, region.y) == (0.1, 0.07)
    assert round(region.w, 3) == 0.8 and round(region.h, 3) == 0.08
    assert completion.text and call["schema_name"] == LOCATE_SCHEMA_NAME
    assert (
        call["schema"] == LOCATE_SCHEMA and call["images"] == (FRAME,) and call["timeout_s"] == 30.0
    )
    assert 'caption "오늘의 메뉴"' in call["user"]
    # 한 번에 문구 하나, 프레임 한 장: 여러 개를 한꺼번에 물으면 상자가 어긋났다
    assert "one frame" in call["system"] and "fractions of the frame" in call["system"]


def test_box_slightly_outside_the_frame_is_clipped_and_swapped_edges_are_put_right():
    region, _, _ = _locate(
        {"found": True, "top": 0.95, "bottom": 0.9, "left": -0.02, "right": 1.04}
    )

    assert (region.x, round(region.y, 3)) == (0.0, 0.9)
    assert region.w == 1.0 and round(region.h, 3) == 0.05


@pytest.mark.parametrize(
    "answer",
    [
        {"found": False, "top": 0, "bottom": 0, "left": 0, "right": 0},
        {"found": True, "top": 0.5, "bottom": 0.5, "left": 0.1, "right": 0.9},  # 높이 없음
        {"found": True, "top": 0.2, "bottom": 0.3, "left": 0.4, "right": 0.402},  # 너무 좁다
        {"found": True, "top": "?", "bottom": 0.3, "left": 0.4, "right": 0.6},
    ],
)
def test_no_region_when_not_found_or_the_box_makes_no_sense(answer):
    region, _, _ = _locate(answer)

    assert region is None


def test_answer_that_is_not_json_is_a_bad_response():
    with pytest.raises(LLMBadResponse):
        _locate("not json")
