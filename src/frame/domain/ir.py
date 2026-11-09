"""구조화된 중간표현(Intermediate Representation).

자연어 요구사항을 장면(Scene)·객체(Entity)·속성/관계 조건(Requirement)·시간 정보로 분해한
**모델 비의존** 값 객체이다. 생성/추적/검수 어댑터는 모두 이 IR 만 입력으로 받으므로
모델을 교체해도 IR 과 그 위의 유스케이스는 변하지 않는다.

불변(frozen) 객체이며, 변경은 항상 새 IR 을 만들어 반환한다 (원본 버전 보존 원칙).

스키마 규칙 (validate 가 강제)
- 객체 키는 프로젝트 내, 장면 키는 버전 내 유일.
- 장면은 0 ms 에서 시작해 빈틈·겹침 없이 이어진다 (조립기가 순서대로 이어 붙이기 때문).
- 요구조건의 술어와 값은 어휘(vocabulary)로 정규화되고, 관계 술어는 상대 객체가 필수이며 값은 상대 객체 키와 같다.
- 관계의 주체·상대는 배경(BACKGROUND) 객체일 수 없다. 장소는 장면 수준 location 조건으로 표현한다.
- 요구조건 키 `장면:객체:술어[:상대객체]` 는 장면 안에서 유일하다.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace
from typing import Any

from .enums import EntityKind
from .errors import NotFound, ValidationError
from .hashing import canonical_hash
from .vocabulary import DEFAULT_VOCABULARY

IR_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class RequirementSpec:
    """장면 안에서 지켜져야 하는 조건. 예) cup.color = red, barista holds -> cup, scene.mood = calm"""

    scene_key: str
    predicate: str
    expected_value: str
    entity_key: str | None = None  # None 이면 장면 수준 조건 (분위기·조명·카메라·장소 등)
    related_entity_key: str | None = None  # 관계 조건의 상대 객체

    def __post_init__(self) -> None:
        predicate = DEFAULT_VOCABULARY.normalize(self.predicate)
        object.__setattr__(self, "predicate", predicate)
        value = DEFAULT_VOCABULARY.normalize_value(predicate, self.expected_value or "")
        if not value and self.related_entity_key:
            value = self.related_entity_key  # 관계 조건의 값은 상대 객체 키
        object.__setattr__(self, "expected_value", value)

    @property
    def is_relational(self) -> bool:
        return self.related_entity_key is not None

    @property
    def key(self) -> str:
        """버전 내에서 요구조건을 식별하는 안정적인 키 (CHECK_RESULT.requirement_id 매핑용)."""
        base = f"{self.scene_key}:{self.entity_key or '-'}:{self.predicate}"
        return f"{base}:{self.related_entity_key}" if self.related_entity_key else base


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

    @property
    def total_duration_ms(self) -> int:
        return self.scenes[-1].planned_end_ms if self.scenes else 0

    def with_scene(self, scene: SceneSpec) -> IntermediateRepresentation:
        """같은 scene_key 의 장면을 교체한 새 IR 을 돌려준다."""
        scenes = tuple(scene if s.scene_key == scene.scene_key else s for s in self.scenes)
        return replace(self, scenes=scenes)

    # ---- 무결성 규칙 (ERD 규칙 2·3·4 의 도메인 측 검증 + 타임라인·관계 규칙) ----------
    def validate(self) -> None:
        entity_keys = [e.entity_key for e in self.entities]
        if len(entity_keys) != len(set(entity_keys)):
            raise ValidationError("entity_key 는 프로젝트 내에서 유일해야 합니다")
        scene_keys = [s.scene_key for s in self.scenes]
        if len(scene_keys) != len(set(scene_keys)):
            raise ValidationError("scene_key 는 버전 내에서 유일해야 합니다")
        if not self.scenes:
            raise ValidationError("장면이 하나 이상 있어야 합니다")
        self._validate_timeline()
        kinds = {e.entity_key: e.kind for e in self.entities}
        for s in self.scenes:
            unknown = set(s.entity_keys) - set(kinds)
            if unknown:
                raise ValidationError(
                    f"장면 '{s.scene_key}' 이 정의되지 않은 객체를 참조합니다: {sorted(unknown)}"
                )
            self._validate_requirements(s, kinds)

    def _validate_timeline(self) -> None:
        expected_start = 0
        for index, s in enumerate(self.scenes):
            if s.planned_start_ms < 0 or s.planned_end_ms <= s.planned_start_ms:
                raise ValidationError(f"장면 '{s.scene_key}' 의 시간 구간이 잘못되었습니다")
            if index == 0 and s.planned_start_ms != 0:
                raise ValidationError(f"첫 장면 '{s.scene_key}' 은 0 ms 에서 시작해야 합니다")
            if s.planned_start_ms < expected_start:
                raise ValidationError(
                    f"장면 '{s.scene_key}' 이 앞 장면과 겹칩니다 "
                    f"({s.planned_start_ms} ms < {expected_start} ms)"
                )
            if s.planned_start_ms > expected_start:
                raise ValidationError(
                    f"장면 '{s.scene_key}' 앞에 비어 있는 구간이 있습니다 "
                    f"({expected_start}~{s.planned_start_ms} ms)"
                )
            expected_start = s.planned_end_ms

    @staticmethod
    def _validate_requirements(scene: SceneSpec, kinds: Mapping[str, EntityKind]) -> None:
        seen: set[str] = set()
        for r in scene.requirements:
            if r.scene_key != scene.scene_key:
                raise ValidationError(f"요구조건 {r.key} 의 scene_key 가 장면과 다릅니다")
            if not r.expected_value:
                raise ValidationError(f"요구조건 {r.key} 의 값이 비어 있습니다")
            for ref in (r.entity_key, r.related_entity_key):
                if ref is not None and ref not in scene.entity_keys:
                    raise ValidationError(
                        f"요구조건 {r.key} 의 대상 객체 '{ref}' 는 장면 '{scene.scene_key}' 에 "
                        "연결되어 있어야 합니다"
                    )
            relational = DEFAULT_VOCABULARY.is_relational(r.predicate)
            if relational is True and r.related_entity_key is None:
                raise ValidationError(
                    f"'{r.predicate}' 는 관계 술어이므로 상대 객체(related_entity_key)가 필요합니다 ({r.key})"
                )
            if relational is False and r.related_entity_key is not None:
                raise ValidationError(
                    f"'{r.predicate}' 는 속성 술어이므로 관계 상대 객체를 가질 수 없습니다 ({r.key})"
                )
            if r.related_entity_key is not None:
                if r.expected_value != r.related_entity_key:
                    raise ValidationError(f"관계 조건 {r.key} 의 값은 상대 객체 키와 같아야 합니다")
                if r.related_entity_key == r.entity_key:
                    raise ValidationError(
                        f"관계 조건 {r.key} 은 자기 자신을 상대로 가질 수 없습니다"
                    )
                if kinds.get(r.related_entity_key) is EntityKind.BACKGROUND:
                    raise ValidationError(
                        f"관계 조건 {r.key} 은 배경 객체를 상대로 가질 수 없습니다 "
                        "(장소는 장면 수준 location 조건으로 표현)"
                    )
                if r.entity_key is not None and kinds.get(r.entity_key) is EntityKind.BACKGROUND:
                    raise ValidationError(
                        f"배경 객체 '{r.entity_key}' 는 관계 조건 {r.key} 의 주체가 될 수 없습니다"
                    )
            if r.key in seen:
                raise ValidationError(
                    f"장면 '{scene.scene_key}' 에 중복된 요구조건이 있습니다: {r.key}"
                )
            seen.add(r.key)

    # ---- 직렬화 / 조건 고정 -----------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": IR_SCHEMA_VERSION,
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
        version = data.get("schema_version", IR_SCHEMA_VERSION)
        if version != IR_SCHEMA_VERSION:
            raise ValidationError(
                f"지원하지 않는 schema_version 입니다: {version} (지원: {IR_SCHEMA_VERSION})"
            )
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
                        expected_value=r.get("expected_value", ""),
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
