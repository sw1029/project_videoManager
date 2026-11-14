"""편의 계획과 그 재료 (ADR-0026).

- EpisodeBrief   편을 요청하는 쪽이 주는 것 (제목·내용·변수 값·seed)
- EpisodeContent 기획(모델 또는 사람)이 내는 것: 편마다 다른 것만 (자리별 장면 설명·객체·조건·말할 문장)
- EpisodePlan    둘을 테마와 합친 결과: IR · 녹음 대본(ScriptLine) · 화면 문구(OverlayItem) · 변수 · 메모
화면 문구는 IR 에 넣지 않는다. 생성 조건이 아니라 후반 합성 계획이다 (ADR-0027).
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

from frame.domain.errors import NotFound, ValidationError
from frame.domain.ir import EntitySpec, IntermediateRepresentation, RequirementSpec
from frame.domain.screen import Region, TextRole
from frame.domain.series.theme import TextStyle

# 문구 규칙에 자리가 없을 때 구실에 따라 두는 기본 자리. 아래쪽 20% 와 오른쪽 가장자리는 플랫폼의 버튼·설명이
# 덮는 곳이라 피한다.
DEFAULT_REGIONS: Mapping[TextRole, Region] = {
    TextRole.TITLE: Region(0.08, 0.08, 0.84, 0.09),
    TextRole.CAPTION: Region(0.08, 0.68, 0.84, 0.08),
    TextRole.LABEL: Region(0.08, 0.58, 0.84, 0.07),
    TextRole.PRICE: Region(0.2, 0.6, 0.6, 0.08),
    TextRole.CALL_TO_ACTION: Region(0.08, 0.7, 0.84, 0.08),
    TextRole.WATERMARK: Region(0.05, 0.03, 0.4, 0.04),
    TextRole.OTHER: Region(0.08, 0.68, 0.84, 0.08),
}


class OverlayKind(StrEnum):
    TEXT = "TEXT"  # 테마의 문구 규칙에서 온 화면 문구
    PATCH = "PATCH"  # 영상 안의 틀린 글자를 가리고 다시 쓴 것 (국소 수정이 더한다)


@dataclass(frozen=True)
class PlanPolicy:
    lead_in_ms: int = 200  # 장면이 시작하고 말이 나오기까지의 여유
    tail_ms: int = 200  # 말이 끝나고 장면이 바뀌기까지의 여유
    # 테마에 말 빠르기가 없을 때 대본 길이를 어림하는 값 (초당 글자 수). 낭독 속도 근처의 어림값이다
    default_pace_cps: float = 5.5


DEFAULT_POLICY = PlanPolicy()


@dataclass(frozen=True)
class EpisodeBrief:
    """이번 편에 대한 요청: 제목, 내용, 테마 변수의 값. 여기 준 변수의 값이 기준이다."""

    title: str
    topic: str = ""
    variables: Mapping[str, str] = field(default_factory=dict)
    seed: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "topic": self.topic,
            "variables": dict(self.variables),
            "seed": self.seed,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EpisodeBrief:
        return cls(
            title=str(data["title"]),
            topic=str(data.get("topic", "")),
            variables={str(k): str(v) for k, v in (data.get("variables") or {}).items()},
            seed=data.get("seed"),
        )


@dataclass(frozen=True)
class SlotContent:
    """자리 하나에 이번 편이 채우는 내용."""

    slot_key: str
    description: str = ""  # 장면 설명. 비우면 자리의 구실을 쓴다
    entity_keys: tuple[str, ...] = ()  # 이 장면에 더 나오는 객체 (편의 객체나 다른 고정 출연)
    requirements: tuple[RequirementSpec, ...] = ()  # 이 편만의 조건
    narration: str = ""  # 이 장면에서 말할 문장
    variables: Mapping[str, str] = field(default_factory=dict)  # 이 자리의 문구 값 (자리별 자막 등)


@dataclass(frozen=True)
class EpisodeContent:
    """편마다 다른 내용 전체. 기획이 내고 build_episode_plan 이 테마와 합친다."""

    slots: tuple[SlotContent, ...]
    entities: tuple[EntitySpec, ...] = ()  # 이 편에만 나오는 객체
    variables: Mapping[str, str] = field(
        default_factory=dict
    )  # 기획이 채운 변수 (요청이 준 값이 우선)


@dataclass(frozen=True)
class ScriptLine:
    """녹음 대본 한 줄: 어느 장면에서, 무엇을, 얼마 안에 말하는가."""

    line_key: str
    scene_key: str
    text: str
    budget_ms: int  # 그 장면에서 말할 수 있는 시간
    estimated_ms: int | None = None  # 말 빠르기로 어림한 길이

    @property
    def fits(self) -> bool | None:
        return None if self.estimated_ms is None else self.estimated_ms <= self.budget_ms

    def to_dict(self) -> dict[str, Any]:
        return {
            "line_key": self.line_key,
            "scene_key": self.scene_key,
            "text": self.text,
            "budget_ms": self.budget_ms,
            "estimated_ms": self.estimated_ms,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ScriptLine:
        estimated = data.get("estimated_ms")
        return cls(
            line_key=str(data["line_key"]),
            scene_key=str(data["scene_key"]),
            text=str(data["text"]),
            budget_ms=int(data["budget_ms"]),
            estimated_ms=int(estimated) if estimated is not None else None,
        )


@dataclass(frozen=True)
class OverlayItem:
    """화면 문구 하나: 어느 장면의 언제(장면 안 시각), 어디에, 어떤 모양으로 무엇을 쓰는가."""

    overlay_id: str
    scene_key: str
    text: str
    start_ms: int
    end_ms: int
    region: Region
    style: TextStyle = field(default_factory=TextStyle)
    role: TextRole = TextRole.OTHER
    rule_key: str | None = None  # 테마의 어느 문구 규칙에서 왔는가
    kind: OverlayKind = OverlayKind.TEXT

    def validate(self, scene_duration_ms: int) -> None:
        if not self.text.strip():
            raise ValidationError(f"화면 문구 '{self.overlay_id}' 의 내용이 비어 있습니다")
        if self.start_ms < 0 or self.end_ms <= self.start_ms or self.end_ms > scene_duration_ms:
            raise ValidationError(
                f"화면 문구 '{self.overlay_id}' 의 시간 {self.start_ms}~{self.end_ms} ms 가 "
                f"장면 '{self.scene_key}' ({scene_duration_ms} ms) 를 벗어납니다"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "overlay_id": self.overlay_id,
            "scene_key": self.scene_key,
            "text": self.text,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "region": self.region.to_dict(),
            "style": self.style.to_dict(),
            "role": str(self.role),
            "rule_key": self.rule_key,
            "kind": str(self.kind),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> OverlayItem:
        return cls(
            overlay_id=str(data["overlay_id"]),
            scene_key=str(data["scene_key"]),
            text=str(data["text"]),
            start_ms=int(data["start_ms"]),
            end_ms=int(data["end_ms"]),
            region=Region.from_dict(data["region"]),
            style=TextStyle.from_dict(data.get("style")),
            role=TextRole(data.get("role", TextRole.OTHER)),
            rule_key=data.get("rule_key"),
            kind=OverlayKind(data.get("kind", OverlayKind.TEXT)),
        )


@dataclass(frozen=True)
class EpisodePlan:
    """편 하나의 계획: 생성 조건(IR) + 녹음 대본 + 화면 문구."""

    title: str
    ir: IntermediateRepresentation
    script: tuple[ScriptLine, ...] = ()
    overlays: tuple[OverlayItem, ...] = ()
    variables: Mapping[str, str] = field(default_factory=dict)  # 편 단위 변수의 값
    notes: tuple[str, ...] = ()  # 계획을 만들며 고치거나 버린 것
    seed: int | None = None

    def overlays_for(self, scene_key: str) -> tuple[OverlayItem, ...]:
        return tuple(o for o in self.overlays if o.scene_key == scene_key)

    def overlay(self, overlay_id: str) -> OverlayItem:
        for item in self.overlays:
            if item.overlay_id == overlay_id:
                return item
        raise NotFound(f"화면 문구 '{overlay_id}' 가 계획에 없습니다", overlay_id=overlay_id)

    def line(self, line_key: str) -> ScriptLine:
        for line in self.script:
            if line.line_key == line_key:
                return line
        raise NotFound(f"대본 줄 '{line_key}' 이 계획에 없습니다", line_key=line_key)

    def with_overlay(self, item: OverlayItem) -> EpisodePlan:
        """같은 id 의 문구를 바꾸거나, 없으면 더한 새 계획. 장면과 시간이 맞아야 한다."""
        item.validate(self.ir.scene(item.scene_key).duration_ms)
        if any(o.overlay_id == item.overlay_id for o in self.overlays):
            overlays = tuple(item if o.overlay_id == item.overlay_id else o for o in self.overlays)
        else:
            overlays = (*self.overlays, item)
        return replace(self, overlays=overlays)

    def without_overlay(self, overlay_id: str) -> EpisodePlan:
        self.overlay(overlay_id)  # 없으면 NotFound
        return replace(self, overlays=tuple(o for o in self.overlays if o.overlay_id != overlay_id))

    def without_patches_of(self, scene_keys: Collection[str]) -> EpisodePlan:
        """다시 만든 장면의 가린 문구(PATCH)를 뺀 새 계획. 뺀 것은 메모로 남긴다.

        가린 문구는 그 클립의 그 자리를 가린 것이다. 클립이 바뀌었는데 남겨 두면 새 클립 위에 옛 글자를 덮고,
        검수는 그 문구가 제 글자로 읽히므로 통과시킨다 (감사 BTS-D01). 문구 틀에서 온 문구는 그대로 둔다.
        """
        stale = tuple(
            o for o in self.overlays if o.kind is OverlayKind.PATCH and o.scene_key in scene_keys
        )
        if not stale:
            return self
        notes = tuple(
            f"장면 '{o.scene_key}' 을 다시 만들어 가린 문구 '{o.overlay_id}' ('{o.text}') 를 뺐습니다. "
            "새 클립의 글자는 검수에서 다시 확인하세요"
            for o in stale
        )
        kept = tuple(o for o in self.overlays if o not in stale)
        return replace(self, overlays=kept, notes=(*self.notes, *notes))

    def validate(self) -> None:
        self.ir.validate()
        durations = {scene.scene_key: scene.duration_ms for scene in self.ir.scenes}
        ids = [o.overlay_id for o in self.overlays]
        if len(ids) != len(set(ids)):
            raise ValidationError(f"화면 문구의 id 가 중복됩니다: {ids}")
        for item in self.overlays:
            if item.scene_key not in durations:
                raise ValidationError(
                    f"화면 문구 '{item.overlay_id}' 의 장면 '{item.scene_key}' 이 편에 없습니다"
                )
            item.validate(durations[item.scene_key])
        keys = [line.line_key for line in self.script]
        if len(keys) != len(set(keys)):
            raise ValidationError(f"대본 줄의 키가 중복됩니다: {keys}")
        for line in self.script:
            if line.scene_key not in durations:
                raise ValidationError(
                    f"대본 줄 '{line.line_key}' 의 장면 '{line.scene_key}' 이 편에 없습니다"
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "ir": self.ir.to_dict(),
            "script": [line.to_dict() for line in self.script],
            "overlays": [item.to_dict() for item in self.overlays],
            "variables": dict(self.variables),
            "notes": list(self.notes),
            "seed": self.seed,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EpisodePlan:
        return cls(
            title=str(data["title"]),
            ir=IntermediateRepresentation.from_dict(data["ir"]),
            script=tuple(ScriptLine.from_dict(line) for line in data.get("script", ())),
            overlays=tuple(OverlayItem.from_dict(item) for item in data.get("overlays", ())),
            variables={str(k): str(v) for k, v in (data.get("variables") or {}).items()},
            notes=tuple(str(n) for n in data.get("notes", ())),
            seed=data.get("seed"),
        )
