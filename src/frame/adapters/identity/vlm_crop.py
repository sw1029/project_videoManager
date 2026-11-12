"""VLM 으로 객체 위치를 찾아 잘라 낸 이미지를 정체성 기준으로 쓰는 추출기 (FRAME_IDENTITY=vlm_crop, ADR-0015).

흐름: 클립의 프레임 한 장(ffmpeg) -> VLM 에게 객체별 '보이는지·박스·외형 설명' 을 묻는다 -> 박스를 조금 넓혀 자른다.
- 화면 전체가 아니라 객체만 넘긴다. 화면 전체를 기억으로 쓰면 그 장면의 구도·배경까지 다음 장면에 새어 든다.
- 객체가 보이지 않거나 박스가 너무 작으면 그 객체는 건너뛴다. 다른 시각의 프레임을 한 번 더 보고,
  그래도 없으면 돌려주지 않는다 (나중 장면의 클립에서 다시 뽑는다). 확인된 모습만 기준으로 삼는다.
- 외형 설명(머리·옷·색·형태)은 기준 이미지와 함께 저장돼 다음 장면 프롬프트에 글로도 들어간다.
- 박스는 추적 결과가 아니라 한 프레임에서 VLM 이 짚은 대략의 위치다 (US-03 추적 어댑터가 생기면 그 박스로 바꿀 수 있다).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any

from frame.adapters.video_tools import Box, extract_frame, require_ffmpeg, video_size
from frame.application.errors import AdapterError, LLMBadResponse
from frame.application.ports.artifact_store import ArtifactStore
from frame.application.ports.identity import (
    IdentityExtractionRequest,
    IdentityImage,
    IdentityReferenceExtractor,
)
from frame.application.ports.llm import ImageInput, LLMClient
from frame.domain.ir import EntitySpec

from .keys import identity_key

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You locate subjects in one frame of an AI-generated video so that each subject can be cropped "
    "and reused as a visual reference. For every requested subject decide whether it is clearly "
    "visible. If it is, return a bounding box that contains the whole visible subject: for a person "
    "include the head, hair and upper body with clothing; for an object include the entire object. "
    "Coordinates are normalized to the image: x and y are the top-left corner, w and h are the "
    "size, all between 0 and 1 (x and w relative to image width, y and h relative to image height). "
    "Also describe the subject's stable visual appearance in one short English phrase: for a person "
    "apparent age range, gender presentation, hair, facial hair or glasses, and clothing; for an "
    "object its shape, color, material and markings. Do not describe actions, pose or background. "
    "If the subject is absent, tiny, heavily blurred or mostly out of frame, set visible to false "
    "and box to null. Return every requested subject exactly once, using the given keys."
)

BOX_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["x", "y", "w", "h"],
    "properties": {name: {"type": "number"} for name in ("x", "y", "w", "h")},
}
RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["entities"],
    "properties": {
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["entity_key", "visible", "box", "appearance"],
                "properties": {
                    "entity_key": {"type": "string"},
                    "visible": {"type": "boolean"},
                    "box": {"anyOf": [BOX_SCHEMA, {"type": "null"}]},
                    "appearance": {"type": "string"},
                },
            },
        }
    },
}


class VlmCropIdentityExtractor(IdentityReferenceExtractor):
    def __init__(
        self,
        store: ArtifactStore,
        ffmpeg: str | None,
        llm: LLMClient,
        *,
        positions: Sequence[float] = (0.5, 0.2),
        padding: float = 0.12,
        min_area: float = 0.005,
        min_side: int = 48,
        max_side: int = 768,
        timeout_s: float | None = 90.0,
    ) -> None:
        self._store = store
        self._ffmpeg = require_ffmpeg(ffmpeg)
        self._llm = llm
        self.positions = tuple(
            positions
        )  # 클립 길이 중 볼 위치 (0..1). 앞에서 못 찾은 객체만 다음 위치에서 본다
        self.padding = (
            padding  # 박스를 가로·세로 이 비율만큼 넓혀 자른다 (VLM 박스가 조금 어긋나도 담기게)
        )
        self.min_area = min_area  # 화면 넓이 대비 이보다 작은 박스는 기준으로 쓰지 않는다
        self.min_side = min_side  # 잘라 낸 이미지의 짧은 변 하한(픽셀)
        self.max_side = max_side
        self.timeout_s = timeout_s

    def extract(self, request: IdentityExtractionRequest) -> tuple[IdentityImage, ...]:
        if not request.entities:
            return ()
        video = self._store.get(request.clip.uri)
        found: dict[str, IdentityImage] = {}
        for position in self.positions:
            remaining = [e for e in request.entities if e.entity_key not in found]
            if not remaining:
                break
            at_ms = int(request.clip.duration_ms * min(1.0, max(0.0, position)))
            try:
                frame = extract_frame(self._ffmpeg, video, at_ms, max_side=self.max_side)
                answers = self._locate(request, remaining, ImageInput(frame.data, frame.media_type))
            except AdapterError as exc:
                if not found:
                    raise
                # 이미 찾은 기준은 살린다 (다시 보기는 보조 시도일 뿐이다)
                log.warning("정체성 기준: 다른 프레임을 다시 보지 못했습니다 (%s)", exc)
                break
            for entity in remaining:
                answer = answers.get(entity.entity_key)
                box = _valid_box(answer, self.min_area) if answer is not None else None
                if answer is None or box is None:
                    continue
                image = self._crop(request, entity, video, at_ms, box, answer)
                if image is not None:
                    found[entity.entity_key] = image
        missing = [e.entity_key for e in request.entities if e.entity_key not in found]
        if missing:
            log.info(
                "정체성 기준: 클립 %s 에서 객체를 찾지 못했습니다: %s",
                request.clip.uri,
                ", ".join(missing),
            )
        return tuple(found[e.entity_key] for e in request.entities if e.entity_key in found)

    def _locate(
        self,
        request: IdentityExtractionRequest,
        entities: Sequence[EntitySpec],
        frame: ImageInput,
    ) -> dict[str, Mapping[str, Any]]:
        completion = self._llm.complete_json(
            SYSTEM_PROMPT,
            _user_prompt(request, entities),
            RESPONSE_SCHEMA,
            schema_name="entity_boxes",
            timeout_s=self.timeout_s,
            images=(frame,),
        )
        try:
            data = json.loads(completion.text)
            items = data["entities"]
            return {str(item["entity_key"]): item for item in items if isinstance(item, dict)}
        except (ValueError, KeyError, TypeError) as exc:
            raise LLMBadResponse(f"객체 위치 응답을 해석하지 못했습니다: {exc}") from exc

    def _crop(
        self,
        request: IdentityExtractionRequest,
        entity: EntitySpec,
        video: bytes,
        at_ms: int,
        box: Box,
        answer: Mapping[str, Any],
    ) -> IdentityImage | None:
        crop = extract_frame(
            self._ffmpeg, video, at_ms, crop=box.padded(self.padding), max_side=self.max_side
        )
        width, height = video_size(self._ffmpeg, crop.data)
        if min(width, height) < self.min_side:
            return None  # 너무 작게 잡힌 객체는 기준이 되기 어렵다
        uri = self._store.put(
            identity_key(request.clip.uri, entity.entity_key, "jpg"), crop.data, crop.media_type
        )
        return IdentityImage(
            entity_key=entity.entity_key,
            image_uri=uri,
            description=" ".join(str(answer.get("appearance") or "").split())[:240],
            detail={
                "extractor": "vlm_crop",
                "frame_ms": at_ms,
                "box": box.as_dict(),
                "size": [width, height],
            },
        )


def _user_prompt(request: IdentityExtractionRequest, entities: Sequence[EntitySpec]) -> str:
    scene = request.scene
    lines = ["Subjects to locate in the image:"]
    for entity in entities:
        details = [entity.description.strip()] if entity.description.strip() else []
        details += [
            f"{r.predicate.replace('_', ' ')}: {r.expected_value}"
            for r in scene.requirements
            if r.entity_key == entity.entity_key and not r.is_relational
        ]
        text = "; ".join(details)
        lines.append(
            f"- {entity.entity_key} ({str(entity.kind).lower()})" + (f": {text}" if text else "")
        )
    if scene.description.strip():
        lines.append(f"Scene the frame comes from: {scene.description.strip()}")
    return "\n".join(lines)


def _valid_box(answer: Mapping[str, Any], min_area: float) -> Box | None:
    """보인다고 답했고 박스가 화면 안의 쓸 만한 크기일 때만 Box. 나머지는 None."""
    if answer.get("visible") is not True or not isinstance(answer.get("box"), Mapping):
        return None
    raw = answer["box"]
    try:
        x, y, w, h = (float(raw[name]) for name in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError):
        return None
    if not (0.0 <= x < 1.0 and 0.0 <= y < 1.0 and w > 0.0 and h > 0.0):
        return None
    w, h = min(w, 1.0 - x), min(h, 1.0 - y)
    return Box(x, y, w, h) if w * h >= min_area else None
