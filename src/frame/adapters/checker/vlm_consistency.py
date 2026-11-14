"""VLM 기반 일관성 검수기 (FRAME_CHECKERS 의 vlm_consistency, US-03 AC-32·33, ADR-0015).

성공 지표 3 의 정의를 그대로 잰다: "객체가 보이는 표본 프레임 중 지정 색상·형상을 유지한 비율" (목표 90% 이상).

방법
1. 객체가 나오는 장면마다 클립에서 표본 프레임을 고르게 뽑는다 (ffmpeg, 기본 장면당 3장).
2. 객체 하나당 한 번, 모든 표본 프레임을 VLM 에 보여 주고 프레임마다 묻는다:
   보이는가 / 기준과 같은 인물·물건인가 / 지정한 고정 속성(색·의상·문구 등)을 유지하는가.
   기준은 사용자 참조 이미지가 있으면 그것, 없으면 객체가 보이는 첫 프레임이다 (첫 등장을 기준으로 삼는다).
3. 점수 = (기준과 같고 고정 속성도 유지한 프레임 수) ÷ (보이고 판정된 프레임 수). 임계값(0.9) 이상이면 PASS.
4. 어긋난 프레임이 대표하는 타임라인 구간을 경고 구간으로 남긴다 (AC-33 경고 마커).

규약
- 판정할 수 없으면 UNDETERMINED: 추적 실패, 클립이 영상이 아님, 객체가 한 번도 안 보임, VLM·ffmpeg 호출 실패.
  VLM 장애로 버전을 FAILED 로 만들지 않는다 (생성은 이미 끝났고 과금됐다).
- 행동·자세·표정·위치는 장면마다 달라지는 것이 정상이라 고정 속성으로 묻지 않는다.
- 판정 근거(프레임별 답·모델 이름)는 detail 에 남긴다. 같은 입력에도 VLM 답은 흔들릴 수 있다.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from frame.adapters.shared.media.ffmpeg import require_ffmpeg
from frame.adapters.shared.media.frames import FrameSampler, midpoints
from frame.adapters.shared.vlm.ask import ask_json
from frame.adapters.shared.vlm.wording import predicate_words
from frame.application.ports.artifact_store import ArtifactStore
from frame.application.ports.checker import CheckContext, Checker, CheckOutcome
from frame.application.ports.llm import ImageInput, LLMClient
from frame.domain.checks import WarningInterval
from frame.domain.enums import CheckType, ReferenceOrigin, TrackingStatus, Verdict
from frame.domain.ir import EntitySpec, SceneSpec
from frame.domain.services.identity import appearance_attributes

from ._guard import UnitGuard

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You audit the visual consistency of ONE subject across sampled frames of an AI-generated "
    "multi-scene video. The frames are given in timeline order, each with an id. Every person "
    "shown is a synthetic character made by a video model; this is a quality check that compares "
    "visible traits between frames, not an identification of any real person.\n"
    "For every frame answer:\n"
    "- visible: true only if the subject is shown clearly enough to judge its appearance.\n"
    "- same_subject: compare the frame with the reference. The reference is the image labelled "
    "REF if one is given; otherwise it is the first frame in which the subject is visible "
    "(report its id in reference_frame_id; that frame is the same subject by definition). "
    "Answer true only if it is evidently the same individual or the same physical object. "
    "For people compare apparent age, gender presentation, face shape, skin tone, hair color, "
    "hair length and style, facial hair, glasses, body build and clothing. For objects compare "
    "shape, proportions, color, material and printed text or markings. A different pose, "
    "movement, facial expression, camera angle, framing, lighting or background is NOT a "
    "difference. Be strict: if any stable trait clearly differs, answer false and name the "
    "differing traits in `differences` (short English phrase). Use null when the subject is not "
    "visible or too small or occluded to compare.\n"
    "- attributes: for each fixed attribute listed for that frame, kept is true if the frame "
    "shows it, false if the frame clearly contradicts it, null if it cannot be seen.\n"
    "Return one entry per frame id, in the given order. Judge only what the images show."
)

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["reference_frame_id", "frames", "summary"],
    "properties": {
        "reference_frame_id": {"type": ["string", "null"]},
        "frames": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["frame_id", "visible", "same_subject", "differences", "attributes"],
                "properties": {
                    "frame_id": {"type": "string"},
                    "visible": {"type": "boolean"},
                    "same_subject": {"type": ["boolean", "null"]},
                    "differences": {"type": "string"},
                    "attributes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["name", "kept"],
                            "properties": {
                                "name": {"type": "string"},
                                "kept": {"type": ["boolean", "null"]},
                            },
                        },
                    },
                },
            },
        },
        "summary": {"type": "string"},
    },
}


@dataclass(frozen=True)
class _Sample:
    frame_id: str
    scene: SceneSpec
    at_ms: int  # 클립 안에서의 시각
    start_ms: int  # 이 프레임이 대표하는 타임라인 구간
    end_ms: int
    image: ImageInput
    attributes: Mapping[str, str]  # 이 장면에서 객체에 지정된 고정 속성


class VlmConsistencyChecker(Checker):
    check_type = CheckType.CONSISTENCY

    def __init__(
        self,
        store: ArtifactStore,
        ffmpeg: str | None,
        llm: LLMClient,
        *,
        # 표본 프레임 수와 임계값은 설정(FRAME_*_FRAMES · FRAME_*_THRESHOLD)에서 온다 (ADR-0032)
        frames_per_scene: int,
        threshold: float,
        max_side: int = 1024,  # 480p·720p 프레임은 줄이지 않고 그대로 보여 준다 (얼굴·문구 세부)
        timeout_s: float | None = 180.0,
        sampler: FrameSampler | None = None,
    ) -> None:
        self._store = store
        self._sampler = sampler or FrameSampler.shared(require_ffmpeg(ffmpeg))
        self._llm = llm
        self.frames_per_scene = max(1, int(frames_per_scene))
        self.threshold = threshold
        self.max_side = max_side
        self.timeout_s = timeout_s

    def check(self, ctx: CheckContext) -> Sequence[CheckOutcome]:
        outcomes = []
        guard = UnitGuard("일관성 검수")
        for entity in ctx.ir.entities:
            scenes = [
                s for s in ctx.ir.scenes_with_entity(entity.entity_key) if s.scene_key in ctx.clips
            ]
            if not scenes:
                continue
            outcomes.append(self._check_entity(ctx, entity, scenes, guard))
        return outcomes

    # ---- 객체 하나 ----
    def _check_entity(
        self,
        ctx: CheckContext,
        entity: EntitySpec,
        scenes: Sequence[SceneSpec],
        guard: UnitGuard,
    ) -> CheckOutcome:
        """객체 하나. 판정하지 못하면 (프레임·모델·집계의 예상 밖 오류) 이 객체만 미판정으로 남긴다."""
        return guard.judge(
            f"객체 {entity.entity_key}",
            lambda: self._judge_entity(ctx, entity, scenes, guard),
            undetermined=lambda reason: self._undetermined(entity, reason),
        )

    def _judge_entity(
        self,
        ctx: CheckContext,
        entity: EntitySpec,
        scenes: Sequence[SceneSpec],
        guard: UnitGuard,
    ) -> CheckOutcome:
        tracks = [t for t in ctx.tracks if t.entity_key == entity.entity_key]
        if any(t.status is not TrackingStatus.COMPLETED for t in tracks):
            return self._undetermined(entity, "추적 실패 구간이 있어 판정할 수 없음")
        not_video = [
            s.scene_key
            for s in scenes
            if not self._store.content_type(ctx.clips[s.scene_key].uri).startswith("video/")
        ]
        if not_video:
            return self._undetermined(
                entity, f"장면 클립이 영상이 아니라 프레임을 뽑을 수 없음: {', '.join(not_video)}"
            )
        guard.about_to_ask()
        samples = self._samples(ctx, entity, scenes)
        reference = self._user_reference(ctx, entity)
        answer = self._ask(entity, samples, reference)
        return self._outcome(entity, samples, answer, user_reference=reference is not None)

    def _samples(
        self, ctx: CheckContext, entity: EntitySpec, scenes: Sequence[SceneSpec]
    ) -> list[_Sample]:
        samples = []
        count = self.frames_per_scene
        for scene in scenes:
            clip = ctx.clips[scene.scene_key]
            video = self._store.get(clip.uri)
            duration = clip.duration_ms or scene.duration_ms
            attributes = appearance_attributes(scene, entity.entity_key)
            frames = self._sampler.clip(video)
            for index, at_ms in enumerate(midpoints(duration, count)):  # 구간마다의 가운데
                frame = frames.at(at_ms, max_side=self.max_side)
                frame_id = f"{scene.scene_key}#{index + 1}"
                expected = "; ".join(f"{predicate_words(p)}: {v}" for p, v in attributes.items())
                label = f"Frame {frame_id} — scene '{scene.scene_key}' at {at_ms / 1000:.1f}s"
                if expected:
                    label += f". Fixed attributes expected here: {expected}"
                samples.append(
                    _Sample(
                        frame_id=frame_id,
                        scene=scene,
                        at_ms=at_ms,
                        start_ms=scene.planned_start_ms + scene.duration_ms * index // count,
                        end_ms=scene.planned_start_ms + scene.duration_ms * (index + 1) // count,
                        image=ImageInput(frame.data, frame.media_type, label),
                        attributes=attributes,
                    )
                )
        return samples

    def _user_reference(self, ctx: CheckContext, entity: EntitySpec) -> ImageInput | None:
        """사용자가 그 객체에 준 참조 이미지 (저장소에서 읽을 수 있는 이미지일 때만)."""
        for ref in ctx.references:
            if ref.origin is not ReferenceOrigin.USER or ref.entity_key != entity.entity_key:
                continue
            if not self._store.exists(ref.image_uri):
                continue
            media_type = self._store.content_type(ref.image_uri)
            if media_type.startswith("image/"):
                label = "REF — reference image of the subject provided by the user"
                return ImageInput(self._store.get(ref.image_uri), media_type, label)
        return None

    def _ask(
        self, entity: EntitySpec, samples: Sequence[_Sample], reference: ImageInput | None
    ) -> dict[str, Any]:
        lines = [
            "Subject to audit:",
            f"- {entity.entity_key} ({str(entity.kind).lower()})"
            + (f": {entity.description.strip()}" if entity.description.strip() else ""),
            "Frame ids in timeline order: " + ", ".join(s.frame_id for s in samples),
        ]
        fixed = {s.scene.scene_key: s.attributes for s in samples if s.attributes}
        if fixed:
            lines.append("Fixed attributes the subject must keep, per scene:")
            lines += [
                f"- {scene_key}: "
                + "; ".join(f"{predicate_words(p)}: {v}" for p, v in attributes.items())
                for scene_key, attributes in fixed.items()
            ]
        if reference is not None:
            lines.append("The first image (REF) is the reference. Compare every frame with it.")
        else:
            lines.append(
                "No separate reference image is given: use the first frame in which the subject "
                "is visible as the reference."
            )
        images = ([reference] if reference is not None else []) + [s.image for s in samples]
        answer = ask_json(
            self._llm,
            SYSTEM_PROMPT,
            "\n".join(lines),
            RESPONSE_SCHEMA,
            schema_name="consistency_audit",
            timeout_s=self.timeout_s,
            images=images,
            read=_read_answer,
            what="일관성 판정",
        )
        return {
            **answer.value,
            "model": answer.model,
            "usage": answer.usage,
        }

    # ---- 점수와 경고 ----
    def _outcome(
        self,
        entity: EntitySpec,
        samples: Sequence[_Sample],
        answer: Mapping[str, Any],
        *,
        user_reference: bool,
    ) -> CheckOutcome:
        frames: Mapping[str, Mapping[str, Any]] = answer["frames"]
        reference_id = None if user_reference else _first_visible(samples, frames)
        rows: list[dict[str, Any]] = []
        judged = same_count = kept_count = consistent = 0
        warnings: list[WarningInterval] = []
        for sample in samples:
            reply = frames.get(sample.frame_id) or {}
            visible = reply.get("visible") is True
            same = reply.get("same_subject")
            if sample.frame_id == reference_id:
                same = True  # 기준 프레임은 정의상 같은 객체다
            dropped = [
                str(a.get("name"))
                for a in reply.get("attributes") or ()
                if isinstance(a, dict) and a.get("kept") is False
            ]
            row = {
                "frame_id": sample.frame_id,
                "scene_key": sample.scene.scene_key,
                "t_ms": sample.scene.planned_start_ms + sample.at_ms,
                "visible": visible,
                "same_subject": same if visible else None,
                "dropped_attributes": dropped,
                "differences": str(reply.get("differences") or "")[:200],
            }
            rows.append(row)
            if not visible or not isinstance(same, bool):
                continue  # 안 보이거나 비교할 수 없는 프레임은 분모에 넣지 않는다
            judged += 1
            same_count += same
            kept_count += not dropped
            if same and not dropped:
                consistent += 1
                continue
            warnings.append(
                WarningInterval(
                    scene_key=sample.scene.scene_key,
                    start_ms=sample.start_ms,
                    end_ms=sample.end_ms,
                    message=_warning_message(entity, row),
                )
            )
        detail: dict[str, Any] = {
            "method": "vlm",
            "model": answer.get("model"),
            "usage": answer.get("usage"),  # VLM 호출 한 번의 토큰 수 (비용 추적)
            "reference": "user_reference" if user_reference else "first_visible_frame",
            "reference_frame_id": reference_id,
            "sampled_frames": len(samples),
            "judged_frames": judged,
            "consistent_frames": consistent,
            "threshold": self.threshold,
            "frames": rows,
            "summary": str(answer.get("summary") or "")[:400],
        }
        if judged == 0:
            detail["reason"] = "객체가 보이는 표본 프레임이 없음"
            return CheckOutcome(
                self.check_type, Verdict.UNDETERMINED, entity_key=entity.entity_key, detail=detail
            )
        score = consistent / judged
        detail["identity_rate"] = same_count / judged
        detail["attribute_rate"] = kept_count / judged
        return CheckOutcome(
            self.check_type,
            Verdict.PASS if score >= self.threshold else Verdict.FAIL,
            score=score,
            entity_key=entity.entity_key,
            warnings=tuple(_merge(warnings)),
            detail=detail,
        )

    def _undetermined(self, entity: EntitySpec, reason: str) -> CheckOutcome:
        return CheckOutcome(
            self.check_type,
            Verdict.UNDETERMINED,
            entity_key=entity.entity_key,
            detail={"method": "vlm", "reason": reason[:400]},
        )


def _read_answer(data: Any) -> dict[str, Any]:
    """응답 본문에서 쓸 것: 프레임마다의 판정(frame_id -> 항목), 기준 프레임, 요약."""
    return {
        "frames": {str(f["frame_id"]): f for f in data["frames"] if isinstance(f, dict)},
        "reference_frame_id": data.get("reference_frame_id"),
        "summary": data.get("summary") or "",
    }


def _first_visible(
    samples: Sequence[_Sample], frames: Mapping[str, Mapping[str, Any]]
) -> str | None:
    for sample in samples:
        if (frames.get(sample.frame_id) or {}).get("visible") is True:
            return sample.frame_id
    return None


def _warning_message(entity: EntitySpec, row: Mapping[str, Any]) -> str:
    parts = []
    if row["same_subject"] is False:
        parts.append(
            "기준과 다른 모습" + (f" ({row['differences']})" if row["differences"] else "")
        )
    if row["dropped_attributes"]:
        parts.append("고정 속성 이탈: " + ", ".join(row["dropped_attributes"]))
    return f"{entity.entity_key}: " + "; ".join(parts)


def _merge(warnings: Sequence[WarningInterval]) -> list[WarningInterval]:
    """같은 장면에서 이어지는 구간은 하나로 합친다 (첫 구간의 설명을 쓴다)."""
    merged: list[WarningInterval] = []
    for warning in warnings:
        last = merged[-1] if merged else None
        if last and last.scene_key == warning.scene_key and last.end_ms == warning.start_ms:
            merged[-1] = WarningInterval(
                last.scene_key, last.start_ms, warning.end_ms, last.message
            )
        else:
            merged.append(warning)
    return merged
