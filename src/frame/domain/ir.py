"""구조화된 중간표현(Intermediate Representation).

자연어 요구사항을 장면(Scene)·객체(Entity)·속성/관계 조건(Requirement)·시간 정보로 분해한
**모델 비의존** 값 객체이다. 생성/추적/검수 어댑터는 모두 이 IR 만 입력으로 받으므로
모델을 교체해도 IR 과 그 위의 유스케이스는 변하지 않는다.

불변(frozen) 객체이며, 변경은 항상 새 IR 을 만들어 반환한다 (원본 버전 보존 원칙).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, replace
from typing import Any

from .enums import EntityKind
from .errors import NotFound, ValidationError
from .hashing import canonical_hash


@dataclass(frozen=True)
class RequirementSpec:
    """장면 안에서 지켜져야 하는 조건. 예) cup.color = red, barista holds -> cup"""

    scene_key: str
    predicate: str
    expected_value: str
    entity_key: str | None = None
    related_entity_key: str | None = None

    @property
    def key(self) -> str:
        """버전 내에서 요구조건을 식별하는 안정적인 키 (CHECK_RESULT.requirement_id 매핑용)."""
        return f"{self.scene_key}:{self.entity_key or '-'}:{self.predicate}"


@dataclass(frozen=True)
class EntitySpec:
    entity_key: str
    kind: EntityKind = EntityKind.OTHER
    description: str = ""


@dataclass(frozen=True)
class SceneSpec:
    scene_key: str
    planned_start_ms: int
    planned_end_ms: int
    description: str = ""
    entity_keys: tuple[str, ...] = ()
    requirements: tuple[RequirementSpec, ...] = ()

    @property
    def duration_ms(self) -> int:
        return self.planned_end_ms - self.planned_start_ms


@dataclass(frozen=True)
class IntermediateRepresentation:
    entities: tuple[EntitySpec, ...] = ()
    scenes: tuple[SceneSpec, ...] = ()

    # ---- 조회 -------------------------------------------------------------
    def has_entity(self, entity_key: str) -> bool:
        return any(e.entity_key == entity_key for e in self.entities)

    def has_scene(self, scene_key: str) -> bool:
        return any(s.scene_key == scene_key for s in self.scenes)

    def entity(self, entity_key: str) -> EntitySpec:
        for e in self.entities:
            if e.entity_key == entity_key:
                return e
        raise NotFound(f"객체 '{entity_key}' 가 중간표현에 없습니다", entity_key=entity_key)

    def scene(self, scene_key: str) -> SceneSpec:
        for s in self.scenes:
            if s.scene_key == scene_key:
                return s
        raise NotFound(f"장면 '{scene_key}' 이 중간표현에 없습니다", scene_key=scene_key)

    def scenes_with_entity(self, entity_key: str) -> tuple[SceneSpec, ...]:
        return tuple(s for s in self.scenes if entity_key in s.entity_keys)

    def iter_requirements(self) -> Iterator[RequirementSpec]:
        for s in self.scenes:
            yield from s.requirements

    def with_scene(self, scene: SceneSpec) -> IntermediateRepresentation:
        """같은 scene_key 의 장면을 교체한 새 IR 을 돌려준다."""
        scenes = tuple(scene if s.scene_key == scene.scene_key else s for s in self.scenes)
        return replace(self, scenes=scenes)

    # ---- 무결성 규칙 (ERD 규칙 2·3·4 의 도메인 측 검증) ----------------------
    def validate(self) -> None:
        entity_keys = [e.entity_key for e in self.entities]
        if len(entity_keys) != len(set(entity_keys)):
            raise ValidationError("entity_key 는 프로젝트 내에서 유일해야 합니다")
        scene_keys = [s.scene_key for s in self.scenes]
        if len(scene_keys) != len(set(scene_keys)):
            raise ValidationError("scene_key 는 버전 내에서 유일해야 합니다")
        if not self.scenes:
            raise ValidationError("장면이 하나 이상 있어야 합니다")
        known = set(entity_keys)
        for s in self.scenes:
            if s.planned_start_ms < 0 or s.planned_end_ms <= s.planned_start_ms:
                raise ValidationError(f"장면 '{s.scene_key}' 의 시간 구간이 잘못되었습니다")
            unknown = set(s.entity_keys) - known
            if unknown:
                raise ValidationError(
                    f"장면 '{s.scene_key}' 이 정의되지 않은 객체를 참조합니다: {sorted(unknown)}"
                )
            for r in s.requirements:
                if r.scene_key != s.scene_key:
                    raise ValidationError(f"요구조건 {r.key} 의 scene_key 가 장면과 다릅니다")
                for ref in (r.entity_key, r.related_entity_key):
                    if ref is not None and ref not in s.entity_keys:
                        raise ValidationError(
                            f"요구조건 {r.key} 의 대상 객체 '{ref}' 는 장면 '{s.scene_key}' 에 "
                            "연결되어 있어야 합니다"
                        )

    # ---- 직렬화 / 조건 고정 -----------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "entities": [
                {"entity_key": e.entity_key, "kind": str(e.kind), "description": e.description}
                for e in self.entities
            ],
            "scenes": [
                {
                    "scene_key": s.scene_key,
                    "planned_start_ms": s.planned_start_ms,
                    "planned_end_ms": s.planned_end_ms,
                    "description": s.description,
                    "entity_keys": list(s.entity_keys),
                    "requirements": [
                        {
                            "scene_key": r.scene_key,
                            "predicate": r.predicate,
                            "expected_value": r.expected_value,
                            "entity_key": r.entity_key,
                            "related_entity_key": r.related_entity_key,
                        }
                        for r in s.requirements
                    ],
                }
                for s in self.scenes
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IntermediateRepresentation:
        entities = tuple(
            EntitySpec(
                entity_key=e["entity_key"],
                kind=EntityKind(e.get("kind", EntityKind.OTHER)),
                description=e.get("description", ""),
            )
            for e in data.get("entities", [])
        )
        scenes = tuple(
            SceneSpec(
                scene_key=s["scene_key"],
                planned_start_ms=int(s["planned_start_ms"]),
                planned_end_ms=int(s["planned_end_ms"]),
                description=s.get("description", ""),
                entity_keys=tuple(s.get("entity_keys", [])),
                requirements=tuple(
                    RequirementSpec(
                        scene_key=r["scene_key"],
                        predicate=r["predicate"],
                        expected_value=r["expected_value"],
                        entity_key=r.get("entity_key"),
                        related_entity_key=r.get("related_entity_key"),
                    )
                    for r in s.get("requirements", [])
                ),
            )
            for s in data.get("scenes", [])
        )
        return cls(entities=entities, scenes=scenes)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> IntermediateRepresentation:
        return cls.from_dict(json.loads(text))

    def fingerprint(self) -> str:
        """IR 전체의 해시. 생성 접수 시 '조건 고정' 값으로 저장된다."""
        return canonical_hash(self.to_dict())

    def scene_fingerprint(self, scene_key: str) -> str:
        """장면 조건 + 그 장면에 등장하는 객체 정의의 해시.

        부모 버전과 값이 같은 장면은 재생성하지 않고 클립을 재사용한다 (선택적 재생성).
        """
        scene = self.scene(scene_key)
        payload = {
            "scene": {
                "scene_key": scene.scene_key,
                "planned_start_ms": scene.planned_start_ms,
                "planned_end_ms": scene.planned_end_ms,
                "description": scene.description,
                "entity_keys": list(scene.entity_keys),
                "requirements": sorted(
                    (r.key, r.expected_value, r.related_entity_key or "")
                    for r in scene.requirements
                ),
            },
            "entities": [
                {"entity_key": e.entity_key, "kind": str(e.kind), "description": e.description}
                for e in self.entities
                if e.entity_key in scene.entity_keys
            ],
        }
        return canonical_hash(payload)
