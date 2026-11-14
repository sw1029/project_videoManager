"""VLM 으로 객체 위치를 찾아 잘라 낸 이미지를 정체성 기준으로 쓰는 추출기 (FRAME_IDENTITY=vlm_crop, ADR-0015).

흐름: 클립의 프레임 한 장(ffmpeg) -> VLM 에게 객체별 '보이는지·박스·외형 설명' 을 묻는다 -> 박스를 조금 넓혀 자른다.
- 화면 전체가 아니라 객체만 넘긴다. 화면 전체를 기억으로 쓰면 그 장면의 구도·배경까지 다음 장면에 새어 든다.
- 객체가 보이지 않거나 박스가 너무 작으면 그 객체는 건너뛴다. 다른 시각의 프레임을 한 번 더 보고,
  그래도 없으면 돌려주지 않는다 (나중 장면의 클립에서 다시 뽑는다). 확인된 모습만 기준으로 삼는다.
- 외형 설명(머리·옷·색·형태)은 기준 이미지와 함께 저장돼 다음 장면 프롬프트에 글로도 들어간다.
- 박스는 추적 결과가 아니라 한 프레임에서 VLM 이 짚은 대략의 위치다 (US-03 추적 어댑터가 생기면 그 박스로 바꿀 수 있다).
- 인물은 머리·어깨까지만 자른다 (손에 든 물건·배경이 기준 이미지에 딸려 가지 않게). 참조 이미지 한 장에는
  인물이 한 명만 있어야 한다는 모델 제약(Wan)과도 맞는다. VLM 에게 그렇게 묻되, 박스가 상반신까지 내려와도
  위쪽만 쓰도록 세로를 가로의 person_max_aspect 배로 제한한다. 2026-11-12 실호출에서 컵을 든 상반신이 통째로
  기준이 되자, 컵이 없어야 할 다음 장면에도 컵을 든 모습이 나왔다 (참조 내용 누출). 1.25 배로 줄이자 드는 자세는
  사라졌지만 기준 이미지 아래쪽에 남은 컵 테두리 때문에 카운터 위에 컵이 나와, 1.1 배로 더 줄였다.
- 한 번 뽑은 기준은 클립·객체별 색인(references/identity/<클립>/<객체>.json)에 적어 두고, 같은 클립에서 같은 객체를
  다시 요청받으면 VLM 을 부르지 않고 그 이미지를 돌려준다. VLM 이 짚는 박스는 호출마다 조금씩 달라서, 다시 뽑으면
  기준 이미지(=생성 캐시·원장 키)가 달라져 이미 만든 장면을 또 생성하게 된다.
  색인에는 자르는 설정(여백·인물 세로 비율·크기 상하한)도 적는다. 설정을 바꾸면 예전 기준은 쓰지 않고 다시 뽑는다.
- 잘라 낸 이미지가 작으면 짧은 변을 min_output_side 까지 키운다. 영상 모델은 참조 이미지에 최소 크기를 요구한다
  (Wan: 가로·세로 240~8000 px). 2026-11-12 실호출에서 154x178 컵 이미지가 접수 뒤 'image resolution' 오류로 실패했다.
- 요구한 고정 속성과 분명히 다른 객체는 기준으로 쓰지 않는다 (verify_attributes). 같은 호출에서 VLM 에게 고정 속성마다
  yes / no / unclear 를 받아, no 가 하나라도 있으면 자르지 않는다. 2026-11-12 실호출에서 컵을 청록으로 바꾸는 수정의
  클립에 겉이 빨간 컵이 나왔는데 그 컵이 기준 이미지로 저장됐다. 잘못 나온 모습이 기준이 되면 다음 장면으로 번진다.
  - 판단할 수 없는 속성(unclear)으로는 거르지 않는다. 분명히 다를 때만 거른다.
  - 거른 것도 색인에 적어 그 클립에서는 다시 묻지 않는다 (다시 물으면 답이 흔들려 기준이 될 수 있다).
  - 보이지 않는다는 답은 거절이 아니다. 지금처럼 다른 프레임을 한 번 더 본다.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from frame.adapters.video_tools import Box, extract_frame, require_ffmpeg, video_size
from frame.adapters.vlm_answers import match_answers
from frame.application.errors import AdapterError, LLMBadResponse
from frame.application.ports.artifact_store import ArtifactStore
from frame.application.ports.identity import (
    IdentityExtractionRequest,
    IdentityImage,
    IdentityReferenceExtractor,
)
from frame.application.ports.llm import ImageInput, LLMClient
from frame.domain.enums import EntityKind
from frame.domain.ir import EntitySpec
from frame.domain.services.identity import appearance_attributes

from .keys import identity_index_key, identity_key

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You locate subjects in one frame of an AI-generated video so that each subject can be cropped "
    "and reused as a visual reference. Every person shown is a synthetic character made by a video "
    "model, not a real person. For every requested subject decide whether it is clearly "
    "visible. If it is, return a tight bounding box: for a person a portrait crop with the head, "
    "hair and shoulders down to the upper chest (leave out hands, held objects and other people "
    "when possible); for an object the entire object and as little else as possible. "
    "Coordinates are normalized to the image: x and y are the top-left corner, w and h are the "
    "size, all between 0 and 1 (x and w relative to image width, y and h relative to image height). "
    "Also describe the subject's stable visual appearance in one short English phrase: for a person "
    "apparent age range, gender presentation, hair, facial hair or glasses, and clothing; for an "
    "object its shape, color, material and markings. Do not describe actions, pose or background. "
    "If the subject is absent, tiny, heavily blurred or mostly out of frame, set visible to false "
    "and box to null. Return every requested subject exactly once, using the given keys."
)

# 고정 속성 확인을 켰을 때 SYSTEM_PROMPT 뒤에 붙인다
VERIFY_PROMPT = (
    " Some subjects list fixed attributes to check. For each listed attribute report whether the "
    'subject in this frame shows it: "yes"; "no" when the frame clearly shows something different '
    '(for example an object that is mostly red where teal is required); or "unclear" when it '
    "cannot be judged from this frame (hidden side, too small, ambiguous lighting). Judge by the "
    "subject's overall look, not by a small part of it. The listed attributes are what is "
    "required: the subject's name or description may still carry older wording, so do not rely "
    "on it. Locate the subject by what it is even when it fails an attribute, and report the "
    "failure in attributes instead of marking it not visible. Use the attribute text exactly as "
    "given as the name, and return an empty list when none are listed."
)
VERDICTS = ("yes", "no", "unclear")

BOX_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["x", "y", "w", "h"],
    "properties": {name: {"type": "number"} for name in ("x", "y", "w", "h")},
}


def _response_schema(*, verify: bool) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "entity_key": {"type": "string"},
        "visible": {"type": "boolean"},
        "box": {"anyOf": [BOX_SCHEMA, {"type": "null"}]},
        "appearance": {"type": "string"},
    }
    if verify:
        properties["attributes"] = {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "verdict"],
                "properties": {
                    "name": {"type": "string"},
                    "verdict": {"type": "string", "enum": list(VERDICTS)},
                },
            },
        }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["entities"],
        "properties": {
            "entities": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": list(properties),
                    "properties": properties,
                },
            }
        },
    }


RESPONSE_SCHEMA = _response_schema(verify=False)
VERIFY_RESPONSE_SCHEMA = _response_schema(verify=True)


@dataclass(frozen=True)
class _Rejected:
    """색인에 남긴 거절 기록: 이 클립의 객체는 요구한 고정 속성과 달라 기준으로 쓰지 않는다."""

    attributes: tuple[str, ...]


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
        min_side: int = 96,
        max_side: int = 1024,
        min_output_side: int = 480,
        person_max_aspect: float = 1.1,
        timeout_s: float | None = 90.0,
        reuse_existing: bool = True,
        verify_attributes: bool = True,
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
        self.min_side = (
            min_side  # 잘라 낸 원본의 짧은 변 하한(픽셀). 이보다 작으면 기준으로 쓰지 않는다
        )
        self.max_side = max_side
        self.min_output_side = min_output_side  # 저장할 이미지의 짧은 변 하한. 작으면 키운다
        # 인물 기준 이미지의 세로 상한 (가로의 배수). 박스의 위쪽부터 이만큼만 쓴다: 머리·어깨
        self.person_max_aspect = person_max_aspect
        self.reuse_existing = reuse_existing  # 같은 클립·객체의 기준이 이미 있으면 다시 뽑지 않는다
        # 요구한 고정 속성과 분명히 다른 객체는 기준으로 쓰지 않는다. 끄면 위치와 외형만 묻는다 (예전 질의)
        self.verify_attributes = verify_attributes
        self.timeout_s = timeout_s

    def extract(self, request: IdentityExtractionRequest) -> tuple[IdentityImage, ...]:
        if not request.entities:
            return ()
        found: dict[str, IdentityImage] = {}
        rejected: dict[str, tuple[str, ...]] = {}  # 고정 속성과 달라 거른 객체 -> 어긋난 속성
        if self.reuse_existing:
            for entity in request.entities:
                existing = self._existing(request.clip.uri, entity.entity_key)
                if isinstance(existing, _Rejected):
                    rejected[entity.entity_key] = existing.attributes
                    log.info(
                        "정체성 기준: 클립 %s 의 %s 은(는) 앞서 고정 속성과 달라 걸렀습니다 "
                        "(다시 묻지 않음): %s",
                        request.clip.uri,
                        entity.entity_key,
                        ", ".join(existing.attributes),
                    )
                elif existing is not None:
                    found[entity.entity_key] = existing

        def pending() -> list[EntitySpec]:
            return [
                e
                for e in request.entities
                if e.entity_key not in found and e.entity_key not in rejected
            ]

        if not pending():
            return tuple(found[e.entity_key] for e in request.entities if e.entity_key in found)
        video = self._store.get(request.clip.uri)
        for position in self.positions:
            remaining = pending()
            if not remaining:
                break
            at_ms = int(request.clip.duration_ms * min(1.0, max(0.0, position)))
            try:
                frame = extract_frame(self._ffmpeg, video, at_ms, max_side=self.max_side)
                frame_size = video_size(self._ffmpeg, frame.data)
                answers = self._locate(request, remaining, ImageInput(frame.data, frame.media_type))
            except AdapterError as exc:
                if not found:
                    raise
                # 이미 찾은 기준은 살린다 (다시 보기는 보조 시도일 뿐이다)
                log.warning("정체성 기준: 다른 프레임을 다시 보지 못했습니다 (%s)", exc)
                break
            for entity in remaining:
                answer = answers.get(entity.entity_key)
                if answer is None:
                    continue
                verdicts = (
                    _verdicts(answer, _fixed_attributes(request, entity))
                    if self.verify_attributes
                    else None
                )
                if verdicts and answer.get("visible") is True:
                    wrong = tuple(name for name, verdict in verdicts.items() if verdict == "no")
                    if wrong:
                        # 이 클립은 요구를 반영하지 못했다. 그 모습을 기준으로 삼으면 다음 장면으로 번진다
                        rejected[entity.entity_key] = wrong
                        self._remember_rejection(request.clip.uri, entity.entity_key, wrong, at_ms)
                        log.warning(
                            "정체성 기준: 클립 %s 의 %s 은(는) 요구한 고정 속성과 달라 "
                            "기준으로 쓰지 않습니다: %s",
                            request.clip.uri,
                            entity.entity_key,
                            ", ".join(wrong),
                        )
                        continue
                box = _valid_box(answer, self.min_area)
                if box is None:
                    continue
                image = self._crop(request, entity, video, at_ms, box, answer, frame_size, verdicts)
                if image is not None:
                    found[entity.entity_key] = image
        missing = [e.entity_key for e in pending()]
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
        verify = self.verify_attributes
        completion = self._llm.complete_json(
            SYSTEM_PROMPT + VERIFY_PROMPT if verify else SYSTEM_PROMPT,
            _user_prompt(request, entities, verify=verify),
            VERIFY_RESPONSE_SCHEMA if verify else RESPONSE_SCHEMA,
            schema_name="entity_boxes",
            timeout_s=self.timeout_s,
            images=(frame,),
        )
        log.info(
            "정체성 기준 VLM 호출: model=%s 입력 %s 토큰, 출력 %s 토큰 (장면 %s, 객체 %s)",
            completion.model,
            completion.input_tokens,
            completion.output_tokens,
            request.scene.scene_key,
            ", ".join(e.entity_key for e in entities),
        )
        try:
            data = json.loads(completion.text)
            items = list(data["entities"])
        except (ValueError, KeyError, TypeError) as exc:
            raise LLMBadResponse(f"객체 위치 응답을 해석하지 못했습니다: {exc}") from exc
        return match_answers(items, [e.entity_key for e in entities])

    def _crop(
        self,
        request: IdentityExtractionRequest,
        entity: EntitySpec,
        video: bytes,
        at_ms: int,
        box: Box,
        answer: Mapping[str, Any],
        frame_size: tuple[int, int],
        verdicts: Mapping[str, str] | None = None,
    ) -> IdentityImage | None:
        region = box.padded(self.padding)
        if entity.kind is EntityKind.PERSON:
            region = _portrait(region, frame_size, self.person_max_aspect)
        native = extract_frame(self._ffmpeg, video, at_ms, crop=region)
        native_size = video_size(self._ffmpeg, native.data)
        if min(native_size) < self.min_side:
            return None  # 너무 작게 잡힌 객체는 기준이 되기 어렵다
        crop = extract_frame(
            self._ffmpeg,
            video,
            at_ms,
            crop=region,
            max_side=self.max_side,
            min_side=self.min_output_side,
        )
        width, height = video_size(self._ffmpeg, crop.data)
        uri = self._store.put(
            identity_key(request.clip.uri, entity.entity_key, "jpg", crop.data),
            crop.data,
            crop.media_type,
        )
        detail: dict[str, Any] = {
            "extractor": "vlm_crop",
            "frame_ms": at_ms,
            "box": box.as_dict(),
            "region": region.as_dict(),
            "size": [width, height],
            "native_size": list(native_size),
        }
        if verdicts is not None:
            detail["attributes"] = dict(verdicts)  # 고정 속성마다 VLM 이 내린 판정 (근거로 남긴다)
        image = IdentityImage(
            entity_key=entity.entity_key,
            image_uri=uri,
            description=" ".join(str(answer.get("appearance") or "").split())[:240],
            detail=detail,
        )
        self._remember(request.clip.uri, image)
        return image

    # ---- 클립·객체별 색인 ----
    def _existing(self, clip_uri: str, entity_key: str) -> IdentityImage | _Rejected | None:
        """이 클립에서 이 객체를 이미 다뤘으면 그 결과: 뽑아 둔 기준(이미지가 남아 있을 때만) 또는 거절 기록."""
        index_uri = self._store.uri_for(identity_index_key(clip_uri, entity_key))
        try:
            if index_uri is None or not self._store.exists(index_uri):
                return None
            data = json.loads(self._store.get(index_uri).decode("utf-8"))
            if data.get("entity_key") != entity_key or data.get("params") != self._params():
                return None  # 다른 객체의 항목이거나 다른 설정으로 만든 결과다
            rejected = data.get("rejected")
            if isinstance(rejected, list) and rejected:
                return _Rejected(tuple(str(name) for name in rejected))
            image_uri = str(data["image_uri"])
            if not self._store.exists(image_uri):
                return None
            return IdentityImage(
                entity_key=entity_key,
                image_uri=image_uri,
                description=str(data.get("description") or ""),
                detail={**dict(data.get("detail") or {}), "reused": True},
            )
        except (AdapterError, OSError, ValueError, KeyError, TypeError) as exc:
            log.warning("정체성 기준 색인을 읽지 못해 다시 뽑습니다 (%s): %s", entity_key, exc)
            return None

    def _params(self) -> dict[str, float | bool]:
        """기준을 어떻게 뽑았는지 정하는 설정 (색인 항목이 지금 설정으로 만든 것인지 가리는 데 쓴다)."""
        params: dict[str, float | bool] = {
            "padding": self.padding,
            "person_max_aspect": self.person_max_aspect,
            "min_output_side": self.min_output_side,
            "max_side": self.max_side,
        }
        if self.verify_attributes:
            # 확인 없이 뽑아 둔 기준(이 값이 없는 항목)은 확인을 켠 뒤에는 다시 쓰지 않는다
            params["verify_attributes"] = True
        return params

    def _remember(self, clip_uri: str, image: IdentityImage) -> None:
        self._write_index(
            clip_uri,
            image.entity_key,
            {
                "image_uri": image.image_uri,
                "description": image.description,
                "detail": dict(image.detail),
            },
        )

    def _remember_rejection(
        self, clip_uri: str, entity_key: str, attributes: Sequence[str], at_ms: int
    ) -> None:
        self._write_index(
            clip_uri,
            entity_key,
            {
                "rejected": list(attributes),
                "detail": {"extractor": "vlm_crop", "frame_ms": at_ms},
            },
        )

    def _write_index(self, clip_uri: str, entity_key: str, fields: Mapping[str, Any]) -> None:
        payload = {"schema": 1, "entity_key": entity_key, "params": self._params(), **fields}
        try:
            self._store.put(
                identity_index_key(clip_uri, entity_key),
                json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"),
                "application/json",
            )
        except Exception as exc:  # noqa: BLE001 - 색인을 못 써도 이번 결과는 그대로 쓴다
            log.warning("정체성 기준 색인을 쓰지 못했습니다 (%s): %s", entity_key, exc)


def _portrait(region: Box, frame_size: tuple[int, int], max_aspect: float) -> Box:
    """세로가 가로의 max_aspect 배를 넘으면 위쪽부터 그만큼만 남긴다 (픽셀 기준)."""
    frame_w, frame_h = frame_size
    width_px, height_px = region.w * frame_w, region.h * frame_h
    if max_aspect <= 0 or height_px <= width_px * max_aspect:
        return region
    return Box(region.x, region.y, region.w, width_px * max_aspect / frame_h)


def _user_prompt(
    request: IdentityExtractionRequest, entities: Sequence[EntitySpec], *, verify: bool = False
) -> str:
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
        fixed = _fixed_attributes(request, entity) if verify else []
        if fixed:
            lines.append("  fixed attributes to check: " + "; ".join(fixed))
    if scene.description.strip():
        lines.append(f"Scene the frame comes from: {scene.description.strip()}")
    return "\n".join(lines)


def _fixed_attributes(request: IdentityExtractionRequest, entity: EntitySpec) -> list[str]:
    """이 장면에서 객체가 지켜야 하는 고정 속성 ('술어: 값'). 관계와 행동·자세 같은 일시적 술어는 뺀다."""
    return [
        f"{predicate.replace('_', ' ')}: {value}"
        for predicate, value in appearance_attributes(request.scene, entity.entity_key).items()
    ]


def _canonical(name: str) -> str:
    """속성 이름을 견줄 때 쓰는 꼴: 대소문자·공백·구두점 차이를 없앤다."""
    return re.sub(r"[\W_]+", "", name.lower())


def _verdicts(answer: Mapping[str, Any], expected: Sequence[str]) -> dict[str, str]:
    """물어본 고정 속성마다 VLM 이 내린 판정 (이름 -> yes | no | unclear).

    물어보지 않은 이름과 형식이 어긋난 항목은 버린다. 모델이 행동 같은 일시적 조건에 no 를 붙여도
    기준을 버리지 않게 한다. 같은 속성에 판정이 여럿이면 no 가 이긴다.
    """
    items = answer.get("attributes")
    if not isinstance(items, list):
        return {}
    names = {_canonical(name): name for name in expected}
    verdicts: dict[str, str] = {}
    for item in items:
        if not isinstance(item, Mapping):
            continue
        name = names.get(_canonical(str(item.get("name") or "")))
        verdict = str(item.get("verdict") or "").strip().lower()
        if name is not None and verdict in VERDICTS and verdicts.get(name) != "no":
            verdicts[name] = verdict
    return verdicts


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
