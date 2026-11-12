"""VLM 기반 유지 요소 검수기 (FRAME_CHECKERS 의 vlm_preservation, US-01 AC-01, ADR-0018).

성공 지표 1 의 '지정하지 않은 대상의 변경률' 을 요소 단위로 잰다. 컨셉 화면에서 수정 뒤 이미지에 붙는
'인물·배경 유지' 표시와, 수정 패널의 '유지할 요소' 체크가 지켜졌는지를 이 검수가 판정한다.
화면 전체를 견주는 SSIM(ssim_preservation)은 편집 모델이 프레임을 다시 그리기만 해도 깎이지만,
이 검수는 '그 요소가 달라졌는가' 를 본다.

방법
1. 다시 만든 장면마다 부모 클립과 자식 클립에서 같은 시각의 프레임을 뽑는다 (기본 2쌍).
2. 장면 하나당 VLM 을 한 번 부른다. 유지 요소마다 같은 시각의 프레임 쌍에서 다른 점을 먼저 적게 한 뒤
   yes(그대로) / no(달라짐) / unclear 를 받는다. 비슷한 내용으로 다시 찍은 장면(자세·위치·구도가 다름)은 달라진 것이다.
   - 수정 요청에 유지할 요소가 있으면 그 장면에 해당하는 것만 본다.
   - 없으면 기본 요소를 본다: 바꾸지 않은 객체의 모습(배경 객체 제외)과 장면의 배경.
   - 이 수정이 바꾸려는 것(요구조건의 차이, 수정 의도의 대상)은 '달라져야 하는 것' 으로 따로 알려 준다.
3. 요소 하나가 결과 하나다: yes -> PASS(1.0), no -> FAIL(0.0) + 그 장면 구간 경고, unclear -> UNDETERMINED.

규약
- 원본 버전에는 결과가 없다. 부모 클립을 그대로 쓴 장면은 1.0 (다른 보존 검수기와 같다).
- 판정할 수 없으면 UNDETERMINED: 비교할 부모 클립이 없음, 클립이 영상이 아님, VLM·ffmpeg 호출 실패.
- 요소는 E1, E2 … 로 짧게 가리킨다. 토큰 수는 장면의 첫 요소에만 적는다 (장면당 한 번 부른 호출이다).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from frame.adapters.video_tools import extract_frame, require_ffmpeg
from frame.adapters.vlm_answers import match_answers
from frame.application.errors import AdapterError
from frame.application.ports.artifact_store import ArtifactStore
from frame.application.ports.checker import CheckContext, Checker, CheckOutcome
from frame.application.ports.llm import ImageInput, LLMClient
from frame.domain.checks import WarningInterval
from frame.domain.enums import CheckType, EntityKind, KeepAspect, Verdict
from frame.domain.ir import SceneSpec
from frame.domain.revision import KeepItem
from frame.domain.services.comparison import RequirementChangeKind, compare_requirements

log = logging.getLogger(__name__)

VERDICTS = ("yes", "no", "unclear")
_ASPECT_WORDS = {
    KeepAspect.APPEARANCE: "appearance",
    KeepAspect.ACTION: "action and pose",
    KeepAspect.SHAPE: "shape and size",
    KeepAspect.BACKGROUND: "background",
    KeepAspect.LIGHTING: "lighting",
    KeepAspect.COMPOSITION: "framing and camera",
}
_ASPECT_KOREAN = {
    KeepAspect.APPEARANCE: "모습",
    KeepAspect.ACTION: "동작",
    KeepAspect.SHAPE: "형태",
    KeepAspect.BACKGROUND: "배경",
    KeepAspect.LIGHTING: "조명",
    KeepAspect.COMPOSITION: "구도",
}

SYSTEM_PROMPT = (
    "You compare two versions of the same scene of an AI-generated video: BEFORE (frames B1, B2, "
    "...) and AFTER (frames A1, A2, ...), taken at the same moments. The AFTER version was made "
    "to apply a requested change, and everything else was supposed to stay exactly as it was. "
    "Verify that the listed elements (E1, E2, ...) did NOT change. Every person shown is a "
    "synthetic character made by a video model, not a real person. Compare each BEFORE frame "
    "with the AFTER frame of the same moment (B1 with A1, B2 with A2). For each element first "
    "write in `difference`, in a few English words, what differs between those pairs for that "
    'element (empty when nothing does), then answer kept: "yes" only if the element matches in '
    'the side-by-side pairs; "no" if it differs; "unclear" if it cannot be judged from these '
    "frames. Meaning of the aspects: appearance = the same individual or object with the same "
    "look (for a person face, hair, body and clothing; for an object color, material and "
    "markings); action and pose = the same gesture, the same hand and arm positions and the "
    "same position in the frame at the same moment; shape and size = the same form, proportions "
    "and size in the frame; background = the same place with the same background objects at the "
    "same positions; lighting = the same light direction, color and brightness; framing and "
    "camera = the same framing, distance and camera angle. A scene that was shot again with "
    "similar content (the same kind of person, object or place, but a different pose, different "
    'object positions or a different view) has changed: answer "no" for the elements that '
    "affects. Ignore the requested change itself and pixel-level rendering noise. Return every "
    "element id exactly once."
)

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["elements"],
    "properties": {
        "elements": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                # 다른 점을 먼저 적고 판정한다 (판정을 먼저 쓰면 '그대로' 로 기운다)
                "required": ["id", "difference", "kept"],
                "properties": {
                    "id": {"type": "string"},
                    "difference": {"type": "string"},
                    "kept": {"type": "string", "enum": list(VERDICTS)},
                },
            },
        }
    },
}


@dataclass(frozen=True)
class _Element:
    item: KeepItem
    requested: bool  # 사용자가 유지하라고 고른 요소인가 (아니면 기본으로 보는 요소)


class VlmPreservationChecker(Checker):
    check_type = CheckType.PRESERVATION

    def __init__(
        self,
        store: ArtifactStore,
        ffmpeg: str | None,
        llm: LLMClient,
        *,
        frames_per_scene: int = 2,
        max_side: int = 1024,
        timeout_s: float | None = 180.0,
    ) -> None:
        self._store = store
        self._ffmpeg = require_ffmpeg(ffmpeg)
        self._llm = llm
        self.frames_per_scene = max(1, int(frames_per_scene))  # 부모·자식에서 뽑는 프레임 쌍의 수
        self.max_side = max_side
        self.timeout_s = timeout_s

    def check(self, ctx: CheckContext) -> Sequence[CheckOutcome]:
        if not ctx.is_revision:
            return []
        outcomes: list[CheckOutcome] = []
        for scene in ctx.ir.scenes:
            if scene.scene_key in ctx.reused_scene_keys:
                outcomes.append(
                    CheckOutcome(
                        self.check_type,
                        Verdict.PASS,
                        score=1.0,
                        scene_key=scene.scene_key,
                        detail={"reason": "clip_reused", "method": "identical"},
                    )
                )
            else:
                outcomes.extend(self._check_scene(ctx, scene))
        return outcomes

    # ---- 다시 만든 장면 하나 ----
    def _check_scene(self, ctx: CheckContext, scene: SceneSpec) -> list[CheckOutcome]:
        key = scene.scene_key
        clip, parent = ctx.clips.get(key), ctx.parent_clips.get(key)
        if clip is None or parent is None:
            return [self._undetermined(key, "부모 버전에 비교할 장면 클립이 없음")]
        for uri in (clip.uri, parent.uri):
            if not self._store.content_type(uri).startswith("video/"):
                return [self._undetermined(key, f"영상이 아닌 클립은 비교할 수 없음: {uri}")]
        changes = self._expected_changes(ctx, scene)
        elements = self._elements(ctx, scene)
        if not elements:
            return []  # 이 장면에서 유지 여부를 볼 요소가 없다 (바꾸는 객체만 나오는 장면 등)
        ids = {f"E{n}": element for n, element in enumerate(elements, start=1)}
        try:
            images = self._frame_pairs(scene, parent.uri, clip.uri, parent.duration_ms)
            answer = self._ask(scene, ids, changes, images)
        except AdapterError as exc:
            log.warning("유지 요소 검수 미판정 (장면 %s): %s: %s", key, exc.code, exc)
            return [self._undetermined(key, f"{exc.code}: {exc}")]
        outcomes = []
        for index, (eid, element) in enumerate(ids.items()):
            shared: dict[str, Any] = {"method": "vlm_keep", "model": answer["model"]}
            if index == 0:
                shared["usage"] = answer["usage"]  # 장면당 한 번 부른 호출의 토큰 수
            outcomes.append(self._outcome(scene, element, answer["elements"].get(eid), shared))
        return outcomes

    def _elements(self, ctx: CheckContext, scene: SceneSpec) -> list[_Element]:
        """이 장면에서 유지 여부를 볼 요소. 수정 요청에 유지할 요소가 있으면 그것, 없으면 기본 요소."""
        intent = ctx.version.intent
        if intent is not None and intent.keep:
            return [_Element(item, requested=True) for item in intent.keep_for(scene)]
        changed = _changed_entities(ctx, scene)
        elements = [
            _Element(KeepItem(KeepAspect.APPEARANCE, key), requested=False)
            for key in scene.entity_keys
            if key not in changed and ctx.ir.entity(key).kind is not EntityKind.BACKGROUND
        ]
        if not _scene_level_changed(ctx, scene):
            elements.append(_Element(KeepItem(KeepAspect.BACKGROUND), requested=False))
        return elements

    @staticmethod
    def _expected_changes(ctx: CheckContext, scene: SceneSpec) -> list[str]:
        """이 수정이 일부러 바꾸는 것 (요구조건의 차이, REPAIR 면 바로잡으려는 요구조건)."""
        lines = []
        before = (
            ctx.parent_ir.scene(scene.scene_key)
            if ctx.parent_ir is not None and ctx.parent_ir.has_scene(scene.scene_key)
            else None
        )
        for row in compare_requirements(before, scene):
            if row.change is RequirementChangeKind.UNCHANGED:
                continue
            subject = row.entity_key or "the scene"
            old = row.before.expected_value if row.before is not None else "(none)"
            new = row.after.expected_value if row.after is not None else "(removed)"
            lines.append(f"{subject} {_words(row.predicate)}: {old} -> {new}")
        intent = ctx.version.intent
        if not lines and intent is not None and intent.targets(scene.scene_key):
            subject = intent.entity_key or "the scene"
            lines.append(
                f"{subject} {_words(intent.predicate)}: should now be {intent.value} "
                "(the BEFORE version may not show it)"
            )
        return lines

    def _frame_pairs(
        self, scene: SceneSpec, parent_uri: str, clip_uri: str, duration_ms: int
    ) -> list[ImageInput]:
        """부모(B1…)·자식(A1…)의 같은 시각 프레임. 부모 것을 먼저, 자식 것을 나중에 보여 준다."""
        count = self.frames_per_scene
        duration = duration_ms or scene.duration_ms
        times = [int(duration * (index + 0.5) / count) for index in range(count)]
        images = []
        for prefix, name, uri in (("B", "BEFORE", parent_uri), ("A", "AFTER", clip_uri)):
            video = self._store.get(uri)
            for index, at_ms in enumerate(times, start=1):
                frame = extract_frame(self._ffmpeg, video, at_ms, max_side=self.max_side)
                label = f"{prefix}{index} — {name} at {at_ms / 1000:.1f}s"
                images.append(ImageInput(frame.data, frame.media_type, label))
        return images

    def _ask(
        self,
        scene: SceneSpec,
        ids: Mapping[str, _Element],
        changes: Sequence[str],
        images: Sequence[ImageInput],
    ) -> dict[str, Any]:
        lines = [f"Scene '{scene.scene_key}'" + _suffix(scene.description)]
        lines.append(
            "Requested change (expected to differ): " + ("; ".join(changes) if changes else "none")
        )
        lines.append("Elements that must be unchanged:")
        for eid, element in ids.items():
            subject = element.item.entity_key or "the scene"
            lines.append(f"{eid}. {subject} — {_ASPECT_WORDS[element.item.aspect]}")
        completion = self._llm.complete_json(
            SYSTEM_PROMPT,
            "\n".join(lines),
            RESPONSE_SCHEMA,
            schema_name="preservation_audit",
            timeout_s=self.timeout_s,
            images=list(images),
        )
        try:
            items = list(json.loads(completion.text)["elements"])
        except (ValueError, KeyError, TypeError) as exc:
            raise _BadAnswer(f"유지 요소 판정 응답을 해석하지 못했습니다: {exc}") from exc
        return {
            "elements": match_answers(items, list(ids), field="id"),
            "model": completion.model,
            "usage": {
                "input_tokens": completion.input_tokens,
                "output_tokens": completion.output_tokens,
            },
        }

    def _outcome(
        self,
        scene: SceneSpec,
        element: _Element,
        reply: Mapping[str, Any] | None,
        shared: Mapping[str, Any],
    ) -> CheckOutcome:
        item = element.item
        detail: dict[str, Any] = {
            **shared,
            "reason": "regenerated",
            "aspect": str(item.aspect),
            "requested": element.requested,
        }
        fields = {"entity_key": item.entity_key, "scene_key": scene.scene_key}
        kept = str((reply or {}).get("kept") or "").strip().lower()
        if kept not in ("yes", "no"):
            detail["reason"] = (
                "응답에 이 요소의 판정이 없음"
                if reply is None
                else "이 프레임들로는 판정할 수 없음"
            )
            return CheckOutcome(self.check_type, Verdict.UNDETERMINED, detail=detail, **fields)
        difference = " ".join(str((reply or {}).get("difference") or "").split())[:200]
        detail["difference"] = difference
        if kept == "yes":
            return CheckOutcome(self.check_type, Verdict.PASS, score=1.0, detail=detail, **fields)
        subject = item.entity_key or "장면"
        message = f"{subject}: {_ASPECT_KOREAN[item.aspect]}이(가) 달라짐" + (
            f" ({difference})" if difference else ""
        )
        warning = WarningInterval(
            scene.scene_key, scene.planned_start_ms, scene.planned_end_ms, message
        )
        return CheckOutcome(
            self.check_type,
            Verdict.FAIL,
            score=0.0,
            warnings=(warning,),
            detail=detail,
            **fields,
        )

    def _undetermined(self, scene_key: str, reason: str) -> CheckOutcome:
        return CheckOutcome(
            self.check_type,
            Verdict.UNDETERMINED,
            scene_key=scene_key,
            detail={"reason": reason[:400], "method": "vlm_keep"},
        )


class _BadAnswer(AdapterError):
    code = "LLM_BAD_RESPONSE"


def _changed_entities(ctx: CheckContext, scene: SceneSpec) -> set[str]:
    """이 장면에서 이 수정이 바꾸는(바로잡는) 객체: 요구조건이 달라진 객체와 수정 의도의 대상."""
    changed: set[str] = set()
    before = (
        ctx.parent_ir.scene(scene.scene_key)
        if ctx.parent_ir is not None and ctx.parent_ir.has_scene(scene.scene_key)
        else None
    )
    for row in compare_requirements(before, scene):
        if row.change is not RequirementChangeKind.UNCHANGED and row.entity_key is not None:
            changed.add(row.entity_key)
    intent = ctx.version.intent
    if intent is not None and intent.entity_key is not None and intent.targets(scene.scene_key):
        changed.add(intent.entity_key)
    return changed


def _scene_level_changed(ctx: CheckContext, scene: SceneSpec) -> bool:
    """장면 수준 조건(장소·조명 등)을 바꾸는 수정인가. 그러면 배경은 기본 요소로 보지 않는다."""
    before = (
        ctx.parent_ir.scene(scene.scene_key)
        if ctx.parent_ir is not None and ctx.parent_ir.has_scene(scene.scene_key)
        else None
    )
    if any(
        row.change is not RequirementChangeKind.UNCHANGED and row.entity_key is None
        for row in compare_requirements(before, scene)
    ):
        return True
    intent = ctx.version.intent
    return intent is not None and intent.entity_key is None and intent.targets(scene.scene_key)


def _words(predicate: str) -> str:
    return predicate.replace("_", " ")


def _suffix(text: str) -> str:
    return f": {text.strip()}" if text.strip() else ""
