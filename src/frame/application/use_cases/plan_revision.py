"""US-01 변경 계획 미리보기 (ADR-0019): 수정 버전을 만들기 전에 '무엇이 어떻게 될지' 를 돌려준다. 상태는 바꾸지 않는다.

컨셉 화면의 '④ 실행' 첫 단계 '변경 계획 확인' 이다.
- 구조화된 변경(객체·술어·값)을 받거나, 글로 쓴 요청을 해석기로 구조화한다.
- 수정의 종류를 정한다: 값이 달라지면 CHANGE, 이미 그 값이면 REPAIR (다시 맞추기).
- 장면마다 다시 만드는지, 지금 값이 무엇인지, 그 요구조건의 검수 판정이 무엇이었는지 알려 준다.
- 다시 만들 장면을 지난번에 만들 때 든 비용·시간을 더해 어림값으로 준다 (기록이 있을 때만).
"""

from __future__ import annotations

from dataclasses import dataclass

from frame.domain.enums import ChangeScope, CheckType, RevisionKind, Verdict
from frame.domain.errors import NotFound, ValidationError, VersionNotRevisable
from frame.domain.revision import KeepItem
from frame.domain.services.propagation import AttributeChange, ChangePropagationPolicy
from frame.domain.services.revision_planning import resolve_revision
from frame.domain.state_machine import REVISABLE_STATES

from ..ports.change_request import ChangeRequestInterpreter
from ..ports.unit_of_work import UnitOfWorkFactory


@dataclass(frozen=True)
class PlanRevisionCommand:
    version_id: str
    # 구조화된 변경. 없으면 request_text 를 해석한다
    change: AttributeChange | None = None
    request_text: str | None = None
    entity_key: str | None = None  # 화면에서 고른 객체 (해석의 실마리)
    scope: ChangeScope | None = None  # 화면에서 고른 범위 (해석 결과보다 우선)
    scene_keys: tuple[str, ...] = ()
    kind: RevisionKind | None = None  # 없으면 스스로 정한다
    keep: tuple[KeepItem, ...] = ()  # 화면에서 고른 유지 요소 (있으면 해석 결과보다 우선)


@dataclass(frozen=True)
class ScenePlan:
    scene_key: str
    planned_start_ms: int
    planned_end_ms: int
    will_regenerate: bool
    current_value: str | None  # 지금 그 조건의 값 (조건이 없으면 None)
    verdict: (
        Verdict | None
    )  # 이 버전에서 그 요구조건의 검수 판정 (검수 전이거나 조건이 없으면 None)
    recorded_cost_usd: float | None  # 이 장면을 지난번에 만들 때의 비용·시간 (기록이 있을 때)
    recorded_seconds: float | None


@dataclass(frozen=True)
class RevisionPlan:
    version_id: str
    kind: RevisionKind
    change: AttributeChange
    keep: tuple[KeepItem, ...]
    request_text: str | None
    interpreted: bool  # request_text 를 해석해서 만든 계획인가
    notes: str | None
    previous_value: str | None
    target_scene_keys: tuple[str, ...]
    scenes: tuple[ScenePlan, ...]

    @property
    def estimated_cost_usd(self) -> float | None:
        costs = [
            s.recorded_cost_usd
            for s in self.scenes
            if s.will_regenerate and s.recorded_cost_usd is not None
        ]
        return round(sum(costs), 6) if costs else None

    @property
    def estimated_seconds(self) -> float | None:
        seconds = [
            s.recorded_seconds
            for s in self.scenes
            if s.will_regenerate and s.recorded_seconds is not None
        ]
        return round(sum(seconds), 1) if seconds else None

    @property
    def recorded_scene_count(self) -> int:
        """다시 만들 장면 가운데 지난 비용·시간 기록이 있는 장면 수 (어림값이 몇 장면을 덮는지)."""
        return sum(
            1
            for s in self.scenes
            if s.will_regenerate
            and (s.recorded_cost_usd is not None or s.recorded_seconds is not None)
        )


class PlanRevision:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        policy: ChangePropagationPolicy,
        interpreter: ChangeRequestInterpreter,
    ) -> None:
        self._uow_factory = uow_factory
        self._policy = policy
        self._interpreter = interpreter

    def execute(self, command: PlanRevisionCommand) -> RevisionPlan:
        with self._uow_factory() as uow:
            source = uow.versions.get(command.version_id)
            if source is None:
                raise NotFound(f"버전 {command.version_id} 을 찾을 수 없습니다")
            if source.state not in REVISABLE_STATES or source.ir is None:
                raise VersionNotRevisable(
                    f"상태 {source.state} 인 버전은 수정할 수 없습니다", state=source.state
                )
            checks = uow.checks.list_by_version(command.version_id)
        ir = source.ir
        text = (command.request_text or "").strip() or None
        change, keep, notes = command.change, command.keep, None
        interpreted = change is None
        if change is None:
            if text is None:
                raise ValidationError(
                    "변경할 값(predicate·new_value)이나 요청 문장(request_text) 가운데 "
                    "하나는 있어야 합니다"
                )
            reading = self._interpreter.interpret(ir, text, entity_key=command.entity_key)
            scope = command.scope or reading.scope or ChangeScope.ALL_APPEARANCES
            scene_keys = command.scene_keys or reading.scene_keys
            change = AttributeChange(
                entity_key=reading.entity_key,
                predicate=reading.predicate,
                new_value=reading.new_value,
                scope=scope,
                scene_keys=tuple(scene_keys) if scope is ChangeScope.SCENES else (),
            )
            keep = command.keep or reading.keep
            notes = reading.notes or None
        resolved = resolve_revision(ir, change, self._policy, kind=command.kind, keep=keep)

        verdicts = {
            r.requirement_key: r.verdict
            for r in checks
            if r.check_type is CheckType.REQUIREMENT and r.requirement_key is not None
        }
        targets = set(resolved.target_scene_keys)
        scenes = []
        for scene in ir.scenes:
            current = [
                r
                for r in scene.requirements
                if r.entity_key == change.entity_key and r.predicate == change.predicate
            ]
            clip = source.clips.get(scene.scene_key)
            metadata = clip.metadata if clip is not None else {}
            scenes.append(
                ScenePlan(
                    scene_key=scene.scene_key,
                    planned_start_ms=scene.planned_start_ms,
                    planned_end_ms=scene.planned_end_ms,
                    will_regenerate=scene.scene_key in targets,
                    current_value=current[0].expected_value if len(current) == 1 else None,
                    verdict=verdicts.get(current[0].key) if len(current) == 1 else None,
                    recorded_cost_usd=_number(metadata.get("cost_usd")),
                    recorded_seconds=_number(metadata.get("generation_seconds")),
                )
            )
        return RevisionPlan(
            version_id=source.version_id,
            kind=resolved.kind,
            change=change,
            keep=resolved.keep,
            request_text=text,
            interpreted=interpreted,
            notes=notes,
            previous_value=resolved.previous_value,
            target_scene_keys=resolved.target_scene_keys,
            scenes=tuple(scenes),
        )


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None
