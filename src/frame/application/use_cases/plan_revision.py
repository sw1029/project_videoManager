"""US-01 변경 계획 미리보기 (ADR-0019): 수정 버전을 만들기 전에 '무엇이 어떻게 될지' 를 돌려준다. 상태는 바꾸지 않는다.

컨셉 화면의 '④ 실행' 첫 단계 '변경 계획 확인' 이다.
- 구조화된 변경(객체·술어·값)을 받거나, 글로 쓴 요청을 해석기로 구조화한다.
- 수정의 종류를 정한다: 값이 달라지면 CHANGE, 이미 그 값이면 REPAIR (다시 맞추기).
- 장면마다 다시 만드는지, 지금 값이 무엇인지, 그 요구조건의 검수 판정이 무엇이었는지 알려 준다.
- 다시 만들 장면마다 생성기에게 '지금 만들면 어떻게 만들어질지' 를 묻는다 (ADR-0020): 새로 생성할지 부모 클립을
  편집할지, 그 어림 비용·시간, 편집하지 못하는 사유. 생성 단계와 같은 요청을 만들어 묻는다.
- 어림값은 생성기가 알려 준 값을 쓰고, 없으면 그 장면을 지난번에 만들 때의 기록으로 채운다 (새로 생성일 때만).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace

from frame.domain.enums import ChangeScope, CheckType, RevisionKind, Verdict
from frame.domain.errors import NotFound, ValidationError
from frame.domain.models import ReferenceImage, VideoVersion
from frame.domain.revision import AttributeChange, ChangePropagationPolicy, KeepItem
from frame.domain.revision.resolution import resolve_revision, revision_draft

from ..pipeline.requests import condition_request, parent_clip_of, revision_directive
from ..ports.change_request import ChangeRequestInterpreter
from ..ports.generator import ProductionMethod, ProductionPreview, VideoGenerator
from ..ports.unit_of_work import UnitOfWorkFactory

log = logging.getLogger(__name__)

# 계획을 볼 때 만드는 수정 버전의 id. 저장되지 않고, 생성기에 물을 요청을 만드는 데만 쓴다
_PLAN_VERSION_ID = "plan-preview"


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
    # 지금 만들면 어떻게 만들어질지 (다시 만들 장면만, 생성기가 알려 줄 때만. ADR-0020)
    production: ProductionPreview | None = None

    @property
    def will_be_edited(self) -> bool:
        return self.production is not None and self.production.method is ProductionMethod.EDIT

    @property
    def planned_cost_usd(self) -> float | None:
        """다시 만드는 어림 비용: 생성기가 알려 준 값, 없으면 지난번 기록.

        편집으로 만들 장면은 지난 기록으로 채우지 않는다 (그것은 새로 생성한 비용이라 더 크다).
        """
        if self.production is not None and self.production.cost_usd is not None:
            return self.production.cost_usd
        return None if self.will_be_edited else self.recorded_cost_usd

    @property
    def planned_seconds(self) -> float | None:
        if self.production is not None and self.production.seconds is not None:
            return self.production.seconds
        return None if self.will_be_edited else self.recorded_seconds


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
            s.planned_cost_usd
            for s in self.scenes
            if s.will_regenerate and s.planned_cost_usd is not None
        ]
        return round(sum(costs), 6) if costs else None

    @property
    def estimated_seconds(self) -> float | None:
        seconds = [
            s.planned_seconds
            for s in self.scenes
            if s.will_regenerate and s.planned_seconds is not None
        ]
        return round(sum(seconds), 1) if seconds else None

    @property
    def estimated_scene_count(self) -> int:
        """다시 만들 장면 가운데 어림 비용이나 시간이 있는 장면 수 (어림값이 몇 장면을 덮는지)."""
        return sum(
            1
            for s in self.scenes
            if s.will_regenerate
            and (s.planned_cost_usd is not None or s.planned_seconds is not None)
        )

    @property
    def edit_scene_count(self) -> int:
        """다시 만들 장면 가운데 부모 클립 편집으로 만들어질 장면 수 (생성기가 알려 줄 때)."""
        return sum(1 for s in self.scenes if s.will_regenerate and s.will_be_edited)


class PlanRevision:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        policy: ChangePropagationPolicy,
        interpreter: ChangeRequestInterpreter,
        generator: VideoGenerator | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._policy = policy
        self._interpreter = interpreter
        self._generator = generator  # 없으면 장면별 생성 방식을 묻지 않는다

    def execute(self, command: PlanRevisionCommand) -> RevisionPlan:
        with self._uow_factory() as uow:
            source = uow.versions.get(command.version_id)
            if source is None:
                raise NotFound(f"버전 {command.version_id} 을 찾을 수 없습니다")
            ir = source.ensure_revisable()
            checks = uow.checks.list_by_version(command.version_id)
            references = uow.reference_images.list_by_version(command.version_id)
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
        # 수정 버전을 만들 때와 같은 버전을 만들어(저장하지 않는다) 생성기에 물을 요청을 구성한다
        draft = revision_draft(
            source,
            resolved,
            change,
            version_id=_PLAN_VERSION_ID,
            created_at=source.created_at,
            request_text=text,
        )
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
                    production=(
                        self._preview(draft, source, references, scene.scene_key)
                        if scene.scene_key in targets
                        else None
                    ),
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

    def _preview(
        self,
        draft: VideoVersion,
        source: VideoVersion,
        references: list[ReferenceImage],
        scene_key: str,
    ) -> ProductionPreview | None:
        """이 장면을 지금 만들면 어떻게 만들어질지 생성기에 묻는다. 모르거나 물을 수 없으면 None.

        생성 단계가 만드는 것과 같은 요청이다: 수정 뒤의 조건, 부모 클립, 수정 지시. 정체성 기준 이미지는 싣지 않는다
        (방식과 비용에 영향이 없다).
        """
        if self._generator is None or draft.ir is None:
            return None
        scene = draft.ir.scene(scene_key)
        request = replace(
            condition_request(draft, draft.ir, scene, references),
            parent=parent_clip_of(source, scene),
            directive=revision_directive(draft.intent, scene),
        )
        try:
            return self._generator.preview(request)
        except Exception as exc:  # noqa: BLE001 - 보조 정보다: 못 받아도 계획은 돌려준다
            log.warning(
                "장면 %s 의 생성 방식을 미리 알아내지 못했습니다 (버전 %s): %s: %s",
                scene_key,
                source.version_id,
                type(exc).__name__,
                exc,
            )
            return None


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None
