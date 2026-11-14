"""프레임을 읽는 LLM 으로 테마를 뽑는다 (FRAME_THEME_EXTRACTOR=vlm, ADR-0025).

방법
1. 원본의 컷을 다시 만들 수 있는 장면 자리로 묶는다 (slot_spans_from_shots).
2. 자리마다 표본 프레임을 고르게 뽑는다 (ffmpeg). 전체 프레임 수에 상한을 두고 자리마다 나눈다.
3. 모델을 한 번 부른다. 프레임과 함께 겉모습·자리의 시간·받아 적은 말소리를 주고, 객체, 자리마다의 내용,
   스타일, 화면에서 읽은 문구, 문구 규칙, 편마다 바뀌는 변수를 받는다.
4. 응답을 코드로 다듬는다. 모델이 낸 것 가운데 규칙에 맞는 것만 쓰고, 버린 것은 메모로 남긴다.
   - 자리의 키·시간은 코드가 정한다 (모델은 S1, S2 … 로만 가리킨다).
   - 객체 키는 영문 소문자·숫자·밑줄로 맞추고, 키에 든 겉모습 낱말은 걷어낸다 (red_mug -> mug).
     키에 속성이 있으면 그 속성을 고친 뒤 검수가 헷갈린다 (2차 실험).
   - 술어·값은 IR 의 어휘로 정규화한다. 어휘에 없는 술어는 버린다.
   - 관계 술어의 상대가 객체가 아니면(카메라를 본다) 행동 조건으로 옮긴다.
   - 관계·일시적 술어는 고정 속성이 될 수 없다. 배경·문구 객체는 고정 출연이 되지 않는다.
   - 카메라 조건은 스타일에 두지 않는다 (자리마다 다르다. 스타일에 두면 모든 장면의 조건이 된다).
   - 말 빠르기·자리별 말의 예는 모델이 아니라 받아 적은 말소리에서 계산한다.
   - 기준 이미지는 붙이지 않는다 (외부 영상의 화면을 잘라 두지 않는다. 필요하면 AdoptAnchors 로 붙인다).
5. 다듬은 결과가 테마·IR 의 규칙을 통과하지 못하면 오류를 되돌려 보내 다시 묻는다 (max_attempts).
6. 얹은 문구의 영역을 다시 묻는다 (refine_text_regions). 3번의 호출이 준 상자는 어긋난다: 프레임 여러 장에
   문구 여러 개를 한 번에 물은 것이라 위·아래 구분만 맞는 정도다. 문구마다 그 자리의 가운데 프레임 한 장을
   보여 주고 그 문구의 영역만 묻는다 (adapters/text_locator.py). 다시 물은 영역은 화면 문구와, 그 문구에서 나온
   문구 규칙에 함께 넣는다. 문구 규칙의 영역은 새 편의 문구가 놓일 자리다. 찾지 못하면 처음 받은 영역을 둔다.

한계
- 프레임만 보므로 움직임·음악은 알 수 없다 (음악은 '알 수 없음' 으로 둔다). 작은 글자는 놓칠 수 있다.
- 편마다 무엇이 다시 나올지(recurring)는 영상 한 편으로는 알 수 없다. 모델의 짐작이고, 사람이 고칠 값이다.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from frame.adapters.text_locator import locate_text
from frame.adapters.video_tools import extract_frame, require_ffmpeg
from frame.application.errors import LLMError
from frame.application.ports.llm import ImageInput, JsonCompletion, LLMClient
from frame.application.ports.video_analysis import (
    ThemeExtraction,
    ThemeExtractionError,
    ThemeExtractionRequest,
    ThemeExtractor,
)
from frame.domain.enums import EntityKind
from frame.domain.errors import DomainError, ValidationError
from frame.domain.ir import EntitySpec, IntermediateRepresentation, RequirementSpec, SceneSpec
from frame.domain.screen import Region, TextRole, TextSource
from frame.domain.theme import (
    CastMember,
    Condition,
    FormatSpec,
    MusicSpec,
    NarrationBeat,
    NarrationSpec,
    SceneSlot,
    StyleSpec,
    TextRule,
    TextStyle,
    Theme,
    ThemeVariable,
    VariableScope,
    color_name,
    slot_spans_from_shots,
    template_variables,
)
from frame.domain.video_analysis import ScreenText
from frame.domain.vocabulary import DEFAULT_VOCABULARY, PredicateVocabulary, ValueKind

log = logging.getLogger(__name__)

PROMPT_VERSION = "2026-11-14.6"
SCHEMA_NAME = "theme_observation"
_CAST_KINDS = (EntityKind.PERSON, EntityKind.ANIMAL, EntityKind.PROP, EntityKind.OTHER)
_SCENE_LEVEL = ("location", "lighting", "mood", "camera", "time_of_day", "weather")
# 자리마다 달라지는 장면 조건: 스타일(모든 장면의 조건)에는 두지 않는다
_SLOT_ONLY = frozenset({"camera"})
_NON_KEY = re.compile(r"[^a-z0-9]+")
# 자리를 가리키는 말: 'S3' · 'slot 3' · 'slot_3 (closeup)' · 프레임 이름 'S3-F2 10.0s', 또는 숫자만 '3'
_SLOT_REF = re.compile(r"\s*(?:(?:slot|s)[\s_#:-]*(\d+)(?![.\d]).*|(\d+))\s*", re.IGNORECASE)
# 객체 키에 들어오면 걷어내는 겉모습 낱말 (색·크기·새것). 키는 '무엇인가' 만 말한다
_LOOK_WORDS = frozenset(
    "red blue green yellow orange purple pink brown black white gray grey teal beige cream gold "
    "silver navy dark light bright big small large little tiny new old".split()
)


def build_system_prompt(vocabulary: PredicateVocabulary = DEFAULT_VOCABULARY) -> str:
    attributes = ", ".join(
        s.name
        for s in vocabulary.specs()
        if not s.relational and not s.transient and s.name not in _SCENE_LEVEL
    )
    transient = ", ".join(s.name for s in vocabulary.specs() if s.transient)
    relations = ", ".join(s.name for s in vocabulary.specs() if s.relational)
    kinds = ", ".join(str(k) for k in EntityKind)
    roles = ", ".join(str(r) for r in TextRole)
    return f"""You analyse ONE short-form video so that new episodes of the same series can be made.
