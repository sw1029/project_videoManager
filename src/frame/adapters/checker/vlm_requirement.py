"""VLM 기반 요구사항 반영 검수기 (FRAME_CHECKERS 의 vlm_requirement, US-06·US-03, ADR-0018).

성공 지표 2 를 실제 영상으로 잰다: "충족한 요구사항 수 ÷ 전체 요구사항 수" (목표 80% 이상).
Fake 검수기는 클립을 보지 않아 항상 PASS 였다. 컨셉 화면의 '컵 색상 불일치' 경고가 이 검수의 FAIL 이다.

방법
1. 장면마다 클립에서 표본 프레임을 고르게 뽑는다 (ffmpeg, 기본 3장).
2. 장면 하나당 VLM 을 한 번 부른다. 그 장면의 요구조건마다 프레임별로 yes / no / unclear 를 받는다.
3. 요구조건의 종류에 따라 판정한다.
   - 고정 속성·장면 조건(색·의상·문구·장소·조명 등): 판정된 프레임 가운데 yes 의 비율이 임계값(기본 0.5) 이상이면 PASS.
   - 행동·자세·표정·위치와 관계(들고 있다 등): 한 프레임이라도 yes 면 PASS (장면 안에서 한 번 보이면 된다).
   - 판정된 프레임이 없으면(모두 unclear) UNDETERMINED.
4. FAIL 이면 어긋난 구간을 경고 구간으로 남기고, 메시지에 요구한 값과 화면에서 본 것을 적는다.

규약
- 다시 만들지 않은 장면(부모 클립 재사용)은 부모 버전의 판정을 물려받는다. 같은 클립을 다시 물으면 답이 흔들려
  바뀌지 않은 장면의 판정이 버전마다 달라질 수 있고 호출 비용도 든다. 부모에 판정이 없으면 새로 판정한다.
- 판정할 수 없으면 UNDETERMINED: 클립이 영상이 아님, VLM·ffmpeg 호출 실패, 응답에 그 요구조건이 없음.
  VLM 장애로 버전을 FAILED 로 만들지 않는다.
- 요구조건은 R1, R2 … 프레임은 F1, F2 … 로 짧게 가리킨다 (모델이 긴 키를 그대로 돌려주지 않을 때가 있다).
- 판정 근거(프레임별 답·화면에서 본 것·모델 이름)는 detail 에 남긴다. 토큰 수는 장면의 첫 요구조건에만 적는다
  (장면당 한 번 부른 호출이다).
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
from frame.domain.enums import CheckType, Verdict
from frame.domain.ir import RequirementSpec, SceneSpec
from frame.domain.vocabulary import DEFAULT_VOCABULARY

log = logging.getLogger(__name__)

VERDICTS = ("yes", "no", "unclear")
RULE_SHARE = "share"  # 판정된 프레임 가운데 yes 의 비율
RULE_ANY = "any"  # 한 프레임이라도 yes

SYSTEM_PROMPT = (
    "You verify whether one scene of an AI-generated video shows what was required. You get a few "
    "frames of the scene in time order (F1, F2, ...) and a numbered list of requirements "
    "(R1, R2, ...). Every person shown is a synthetic character made by a video model, not a real "
    'person. For each requirement give one verdict per frame, in frame order: "yes" if that '
    'frame shows it; "no" if that frame clearly shows something else or the opposite (for '
    'example a red object where teal is required); "unclear" if it cannot be judged from that '
    "frame (the subject is not visible, too small or hidden, or the requirement is about motion "
    "that a single frame cannot show). Judge only what the images show. A subject's name or "
    "description may carry older wording, so rely on the requirement itself. In `observed` say "
    "in a few English words what the frames actually show for that requirement (for example "
    "'red ceramic mug'). Return every requirement id exactly once, with exactly one verdict per "
    "frame."
)

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["requirements"],
    "properties": {
        "requirements": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "verdicts", "observed"],
                "properties": {
                    "id": {"type": "string"},
                    "verdicts": {
                        "type": "array",
                        "items": {"type": "string", "enum": list(VERDICTS)},
                    },
                    "observed": {"type": "string"},
                },
            },
        }
    },
}


@dataclass(frozen=True)
class _Frame:
    frame_id: str
    at_ms: int  # 클립 안에서의 시각
    start_ms: int  # 이 프레임이 대표하는 타임라인 구간
    end_ms: int
    image: ImageInput


class VlmRequirementChecker(Checker):
    check_type = CheckType.REQUIREMENT

    def __init__(
        self,
        store: ArtifactStore,
        ffmpeg: str | None,
        llm: LLMClient,
        *,
        frames_per_scene: int = 3,
        threshold: float = 0.5,
        max_side: int = 1024,
        timeout_s: float | None = 180.0,
    ) -> None:
        self._store = store
        self._ffmpeg = require_ffmpeg(ffmpeg)
        self._llm = llm
        self.frames_per_scene = max(1, int(frames_per_scene))
        self.threshold = threshold  # 고정 속성·장면 조건: 판정된 프레임 가운데 yes 의 비율 하한
        self.max_side = max_side
        self.timeout_s = timeout_s

    def check(self, ctx: CheckContext) -> Sequence[CheckOutcome]:
        outcomes: list[CheckOutcome] = []
        for scene in ctx.ir.scenes:
            if not scene.requirements or scene.scene_key not in ctx.clips:
                continue
            inherited = self._inherited(ctx, scene)
            outcomes.extend(inherited if inherited is not None else self._check_scene(ctx, scene))
        return outcomes

    # ---- 부모 판정 물려받기 ----
    def _inherited(self, ctx: CheckContext, scene: SceneSpec) -> list[CheckOutcome] | None:
        """부모 클립을 그대로 쓴 장면: 부모 버전의 판정이 요구조건마다 다 있으면 그것을 그대로 쓴다."""
        if scene.scene_key not in ctx.reused_scene_keys or ctx.parent is None:
            return None
        previous = {
            r.requirement_key: r
            for r in ctx.parent_checks
            if r.check_type is CheckType.REQUIREMENT and r.requirement_key is not None
        }
        if any(r.key not in previous for r in scene.requirements):
            return None
        outcomes = []
        for requirement in scene.requirements:
            result = previous[requirement.key]
            detail = {k: v for k, v in result.detail.items() if k != "usage"}
            detail["inherited_from"] = result.detail.get("inherited_from") or ctx.parent.version_id
            outcomes.append(
                CheckOutcome(
                    self.check_type,
                    result.verdict,
                    score=result.score,
                    requirement_key=requirement.key,
                    entity_key=requirement.entity_key,
                    scene_key=scene.scene_key,
                    warnings=tuple(result.warnings),
                    detail=detail,
                )
            )
        return outcomes

    # ---- 장면 하나 ----
    def _check_scene(self, ctx: CheckContext, scene: SceneSpec) -> list[CheckOutcome]:
        clip = ctx.clips[scene.scene_key]
        if not self._store.content_type(clip.uri).startswith("video/"):
            return self._undetermined(scene, "장면 클립이 영상이 아니라 프레임을 뽑을 수 없음")
        ids = {f"R{n}": r for n, r in enumerate(scene.requirements, start=1)}
        try:
            frames = self._frames(scene, self._store.get(clip.uri), clip.duration_ms)
            answer = self._ask(ctx, scene, ids, frames)
        except AdapterError as exc:
            log.warning("요구 반영 검수 미판정 (장면 %s): %s: %s", scene.scene_key, exc.code, exc)
            return self._undetermined(scene, f"{exc.code}: {exc}")
        outcomes = []
        for index, (rid, requirement) in enumerate(ids.items()):
            reply = answer["requirements"].get(rid)
            shared = {"method": "vlm", "model": answer["model"]}
            if index == 0:
                shared["usage"] = answer["usage"]  # 장면당 한 번 부른 호출의 토큰 수
            outcomes.append(self._outcome(scene, requirement, frames, reply, shared))
        return outcomes

    def _frames(self, scene: SceneSpec, video: bytes, clip_duration_ms: int) -> list[_Frame]:
        count = self.frames_per_scene
        duration = clip_duration_ms or scene.duration_ms
        frames = []
        for index in range(count):
            at_ms = int(duration * (index + 0.5) / count)  # 구간의 가운데
            image = extract_frame(self._ffmpeg, video, at_ms, max_side=self.max_side)
            frame_id = f"F{index + 1}"
            frames.append(
                _Frame(
                    frame_id=frame_id,
                    at_ms=at_ms,
                    start_ms=scene.planned_start_ms + scene.duration_ms * index // count,
                    end_ms=scene.planned_start_ms + scene.duration_ms * (index + 1) // count,
                    image=ImageInput(
                        image.data, image.media_type, f"{frame_id} — {at_ms / 1000:.1f}s"
                    ),
                )
            )
        return frames

    def _ask(
        self,
        ctx: CheckContext,
        scene: SceneSpec,
        ids: Mapping[str, RequirementSpec],
        frames: Sequence[_Frame],
    ) -> dict[str, Any]:
        lines = [f"Scene '{scene.scene_key}'" + _suffix(scene.description)]
        if scene.entity_keys:
            lines.append("Subjects in this scene:")
            for key in scene.entity_keys:
                entity = ctx.ir.entity(key)
                lines.append(f"- {key} ({str(entity.kind).lower()})" + _suffix(entity.description))
        lines.append(
            "Frames in time order: "
            + ", ".join(f"{f.frame_id} ({f.at_ms / 1000:.1f}s)" for f in frames)
        )
        lines.append("Requirements:")
        lines += [f"{rid}. {_statement(requirement)}" for rid, requirement in ids.items()]
        completion = self._llm.complete_json(
            SYSTEM_PROMPT,
            "\n".join(lines),
            RESPONSE_SCHEMA,
            schema_name="requirement_audit",
            timeout_s=self.timeout_s,
            images=[f.image for f in frames],
        )
        try:
            items = list(json.loads(completion.text)["requirements"])
        except (ValueError, KeyError, TypeError) as exc:
            raise _BadAnswer(f"요구 반영 판정 응답을 해석하지 못했습니다: {exc}") from exc
        return {
            "requirements": match_answers(items, list(ids), field="id"),
            "model": completion.model,
            "usage": {
                "input_tokens": completion.input_tokens,
                "output_tokens": completion.output_tokens,
            },
        }

    # ---- 판정 ----
    def _outcome(
        self,
        scene: SceneSpec,
        requirement: RequirementSpec,
        frames: Sequence[_Frame],
        reply: Mapping[str, Any] | None,
        shared: Mapping[str, Any],
    ) -> CheckOutcome:
        rule = _rule(requirement)
        detail: dict[str, Any] = {
            **shared,
            "rule": rule,
            "predicate": requirement.predicate,
            "expected_value": requirement.expected_value,
        }
        if rule == RULE_SHARE:
            detail["threshold"] = self.threshold
        fields = {
            "requirement_key": requirement.key,
            "entity_key": requirement.entity_key,
            "scene_key": scene.scene_key,
        }
        if reply is None:
            detail["reason"] = "응답에 이 요구조건의 판정이 없음"
            return CheckOutcome(self.check_type, Verdict.UNDETERMINED, detail=detail, **fields)
        raw = reply.get("verdicts")
        given = [str(v).strip().lower() for v in raw] if isinstance(raw, list) else []
        verdicts = [
            given[i] if i < len(given) and given[i] in VERDICTS else "unclear"
            for i in range(len(frames))
        ]
        observed = " ".join(str(reply.get("observed") or "").split())[:200]
        detail["observed"] = observed
        detail["frames"] = [
            {"frame_id": f.frame_id, "t_ms": scene.planned_start_ms + f.at_ms, "verdict": v}
            for f, v in zip(frames, verdicts, strict=True)
        ]
        yes, no = verdicts.count("yes"), verdicts.count("no")
        if yes + no == 0:
            detail["reason"] = "판정할 수 있는 프레임이 없음"
            return CheckOutcome(self.check_type, Verdict.UNDETERMINED, detail=detail, **fields)
        if rule == RULE_ANY:
            score = 1.0 if yes else 0.0
            passed = yes > 0
        else:
            score = yes / (yes + no)
            passed = score >= self.threshold
        warnings: tuple[WarningInterval, ...] = ()
        if not passed:
            message = _warning_message(requirement, observed)
            if rule == RULE_ANY:
                spans = [(scene.planned_start_ms, scene.planned_end_ms)]
            else:
                spans = _merged(
                    [
                        (f.start_ms, f.end_ms)
                        for f, v in zip(frames, verdicts, strict=True)
                        if v == "no"
                    ]
                )
            warnings = tuple(
                WarningInterval(scene.scene_key, start, end, message) for start, end in spans
            )
        return CheckOutcome(
            self.check_type,
            Verdict.PASS if passed else Verdict.FAIL,
            score=score,
            warnings=warnings,
            detail=detail,
            **fields,
        )

    def _undetermined(self, scene: SceneSpec, reason: str) -> list[CheckOutcome]:
        return [
            CheckOutcome(
                self.check_type,
                Verdict.UNDETERMINED,
                requirement_key=r.key,
                entity_key=r.entity_key,
                scene_key=scene.scene_key,
                detail={"method": "vlm", "reason": reason[:400]},
            )
            for r in scene.requirements
        ]


class _BadAnswer(AdapterError):
    code = "LLM_BAD_RESPONSE"


def _rule(requirement: RequirementSpec) -> str:
    """행동·자세 같은 일시적 조건과 관계는 장면 안에서 한 번 보이면 된다. 나머지는 프레임의 과반이 지켜야 한다."""
    if requirement.is_relational or DEFAULT_VOCABULARY.is_transient(requirement.predicate):
        return RULE_ANY
    return RULE_SHARE


def _words(predicate: str) -> str:
    return predicate.replace("_", " ")


def _suffix(text: str) -> str:
    return f": {text.strip()}" if text.strip() else ""


def _statement(requirement: RequirementSpec) -> str:
    """요구조건 한 줄: 'cup — color: teal', 'barista — holds cup', 'the scene — lighting: warm'."""
    subject = requirement.entity_key or "the scene"
    if requirement.related_entity_key is not None:
        return f"{subject} — {_words(requirement.predicate)} {requirement.related_entity_key}"
    return f"{subject} — {_words(requirement.predicate)}: {requirement.expected_value}"


def _warning_message(requirement: RequirementSpec, observed: str) -> str:
    subject = requirement.entity_key or "장면"
    if requirement.related_entity_key is not None:
        text = (
            f"{subject} {_words(requirement.predicate)} {requirement.related_entity_key}: "
            "요구한 관계가 보이지 않음"
        )
    else:
        text = f"{subject} {_words(requirement.predicate)}: 요구 '{requirement.expected_value}'"
    return text + (f", 화면: {observed}" if observed else "")


def _merged(spans: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    """이어지는 구간은 하나로 합친다."""
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and merged[-1][1] == start:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged
