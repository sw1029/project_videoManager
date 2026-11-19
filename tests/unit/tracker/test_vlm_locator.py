"""VLM 위치 찾기·박스 확인 (추적기의 시작 위치, ADR-0036): 묻는 글과 답 읽기."""

from __future__ import annotations

import json

import pytest

from frame.adapters.llm import ReplayLLMClient
from frame.adapters.shared.media.frames import Box
from frame.adapters.tracker import VlmEntityLocator
from frame.application.errors import LLMBadResponse
from frame.application.ports.llm import ImageInput
from frame.domain.enums import EntityKind
from frame.domain.ir import EntitySpec, RequirementSpec, SceneSpec

CUP = EntitySpec("cup", EntityKind.PROP, "red ceramic mug")
BARISTA = EntitySpec("barista", EntityKind.PERSON, "barista in a black apron")
HAT = EntitySpec("hat", EntityKind.PROP, "silver cowboy hat")
SCENE = SceneSpec(
    "intro",
    0,
    4000,
    "A barista shows a red mug",
    ("barista", "cup"),
    (RequirementSpec("intro", "color", "red", "cup"),),
)
FRAME = ImageInput(b"\xff\xd8frame", "image/jpeg")


def _entities(*items):
    return json.dumps({"entities": list(items)})


def test_locate_reads_boxes_of_visible_subjects_only():
    llm = ReplayLLMClient(
        [
            _entities(
                # 실제 모델은 키 대신 질의 한 줄을 돌려줄 때가 있다
                {
                    "entity_key": "cup (prop): red ceramic mug",
                    "visible": True,
                    "box": {"x": 0.4, "y": 0.5, "w": 0.2, "h": 0.25},
                },
                {"entity_key": "barista", "visible": False, "box": None},
            )
        ]
    )

    located = VlmEntityLocator(llm).locate(FRAME, [CUP, BARISTA], SCENE)

    assert located.boxes == {"cup": Box(0.4, 0.5, 0.2, 0.25)}
    assert located.absent == frozenset(
        {"barista"}
    )  # 보이지 않는다고 답한 객체 (답이 없는 것과 다르다)
    assert located.model == "replay"
    (call,) = llm.calls
    assert "cup (prop): red ceramic mug; color: red" in call["user"]
    # 인물도 몸 전체를 감싼다 (정체성 기준의 머리·어깨와 다르다)
    assert "whole visible body" in call["system"]
    assert len(call["images"]) == 1


def test_locate_drops_boxes_too_small_to_follow():
    llm = ReplayLLMClient(
        [
            _entities(
                {
                    "entity_key": "cup",
                    "visible": True,
                    "box": {"x": 0.5, "y": 0.5, "w": 0.01, "h": 0.01},
                }
            )
        ]
    )

    assert VlmEntityLocator(llm).locate(FRAME, [CUP], SCENE).boxes == {}


def test_verify_reads_a_verdict_per_crop_in_order():
    llm = ReplayLLMClient(
        [
            json.dumps(
                {
                    "crops": [
                        {"crop_id": "C1", "verdict": "no", "seen": "blue sky above a hat"},
                        {"crop_id": "C2", "verdict": "YES", "seen": "red mug"},
                    ]
                }
            )
        ]
    )
    crops = [
        (HAT, ImageInput(b"\xff\xd81", "image/jpeg")),
        (CUP, ImageInput(b"\xff\xd82", "image/jpeg")),
    ]

    verified = VlmEntityLocator(llm).verify(crops, SCENE)

    assert verified.verdicts == {"hat": "no", "cup": "yes"}
    assert verified.seen["hat"] == "blue sky above a hat"
    (call,) = llm.calls
    assert [image.label for image in call["images"]] == ["C1", "C2"]
    assert "- C1: hat (prop): silver cowboy hat" in call["user"]
    # 확인은 '그 객체인가' 만 묻는다. 장면 요구조건(색·동작)을 실으면 맞는 객체도 거절한다 (실측: 컵을 아직 안 든
    # 첫 프레임의 바리스타가 'holds: cup' 때문에 no 를 받았다)
    assert "color: red" not in call["user"]
    # 설명도 어느 객체인지만 알린다 (실측: 갈색 반점이 있는 개가 'white dog' 와 다르다고 no 를 받았다)
    assert "not how faithfully it was drawn" in call["system"]


def test_verify_without_crops_does_not_call_the_model():
    llm = ReplayLLMClient([])

    assert VlmEntityLocator(llm).verify([], SCENE).verdicts == {}
    assert llm.calls == []


def test_an_unreadable_answer_is_a_bad_response():
    llm = ReplayLLMClient([json.dumps({"entities": "not a list"})])

    with pytest.raises(LLMBadResponse, match="추적 시작 위치"):
        VlmEntityLocator(llm).locate(FRAME, [CUP], SCENE)