You get sampled frames grouped by scene slot (S1, S2, ... in time order; each frame is labelled
like "S2-F1 5.3s"), the time range of every slot, and a transcript of the speech with timestamps
(it may be empty). Return ONLY JSON that matches the schema.

Entities
- List every person, animal and object that matters. entity_key is a unique snake_case ASCII name
  for what the thing is, never how it looks: "mug", not "red_mug". kind is one of {kinds}.
- recurring is true when the entity would come back in other episodes of the series (the host, a
  mascot, a signature prop) and false when it belongs to this episode's topic (today's product).
- fixed_attributes are looks that stay the same through the whole video. Predicates: {attributes}.
  Never put actions, poses, expressions, positions or relations there. Values are short lowercase
  English phrases.

Slots (one entry per slot, in order)
- purpose: what this part does in the format, worded so that it fits any episode
  ("the host greets the viewer", "close-up of today's product").
- description: what this video actually shows in the slot.
- purpose and description say what is filmed (people, objects, actions, place). Do not describe
  overlay text there ("shows the price"): overlay text is recorded in screen_texts and text_rules.
- entity_keys: the entities visible in the slot.
- requirements: what is visible in the slot. Object attributes ({attributes}) and what the entity
  does ({transient}) need entity_key. Relations ({relations}) need entity_key and
  related_entity_key, and never involve a BACKGROUND entity. Scene-level conditions
  ({", ".join(_SCENE_LEVEL)}) use entity_key null.
- narration_purpose: what the speech does during the slot ("names the product"), or "" if silent.
- State an attribute in every slot where it can be seen: a red mug is "color: red" in each slot
  it appears in.
- Use only the predicate names listed above. If something does not fit any of them, leave it
  out of requirements and mention it in the description instead.
- Every value is a short lowercase ENGLISH phrase ("white shirt and brown apron", "close up",
  "waves hand to camera"), whatever language the video and the other fields use. The only
  exceptions are text and brand, which keep the exact spelling seen on screen.

Style: conditions that hold for the whole video (scene-level predicates only, and never camera:
framing changes from slot to slot, so camera belongs to the slots), 3 to 5 palette colour names,
and notes on composition, texture and editing feel.

On-screen text
- Text added in editing (OVERLAY) is never an entity and never a requirement: it goes only into
  screen_texts and text_rules. Only text that is physically in the scene (IN_SCENE) may be a
  TEXT entity or a text/brand requirement.
- screen_texts: every piece of text you can read. Copy it exactly, character by character; do not
  correct it. role is one of {roles}. source is OVERLAY for text added in editing (captions,
  titles, price tags, watermarks) and IN_SCENE for text that is physically in the scene (signs,
  product labels). box is the position as fractions of the frame (x, y = top-left corner, w, h),
  or null if you cannot tell.
- text_rules: generalise the OVERLAY texts into rules for future episodes. template is either
  fixed text or text with variables in braces ("{{menu}} {{price}}"). example is the text seen in
  this video. color and background are CSS colour names ("white", "lightyellow") or #RRGGBB
  (background null if none).
- variables: the values that change from episode to episode (names, prices, per-slot captions).
  key is snake_case ASCII, example is this video's value, scope is EPISODE (one value per
  episode) or SLOT (a different value in each slot). Declare every variable a template uses.

