"""시작 위치 찾기: 추적기가 따라갈 객체를 프레임에서 찾고, 짚은 박스가 그 객체인지 확인한다 (OpenCV 추적기가 쓴다).

EntityLocator 는 추적기 안의 의존성이다 (포트가 아니다). 실제 구현은 VLM(VlmEntityLocator)이고, 테스트는 정해진
박스를 돌려주는 구현을 넣는다. 묻는 글과 응답 형식은 _locate_question.py 에 있다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from frame.adapters.shared.media.frames import Box
from frame.adapters.shared.vlm.answers import match_answers
from frame.adapters.shared.vlm.ask import ask_json
from frame.adapters.shared.vlm.boxes import valid_box
from frame.application.ports.llm import ImageInput, LLMClient
from frame.domain.ir import EntitySpec, SceneSpec

from ._locate_question import (
    LOCATE_SCHEMA,
    LOCATE_SYSTEM_PROMPT,
    VERDICTS,
    VERIFY_SCHEMA,
    VERIFY_SYSTEM_PROMPT,
    locate_prompt,
    verify_prompt,
)

# 화면의 0.1% 보다 작은 박스는 추적기를 시작하기에 너무 작다 (몇 픽셀짜리 박스는 곧 놓친다)
MIN_BOX_AREA = 0.001
LOCATE_TIMEOUT_S = 90.0  # 이미지 한 장을 읽는 VLM 호출 하나의 시간 제한


@dataclass(frozen=True)
class Located:
    boxes: Mapping[str, Box]  # 찾은 객체 -> 박스 (못 찾은 객체는 없다)
    model: str | None = None
    usage: Mapping[str, int | None] = field(default_factory=dict)
    # 보이지 않는다고 분명히 답한 객체 (visible=false). 답이 없거나 박스가 너무 작은 것과 구분한다:
    # 추적기는 이 객체를 따라가던 중이면 그 구간을 끝낸다 (화면을 떠난 객체에 박스가 남지 않게)
    absent: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Verified:
    verdicts: Mapping[str, str]  # 객체 -> yes | no | unclear (답이 없으면 빠진다)
    seen: Mapping[str, str] = field(default_factory=dict)  # 객체 -> 잘라 낸 이미지에 실제로 보인 것
    model: str | None = None
    usage: Mapping[str, int | None] = field(default_factory=dict)


class EntityLocator(ABC):
    @abstractmethod
    def locate(
        self, frame: ImageInput, entities: Sequence[EntitySpec], scene: SceneSpec
    ) -> Located: ...

    @abstractmethod
    def verify(
        self, crops: Sequence[tuple[EntitySpec, ImageInput]], scene: SceneSpec
    ) -> Verified: ...


class VlmEntityLocator(EntityLocator):
    """VLM 에게 묻는다. 모델·키·주소는 FRAME_VLM_* (조립이 만든 LLMClient). 호출 실패는 LLMError 로 올라간다."""

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    def locate(
        self, frame: ImageInput, entities: Sequence[EntitySpec], scene: SceneSpec
    ) -> Located:
        wanted = [e.entity_key for e in entities]
        answer = ask_json(
            self._llm,
            LOCATE_SYSTEM_PROMPT,
            locate_prompt(entities, scene),
            LOCATE_SCHEMA,
            schema_name="track_locate",
            timeout_s=LOCATE_TIMEOUT_S,
            images=[frame],
            read=_items("entities"),
            what="추적 시작 위치",
        )
        replies = match_answers(answer.value, wanted)
        boxes = {
            key: box
            for key in wanted
            if (box := valid_box(replies.get(key) or {}, MIN_BOX_AREA)) is not None
        }
        absent = frozenset(key for key, reply in replies.items() if reply.get("visible") is False)
        return Located(boxes, answer.model, answer.usage, absent)

    def verify(self, crops: Sequence[tuple[EntitySpec, ImageInput]], scene: SceneSpec) -> Verified:
        if not crops:
            return Verified({})
        ids = [f"C{i}" for i in range(1, len(crops) + 1)]
        images = [
            ImageInput(image.data, image.media_type, label=crop_id)
            for crop_id, (_, image) in zip(ids, crops, strict=True)
        ]
        answer = ask_json(
            self._llm,
            VERIFY_SYSTEM_PROMPT,
            verify_prompt([(crop_id, e) for crop_id, (e, _) in zip(ids, crops, strict=True)]),
            VERIFY_SCHEMA,
            schema_name="track_verify",
            timeout_s=LOCATE_TIMEOUT_S,
            images=images,
            read=_items("crops"),
            what="추적 시작 박스 확인",
        )
        replies = match_answers(answer.value, ids, field="crop_id")
        verdicts: dict[str, str] = {}
        seen: dict[str, str] = {}
        for crop_id, (entity, _) in zip(ids, crops, strict=True):
            reply = replies.get(crop_id) or {}
            verdict = str(reply.get("verdict") or "").strip().lower()
            if verdict in VERDICTS:
                verdicts[entity.entity_key] = verdict
                seen[entity.entity_key] = " ".join(str(reply.get("seen") or "").split())[:120]
        return Verified(verdicts, seen, answer.model, answer.usage)


def _items(name: str) -> Callable[[Any], list[Any]]:
    """응답 본문의 목록 하나를 꺼내는 읽기 함수 (목록이 아니면 ValueError -> LLMBadResponse)."""

    def read(body: Any) -> list[Any]:
        items = body[name]
        if not isinstance(items, list):
            raise ValueError(f"{name} 이(가) 목록이 아닙니다")
        return items

    return read