Narration tone: how the speaker talks (register, energy), judged from the transcript; "" if there
is no speech. Music: you only see frames, so present is null and mood is "".
Do not invent anything the frames and the transcript do not show."""


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


_STRING = {"type": "string"}
_NULLABLE_STRING = {"type": ["string", "null"]}
_CONDITION = _object({"predicate": _STRING, "value": _STRING})
_BOX = {
    "anyOf": [
        _object(
            {
                "x": {"type": "number"},
                "y": {"type": "number"},
                "w": {"type": "number"},
                "h": {"type": "number"},
            }
        ),
        {"type": "null"},
    ]
}

RESPONSE_SCHEMA: dict[str, Any] = _object(
    {
        "title": _STRING,
        "summary": _STRING,
        "entities": {
            "type": "array",
            "items": _object(
                {
                    "entity_key": _STRING,
                    "kind": {"type": "string", "enum": [str(k) for k in EntityKind]},
                    "description": _STRING,
                    "recurring": {"type": "boolean"},
                    "role": _STRING,
                    "fixed_attributes": {"type": "array", "items": _CONDITION},
                }
            ),
        },
        "slots": {
            "type": "array",
            "items": _object(
                {
                    "slot": _STRING,
                    "purpose": _STRING,
                    "description": _STRING,
                    "entity_keys": {"type": "array", "items": _STRING},
                    "requirements": {
                        "type": "array",
                        "items": _object(
                            {
                                "entity_key": _NULLABLE_STRING,
                                "related_entity_key": _NULLABLE_STRING,
                                "predicate": _STRING,
                                "value": _STRING,
                            }
                        ),
                    },
                    "narration_purpose": _STRING,
                }
            ),
        },
        "style": _object(
            {
                "conditions": {"type": "array", "items": _CONDITION},
                "palette": {"type": "array", "items": _STRING},
                "notes": _STRING,
            }
        ),
        "screen_texts": {
            "type": "array",
            "items": _object(
                {
                    "text": _STRING,
                    "slot": _STRING,
                    "role": {"type": "string", "enum": [str(r) for r in TextRole]},
                    "source": {"type": "string", "enum": [str(s) for s in TextSource]},
                    "box": _BOX,
                    "style": _STRING,
                }
            ),
        },
        "text_rules": {
            "type": "array",
            "items": _object(
                {
                    "rule_key": _STRING,
                    "role": {"type": "string", "enum": [str(r) for r in TextRole]},
                    "template": _STRING,
                    "slots": {"type": "array", "items": _STRING},
                    "box": _BOX,
                    "color": _STRING,
                    "background": _NULLABLE_STRING,
                    "example": _STRING,
                }
            ),
        },
        "variables": {
            "type": "array",
            "items": _object(
                {
                    "key": _STRING,
                    "description": _STRING,
                    "example": _STRING,
                    "scope": {"type": "string", "enum": [str(s) for s in VariableScope]},
                }
            ),
        },
        "narration_tone": _STRING,
        "music": _object({"present": {"type": ["boolean", "null"]}, "mood": _STRING}),
    }
)


@dataclass(frozen=True)
class _Frame:
    label: str
    slot_index: int
    at_ms: int
    image: ImageInput


class VlmThemeExtractor(ThemeExtractor):
    def __init__(
        self,
        ffmpeg: str | None,
        llm: LLMClient,
        *,
        frames_per_slot: int = 3,
        max_frames: int = 24,
        max_side: int = 768,
        max_attempts: int = 2,
        language: str = "Korean",
        timeout_s: float | None = 240.0,
        refine_text_regions: bool = True,
        max_refined_texts: int = 8,
    ) -> None:
        self._ffmpeg = require_ffmpeg(ffmpeg)
        self._llm = llm
        self.frames_per_slot = max(1, int(frames_per_slot))
        self.max_frames = max(1, int(max_frames))
        self.max_side = max_side
        self.max_attempts = max(1, int(max_attempts))
        self.language = language  # 설명·구실·메모를 적을 언어
        self.timeout_s = timeout_s
        self.refine_text_regions = refine_text_regions  # 얹은 문구의 영역을 문구마다 다시 묻는다
        self.max_refined_texts = max(0, int(max_refined_texts))  # 다시 묻는 호출 수의 상한
        self.last_completion: JsonCompletion | None = None  # 디버깅·기록용 원본 응답

    @property
    def signature(self) -> str:
        model = getattr(self._llm, "model", "?")
        regions = "refined" if self.refine_text_regions else "plain"
        return (
            f"vlm_frames:{model}:{PROMPT_VERSION}:{self.frames_per_slot}x{self.max_frames}"
            f"@{self.max_side}:{regions}:{self.language}"
        )

    def extract(self, request: ThemeExtractionRequest) -> ThemeExtraction:
        analysis = request.analysis
        spans = slot_spans_from_shots(analysis.shots, min_slot_ms=request.min_slot_ms)
        frames = self._frames(request.video, spans)
        system = build_system_prompt()
        user = self._user_prompt(request, spans, frames)
        usage = {"input_tokens": 0, "output_tokens": 0}
        last_error = "알 수 없는 오류"
        rejections: list[str] = []  # 재질의마다 무엇이 걸렸는가 (기록에 남긴다)
        prompt = user
        for attempt in range(1, self.max_attempts + 1):
            completion = self._llm.complete_json(
                system,
                prompt,
                RESPONSE_SCHEMA,
                schema_name=SCHEMA_NAME,
                timeout_s=self.timeout_s,
                images=[f.image for f in frames],
            )
            self.last_completion = completion
            usage["input_tokens"] += completion.input_tokens or 0
            usage["output_tokens"] += completion.output_tokens or 0
            try:
                document = json.loads(completion.text)
                if not isinstance(document, dict):
                    raise ValueError("응답이 JSON 객체가 아닙니다")
                theme, source_ir, texts, notes = build_theme(request, spans, document)
            except (ValueError, KeyError, TypeError, AttributeError, DomainError) as exc:
                # AttributeError: strict 가 아닌 서버가 형이 어긋난 응답을 준 경우 (목록 자리에 글 등)
                last_error = (
                    exc.message if isinstance(exc, DomainError) else f"{type(exc).__name__}: {exc}"
                )
                rejections.append(last_error[:300])
                prompt = (
                    f"{user}\n\nYour previous answer was rejected: {last_error}\n"
                    f"Previous answer:\n{completion.text[:4000]}\nReturn the corrected JSON only."
                )
                continue
            refined: int | None = None
            if self.refine_text_regions:
                refined, refine_notes = self._refine_text_boxes(
                    document, request.video, spans, frames, usage
                )
                if refined:
                    theme, source_ir, texts, notes = build_theme(request, spans, document)
                notes = [*notes, *refine_notes]
            detail: dict[str, Any] = {
                "method": "vlm_frames",
                "model": completion.model,
                "prompt_version": PROMPT_VERSION,
                "frames": len(frames),
                "attempts": attempt,
                "rejections": rejections,
                "usage": usage,
            }
            if refined is not None:
                detail["refined_text_regions"] = refined
            return ThemeExtraction(theme, source_ir, texts, tuple(notes), detail)
        log.warning("테마 추출의 응답이 %d회 모두 거절됐습니다: %s", len(rejections), rejections)
        raise ThemeExtractionError(
            f"테마 추출이 {self.max_attempts}회 시도 후 실패했습니다: {last_error}"
        )

    # ---- 얹은 문구의 영역 다시 묻기 ----
    def _refine_text_boxes(
        self,
        document: dict[str, Any],
        video: bytes,
        spans: Sequence[tuple[int, int]],
        frames: Sequence[_Frame],
        usage: dict[str, int],
    ) -> tuple[int, list[str]]:
        """document 의 얹은 문구와 그 문구 규칙의 box 를 다시 물은 영역으로 바꾼다. (바꾼 수, 메모)"""
        notes: list[str] = []
        refined = 0
        asked = 0
        for item in document.get("screen_texts") or ():
            if not isinstance(item, dict) or str(item.get("source") or "").upper() == "IN_SCENE":
                continue
            text = " ".join(str(item.get("text") or "").split())
            index = _slot_index(item.get("slot"), len(spans))
            if not text or index is None:
                continue
            if asked >= self.max_refined_texts:
                notes.append(f"화면 문구가 많아 '{text}' 부터는 영역을 다시 묻지 않았습니다")
                break
            shown = [f.at_ms for f in frames if f.slot_index == index]
            at_ms = shown[len(shown) // 2] if shown else sum(spans[index]) // 2
            image = extract_frame(
                self._ffmpeg, video, at_ms, max_side=1280, min_side=720, image_format="png"
            )
            asked += 1
            try:
                region, completion = locate_text(
                    self._llm,
                    ImageInput(image.data, image.media_type, "frame"),
                    f'caption "{text}"',
                    timeout_s=self.timeout_s,
                )
            except LLMError as exc:
                notes.append(
                    f"화면 문구의 영역을 다시 묻지 못했습니다 ({exc.code}). 처음 받은 영역을 둡니다"
                )
                break  # 한 번 실패하면 나머지도 묻지 않는다 (같은 실패를 되풀이하지 않는다)
            usage["input_tokens"] += completion.input_tokens or 0
            usage["output_tokens"] += completion.output_tokens or 0
            if region is None:
                notes.append(f"화면 문구 '{text}' 의 영역을 다시 찾지 못해 처음 받은 영역을 둡니다")
                continue
            box = region.to_dict()
            item["box"] = box
            refined += 1
            for rule in document.get("text_rules") or ():
                if not isinstance(rule, dict) or _squash(rule.get("example")) != _squash(text):
                    continue
                slots = {_slot_index(s, len(spans)) for s in rule.get("slots") or ()} - {None}
                if not slots or index in slots:
                    rule["box"] = dict(box)
        return refined, notes

    # ---- 프레임 ----
    def _frames(self, video: bytes, spans: Sequence[tuple[int, int]]) -> list[_Frame]:
        per_slot = max(1, min(self.frames_per_slot, self.max_frames // max(1, len(spans))))
        frames: list[_Frame] = []
        for index, (start, end) in enumerate(spans):
            if len(frames) >= self.max_frames:
                break  # 자리가 상한보다 많으면 뒤의 자리는 프레임 없이 시간과 말소리만 준다
            for number in range(per_slot):
                at_ms = start + int((end - start) * (number + 0.5) / per_slot)
                label = f"S{index + 1}-F{number + 1} {at_ms / 1000:.1f}s"
                image = extract_frame(self._ffmpeg, video, at_ms, max_side=self.max_side)
                frames.append(
                    _Frame(label, index, at_ms, ImageInput(image.data, image.media_type, label))
                )
        return frames

    def _user_prompt(
        self,
        request: ThemeExtractionRequest,
        spans: Sequence[tuple[int, int]],
        frames: Sequence[_Frame],
    ) -> str:
        media = request.analysis.media
        lines = [
            f"Video: {media.width}x{media.height} ({media.aspect_label}), "
            f"{media.duration_ms / 1000:.1f} s, audio: {'yes' if media.has_audio else 'no'}.",
            "Write title, summary, description, purpose, role, notes, tone and narration_purpose in "
            f"{self.language}. Keep every requirement value, fixed attribute value and style condition "
            "value in English.",
        ]
        if request.title.strip():
            lines.append(f"Title given by the user: {request.title.strip()}")
        lines.append("Scene slots:")
        for index, (start, end) in enumerate(spans):
            labels = ", ".join(f.label for f in frames if f.slot_index == index) or "no frames"
            lines.append(f"S{index + 1} {start / 1000:.1f}-{end / 1000:.1f}s (frames: {labels})")
        cuts = ", ".join(f"{s.duration_ms / 1000:.1f}" for s in request.analysis.shots)
        lines.append(f"Original shot lengths in seconds: {cuts}")
        transcript = request.analysis.transcript
        if transcript is None or transcript.is_empty:
            lines.append("Transcript: none (no speech was recognised).")
        else:
            lines.append(f"Transcript (language: {transcript.language or 'unknown'}):")
            lines += [
                f"[{s.start_ms / 1000:.1f}-{s.end_ms / 1000:.1f}] {s.text.strip()}"
                for s in transcript.segments
                if s.text.strip()
            ]
        return "\n".join(lines)


# ---- 응답을 테마와 원본 IR 로 다듬기 (결정적, 순수 함수) -----------------------------------------
def build_theme(
    request: ThemeExtractionRequest,
    spans: Sequence[tuple[int, int]],
    document: Mapping[str, Any],
) -> tuple[Theme, IntermediateRepresentation, tuple[ScreenText, ...], list[str]]:
    """모델의 응답(document)에서 규칙에 맞는 것만 골라 테마·원본 IR·화면 문구를 만든다.

    규칙을 통과하지 못하면 ValidationError (부른 쪽이 모델에 되돌려 보낸다).
    """
    _check_shapes(document)
    notes: list[str] = []
    slot_keys = [f"slot_{i}" for i in range(1, len(spans) + 1)]
    answers = _slot_answers(document.get("slots"), len(spans))
    key_of, entities, raw_entities = _entities(document.get("entities"), notes)
    kinds = {e.entity_key: e.kind for e in entities}

    cast = _cast(raw_entities, key_of, entities, notes)
    cast_keys = {c.entity_key for c in cast}
    style_conditions = [
        condition
        for condition in _scene_conditions(
            _mapping(document.get("style")).get("conditions"), notes, "스타일"
        )
        if condition.predicate not in _SLOT_ONLY
    ]
    style_by_predicate = {c.predicate: c for c in style_conditions}

    overlay_texts = {
        _squash(item.get("text"))
        for item in document.get("screen_texts") or ()
        if isinstance(item, dict) and str(item.get("source") or "").upper() != "IN_SCENE"
    } - {""}
    overlay_entities: set[str] = set()
    scenes: list[SceneSpec] = []
    slots: list[SceneSlot] = []
    for index, (slot_key, (start, end)) in enumerate(zip(slot_keys, spans, strict=True)):
        answer = answers.get(index, {})
        entity_keys = _unique(
            key
            for key in (_lookup(k, key_of) for k in answer.get("entity_keys") or ())
            if key is not None
        )
        requirements = _requirements(
            slot_key, answer.get("requirements"), key_of, kinds, entity_keys, notes
        )
        # 편집에서 얹은 문구를 객체로 적어 온 것은 뺀다: 얹은 문구는 생성 조건이 아니다 (ADR-0027)
        captions = {
            r.entity_key
            for r in requirements
            if kinds.get(r.entity_key or "") is EntityKind.TEXT
            and DEFAULT_VOCABULARY.value_kind(r.predicate) is ValueKind.LITERAL
            and _squash(r.expected_value) in overlay_texts
        }
        if captions:
            overlay_entities.update(k for k in captions if k is not None)
            requirements = [r for r in requirements if r.entity_key not in captions]
            entity_keys = [k for k in entity_keys if k not in captions]
        # 고정 출연의 고정 속성과 스타일의 조건은 그 장면의 요구조건이기도 하다 (모델이 빠뜨려도 넣는다)
        seen = {r.key for r in requirements}
        for member in cast:
            if member.entity_key not in entity_keys:
                continue
            for condition in member.fixed_attributes:
                extra = RequirementSpec(
                    slot_key, condition.predicate, condition.value, member.entity_key
                )
                if extra.key not in seen:
                    requirements.append(extra)
                    seen.add(extra.key)
        own_scene_level = [r for r in requirements if r.entity_key is None]
        for condition in style_conditions:
            extra = RequirementSpec(slot_key, condition.predicate, condition.value)
            if extra.key not in seen:
                requirements.append(extra)
                seen.add(extra.key)
        description = " ".join(str(answer.get("description") or "").split())
        purpose = " ".join(str(answer.get("purpose") or "").split())
        scenes.append(
            SceneSpec(
                slot_key,
                start,
                end,
                description or purpose,
                tuple(entity_keys),
                tuple(requirements),
            )
        )
        slots.append(
            SceneSlot(
                slot_key=slot_key,
                duration_ms=end - start,
                purpose=purpose or description,
                cast_keys=tuple(k for k in entity_keys if k in cast_keys),
                # 이 자리만의 장면 조건: 스타일에 없거나 스타일과 값이 다른 것
                conditions=tuple(
                    Condition(r.predicate, r.expected_value)
                    for r in own_scene_level
                    if style_by_predicate.get(r.predicate) is None
                    or style_by_predicate[r.predicate].value != r.expected_value
                ),
            )
        )
    if overlay_entities:
        notes.append(
            f"편집에서 얹은 문구를 객체로 적은 것을 원본 IR 에서 뺐습니다: {sorted(overlay_entities)}"
        )
    used = {key for scene in scenes for key in scene.entity_keys}
    dropped = [e.entity_key for e in entities if e.entity_key not in used]
    if dropped:
        notes.append(f"어느 자리에도 나오지 않는 객체를 뺐습니다: {dropped}")
    source_ir = IntermediateRepresentation(
        entities=tuple(e for e in entities if e.entity_key in used), scenes=tuple(scenes)
    )
    source_ir.validate()

    variables = _variables(document.get("variables"), notes)
    rules, variables = _text_rules(document.get("text_rules"), slot_keys, variables, notes)
    transcript = request.analysis.transcript
    spoken = transcript is not None and not transcript.is_empty
    beats: list[NarrationBeat] = []
    if spoken:
        assert transcript is not None
        for index, (slot_key, (start, end)) in enumerate(zip(slot_keys, spans, strict=True)):
            said = " ".join(s.text.strip() for s in transcript.within(start, end))
            purpose = " ".join(str(answers.get(index, {}).get("narration_purpose") or "").split())
            if said or purpose:
                beats.append(NarrationBeat(slot_key, purpose, said))
    music = _mapping(document.get("music"))
    style = _mapping(document.get("style"))
    media = request.analysis.media
    theme = Theme(
        theme_id=request.theme_id,
        title=request.title.strip()
        or str(document.get("title") or "").strip()
        or request.source.title
        or "테마",
        summary=" ".join(str(document.get("summary") or "").split()),
        source=request.source,
        format=FormatSpec(
            slots=tuple(slots),
            aspect_ratio=media.aspect_label,
            observed_shot_ms=tuple(s.duration_ms for s in request.analysis.shots),
        ),
        cast=tuple(c for c in cast if c.entity_key in used),
        style=StyleSpec(
            conditions=tuple(style_conditions),
            palette=tuple(str(c).strip() for c in style.get("palette") or () if str(c).strip())[:6],
            notes=" ".join(str(style.get("notes") or "").split()),
        ),
        text_rules=tuple(rules),
        narration=NarrationSpec(
            present=spoken,
            language=transcript.language if spoken and transcript is not None else None,
            tone=" ".join(str(document.get("narration_tone") or "").split()) if spoken else "",
            pace_cps=transcript.pace_cps if spoken and transcript is not None else None,
            beats=tuple(beats),
        ),
        music=MusicSpec(
            present=music.get("present") if isinstance(music.get("present"), bool) else None,
            mood=" ".join(str(music.get("mood") or "").split()),
        ),
        variables=tuple(variables),
    )
    theme.validate()
    texts = _screen_texts(document.get("screen_texts"), spans)
    return theme, source_ir, texts, notes


def _slot_index(raw: Any, count: int) -> int | None:
    """모델이 가리킨 자리('S3', 'slot 3')의 0 기반 번호. 범위 밖이면 None."""
    number = _slot_number(raw)
    if number is None or not 0 < number <= count:
        return None
    return number - 1


def _slot_number(raw: Any) -> int | None:
    """자리 번호 (1부터). 'S3'·'slot 3'·'3', 그리고 프레임 이름 'S3-F2 10.0s' 를 읽는다.

    시각("5.3s")처럼 자리가 아닌 숫자는 읽지 않는다. 아무 숫자나 집으면 모델이 시각을 적어 왔을 때
    그 숫자가 자리 번호가 된다 (감사 BTS-32).
    """
    match = _SLOT_REF.fullmatch(str(raw or ""))
    if match is None:
        return None
    return int(match.group(1) or match.group(2))


def _squash(raw: Any) -> str:
    """문구를 견줄 수 있게: 공백을 없앤다."""
    return "".join(str(raw or "").split())


def _plain(raw: Any) -> str:
    """키를 견줄 수 있게: 소문자, 영문·숫자 아닌 것은 밑줄."""
    return _NON_KEY.sub("_", str(raw or "").strip().lower()).strip("_")


def _unique(items: Any) -> list[str]:
    return list(dict.fromkeys(items))


def _slot_answers(raw: Any, count: int) -> dict[int, Mapping[str, Any]]:
    """모델의 자리별 답을 자리 번호에 맞춘다. 'S2' 처럼 가리킨 것을 먼저 쓰고, 없으면 나온 순서로 채운다."""
    items = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
    answers: dict[int, Mapping[str, Any]] = {}
    unplaced: list[Mapping[str, Any]] = []
    for item in items:
        number = _slot_number(item.get("slot"))
        index = number - 1 if number is not None else -1
        if 0 <= index < count and index not in answers:
            answers[index] = item
        else:
            unplaced.append(item)
    for index in range(count):
        if index not in answers and unplaced:
            answers[index] = unplaced.pop(0)
    if len(answers) < count:
        missing = [f"S{i + 1}" for i in range(count) if i not in answers]
        raise ValidationError(f"자리마다 답이 있어야 합니다. 빠진 자리: {missing}")
    return answers


def _entities(
    raw: Any, notes: list[str]
) -> tuple[dict[str, str], list[EntitySpec], dict[str, Mapping[str, Any]]]:
    """(모델이 쓴 키 -> 다듬은 키, 객체 목록, 다듬은 키 -> 모델의 원래 항목)."""
    key_of: dict[str, str] = {}
    entities: list[EntitySpec] = []
    originals: dict[str, Mapping[str, Any]] = {}
    for position, item in enumerate(raw if isinstance(raw, list) else [], start=1):
        if not isinstance(item, dict):
            continue
        raw_key = _raw_key(item.get("entity_key"))
        ascii_key = _plain(raw_key)  # 한글 키는 빈 문자열이 된다
        plain = ascii_key or f"entity_{position}"
        if plain[0].isdigit():
            plain = f"entity_{plain}"
        bare = "_".join(word for word in plain.split("_") if word not in _LOOK_WORDS)
        if bare and bare != plain and bare not in originals and not bare[0].isdigit():
            notes.append(f"객체 키 '{plain}' 를 '{bare}' 로 고쳤습니다 (키에 겉모습을 넣지 않는다)")
            key_of[plain] = bare
            plain = bare
        if plain in originals:
            notes.append(f"객체 키 '{plain}' 가 두 번 나와 뒤의 것을 뺐습니다")
            continue
        try:
            kind = EntityKind(str(item.get("kind") or "").strip().upper())
        except ValueError:
            kind = EntityKind.OTHER
        # 모델이 쓴 키 그대로도 찾을 수 있게 한다. 빈 키는 넣지 않는다: 영문이 아닌 키가 모두 빈 키 하나로 모여
        # 마지막 객체를 가리켰다 (감사 BTS-25)
        for name in (raw_key, ascii_key, plain):
            if name:
                key_of.setdefault(name, plain)
        originals[plain] = item
        entities.append(
            EntitySpec(plain, kind, " ".join(str(item.get("description") or "").split()))
        )
    return key_of, entities, originals


def _conditions(raw: Any, notes: list[str], owner: str) -> list[Condition]:
    conditions: list[Condition] = []
    seen: set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            condition = Condition(str(item.get("predicate") or ""), str(item.get("value") or ""))
        except ValidationError:
            continue
        if condition.predicate in seen:
            notes.append(
                f"{owner} 의 조건 '{condition.predicate}' 가 두 번 나와 뒤의 것을 뺐습니다"
            )
            continue
        seen.add(condition.predicate)
        conditions.append(condition)
    return conditions


def _scene_conditions(raw: Any, notes: list[str], owner: str) -> list[Condition]:
    kept = []
    for condition in _conditions(raw, notes, owner):
        if DEFAULT_VOCABULARY.is_relational(condition.predicate):
            notes.append(f"{owner} 의 관계 조건 '{condition.predicate}' 를 뺐습니다")
            continue
        kept.append(condition)
    return kept


def _cast(
    originals: Mapping[str, Mapping[str, Any]],
    key_of: Mapping[str, str],
    entities: Sequence[EntitySpec],
    notes: list[str],
) -> list[CastMember]:
    cast: list[CastMember] = []
    for entity in entities:
        item = originals[entity.entity_key]
        if not item.get("recurring") or entity.kind not in _CAST_KINDS:
            continue
        fixed = []
        for condition in _conditions(item.get("fixed_attributes"), notes, f"'{entity.entity_key}'"):
            if DEFAULT_VOCABULARY.is_relational(
                condition.predicate
            ) or DEFAULT_VOCABULARY.is_transient(condition.predicate):
                notes.append(
                    f"'{entity.entity_key}' 의 '{condition.predicate}' 는 고정 속성이 될 수 없어 뺐습니다"
                )
                continue
            fixed.append(condition)
        cast.append(
            CastMember(
                entity_key=entity.entity_key,
                kind=entity.kind,
                description=entity.description,
                role=" ".join(str(item.get("role") or "").split()),
                fixed_attributes=tuple(fixed),
            )
        )
    return cast


def _requirements(
    slot_key: str,
    raw: Any,
    key_of: Mapping[str, str],
    kinds: Mapping[str, EntityKind],
    entity_keys: list[str],
    notes: list[str],
) -> list[RequirementSpec]:
    """자리의 요구조건. IR 의 규칙에 맞지 않는 것은 버리고, 조건이 가리키는 객체는 그 장면에 연결한다.

    - 어휘에 없는 술어는 버린다 (모델이 지어낸 visible·blurred 같은 것).
    - 관계 술어의 상대가 객체가 아니면(카메라·시청자) 그 객체의 행동으로 옮긴다: looks_at camera -> action.
    """
    requirements: list[RequirementSpec] = []
    seen: set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        subject = _lookup(item.get("entity_key"), key_of)
        related = _lookup(item.get("related_entity_key"), key_of)
        if item.get("entity_key") and subject is None:
            notes.append(f"장면 '{slot_key}' 의 조건이 없는 객체를 가리켜 뺐습니다: {item}")
            continue
        try:
            predicate = DEFAULT_VOCABULARY.normalize(str(item.get("predicate") or ""))
        except ValidationError:
            continue
        relational = DEFAULT_VOCABULARY.is_relational(predicate)
        value = str(item.get("value") or "").strip()
        if relational is None:
            notes.append(f"장면 '{slot_key}' 의 어휘에 없는 술어 '{predicate}' 를 뺐습니다")
            continue
        if relational is True and related is None and subject is not None:
            # 상대가 객체가 아니다: 상대로 적어 온 말(또는 값)을 붙여 행동으로 옮긴다
            target = value or " ".join(str(item.get("related_entity_key") or "").split("_"))
            if not target:
                notes.append(
                    f"장면 '{slot_key}' 의 관계 조건 '{predicate}' 를 뺐습니다 (상대 없음)"
                )
                continue
            predicate, value, relational = (
                "action",
                f"{predicate.replace('_', ' ')} {target}",
                False,
            )
        if relational is True:
            background = EntityKind.BACKGROUND in (
                kinds.get(subject or ""),
                kinds.get(related or ""),
            )
            if subject is None or related is None or subject == related or background:
                notes.append(
                    f"장면 '{slot_key}' 의 관계 조건 '{predicate}' 를 뺐습니다 (상대 객체 규칙)"
                )
                continue
            value = related
        else:
            related = None  # 속성 조건은 상대 객체를 갖지 않는다
        try:
            requirement = RequirementSpec(slot_key, predicate, value, subject, related)
        except ValidationError:
            continue
        if not requirement.expected_value or requirement.key in seen:
            continue
        seen.add(requirement.key)
        for key in (subject, related):
            if key is not None and key not in entity_keys:
                entity_keys.append(key)
        requirements.append(requirement)
    return requirements


def _lookup(raw: Any, key_of: Mapping[str, str]) -> str | None:
    """모델이 가리킨 객체 키 -> 다듬은 키. 없으면 None.

    영문·숫자로 다듬을 수 없는 키(한글 등)가 정의된 객체와 맞지 않으면 ValidationError 로 되돌려 보낸다.
    그런 키를 조용히 버리면 장면에서 객체가 빠진 테마가 오류 없이 저장된다.
    """
    if not raw:
        return None
    raw_key = _raw_key(raw)
    found = key_of.get(raw_key)
    if found is not None:
        return found
    ascii_key = _plain(raw_key)
    if not ascii_key:
        raise ValidationError(
            f"entity key '{raw_key}' is not defined in entities. Refer to entities by the exact "
            "entity_key you defined (lowercase English snake_case)"
        )
    return key_of.get(ascii_key)


def _raw_key(raw: Any) -> str:
    """모델이 준 키 그대로 (공백만 다듬는다). 영문이 아닌 키를 서로 가르는 데 쓴다."""
    return " ".join(str(raw or "").split())


def _mapping(raw: Any) -> Mapping[str, Any]:
    return raw if isinstance(raw, Mapping) else {}


_LISTS = ("entities", "slots", "screen_texts", "text_rules", "variables")
_OBJECTS = ("style", "music")


def _check_shapes(document: Mapping[str, Any]) -> None:
    """응답의 큰 모양을 본다. strict 가 아닌 서버는 목록 자리에 글이나 객체를 줄 수 있다 (감사 BTS-15)."""
    for name in _LISTS:
        if document.get(name) is not None and not isinstance(document[name], list):
            raise ValidationError(f"'{name}' must be a list")
    for name in _OBJECTS:
        if document.get(name) is not None and not isinstance(document[name], Mapping):
            raise ValidationError(f"'{name}' must be an object")


def _variables(raw: Any, notes: list[str]) -> list[ThemeVariable]:
    variables: list[ThemeVariable] = []
    seen: set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        key = _plain(item.get("key"))
        if not key or key[0].isdigit() or key in seen:
            continue
        seen.add(key)
        try:
            scope = VariableScope(str(item.get("scope") or "EPISODE").upper())
        except ValueError:
            scope = VariableScope.EPISODE
        variables.append(
            ThemeVariable(
                key,
                " ".join(str(item.get("description") or "").split()),
                " ".join(str(item.get("example") or "").split()),
                scope,
            )
        )
    return variables


def _text_rules(
    raw: Any, slot_keys: Sequence[str], variables: list[ThemeVariable], notes: list[str]
) -> tuple[list[TextRule], list[ThemeVariable]]:
    rules: list[TextRule] = []
    declared = {v.key for v in variables}
    seen: set[str] = set()
    for position, item in enumerate(raw if isinstance(raw, list) else [], start=1):
        if not isinstance(item, dict):
            continue
        template = " ".join(str(item.get("template") or "").split())
        if not template:
            continue
        # 틀의 변수 이름도 변수 키와 같은 규칙으로 맞춘다
        for name in template_variables(template):
            template = template.replace("{" + name + "}", "{" + (_plain(name) or name) + "}")
        key = _plain(item.get("rule_key")) or f"rule_{position}"
        if key in seen:
            key = f"{key}_{position}"
        seen.add(key)
        for name in template_variables(template):
            if name not in declared:
                declared.add(name)
                variables.append(ThemeVariable(name))
                notes.append(f"문구 규칙 '{key}' 가 쓰는 변수 '{name}' 를 테마의 변수로 더했습니다")
        try:
            role = TextRole(str(item.get("role") or "").upper())
        except ValueError:
            role = TextRole.OTHER
        slots = _unique(
            slot_keys[index]
            for index in (_slot_index(s, len(slot_keys)) for s in item.get("slots") or ())
            if index is not None
        )
        base = TextStyle()
        background = item.get("background")
        style = TextStyle(
            color=color_name(item.get("color")) or base.color,
            # 띠가 없다고 한 것(null)은 그대로, 모르는 색 표기는 기본 띠로
            background=None if background is None else (color_name(background) or base.background),
        )
        rules.append(
            TextRule(
                rule_key=key,
                template=template,
                role=role,
                slot_keys=tuple(slots),
                region=_region(item.get("box")),
                style=style,
                example=" ".join(str(item.get("example") or "").split()),
            )
        )
    return rules, variables


def _region(raw: Any) -> Region | None:
    if not isinstance(raw, dict):
        return None
    return Region.clamped(raw.get("x"), raw.get("y"), raw.get("w"), raw.get("h"))


def _screen_texts(raw: Any, spans: Sequence[tuple[int, int]]) -> tuple[ScreenText, ...]:
    texts: list[ScreenText] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get("text") or "").split())
        index = _slot_index(item.get("slot"), len(spans))
        if not text or index is None:
            continue
        start, end = spans[index]
        try:
            role = TextRole(str(item.get("role") or "").upper())
        except ValueError:
            role = TextRole.OTHER
        try:
            source = TextSource(str(item.get("source") or "").upper())
        except ValueError:
            source = TextSource.OVERLAY
        texts.append(
            ScreenText(
                text=text,
                start_ms=start,
                end_ms=end,
                region=_region(item.get("box")),
                role=role,
                source=source,
                style=" ".join(str(item.get("style") or "").split()),
            )
        )
    return tuple(texts)
