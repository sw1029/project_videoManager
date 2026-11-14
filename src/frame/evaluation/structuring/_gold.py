"""골드: 주석이 붙은 기대 IR(GoldSpec)과 골드 사례 파일(GoldCase).

골드 사례 = 브리프 + 목표 길이 + 주석이 붙은 기대 IR (tests/evaluation/structuring_gold/*.json).

골드 주석 (기대 IR 에 덧붙이며 IR.from_dict 는 읽지 않는다)
- 객체: aliases(키·설명에서 찾을 이름), accept_kinds(같다고 볼 다른 종류), optional
- 장면: optional_entities(그 장면에 있어도 없어도 되는 객체)
- 요구조건: accept(다른 값), accept_predicates(다른 술어), optional
선택 항목은 맞혀도 세지 않고(중립), 값이 틀리면 오탐으로 센다. 선택 객체가 걸린 요구조건은 저절로 선택이 된다.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from frame.application.ports.structurer import StructuringRequest
from frame.domain.enums import EntityKind
from frame.domain.errors import ValidationError
from frame.domain.hashing import canonical_hash
from frame.domain.ir import EntitySpec, IntermediateRepresentation, RequirementSpec
from frame.domain.vocabulary import DEFAULT_VOCABULARY

from ._values import values_match


@dataclass(frozen=True)
class GoldEntity:
    entity_key: str
    kind: EntityKind
    aliases: tuple[str, ...]
    accept_kinds: frozenset[EntityKind] = frozenset()
    optional: bool = False

    def accepts(self, kind: EntityKind) -> bool:
        return kind is self.kind or kind in self.accept_kinds


@dataclass(frozen=True)
class GoldRequirement:
    spec: RequirementSpec
    accept: tuple[str, ...] = ()
    accept_predicates: frozenset[str] = frozenset()
    optional: bool = False

    @property
    def key(self) -> str:
        return self.spec.key

    @property
    def context(self) -> tuple[str, str | None, str | None]:
        return (self.spec.scene_key, self.spec.entity_key, self.spec.related_entity_key)

    def matches(self, value: str) -> bool:
        expected = (self.spec.expected_value, *self.accept)
        return any(values_match(self.spec.predicate, e, value) for e in expected)


@dataclass(frozen=True)
class GoldSpec:
    ir: IntermediateRepresentation
    entities: tuple[GoldEntity, ...]
    requirements: tuple[GoldRequirement, ...]
    optional_appearances: frozenset[tuple[str, str]]

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> GoldSpec:
        """주석이 붙은 기대 IR 문서를 읽는다. 요구조건의 scene_key 는 생략할 수 있다."""
        doc = copy.deepcopy(dict(document))
        raw_scenes = doc.get("scenes", [])
        for raw in raw_scenes:
            for req in raw.get("requirements", []):
                req.setdefault("scene_key", raw.get("scene_key"))
        ir = IntermediateRepresentation.from_dict(doc)
        ir.validate()

        entities = tuple(
            _gold_entity(raw, spec)
            for raw, spec in zip(doc.get("entities", []), ir.entities, strict=True)
        )
        optional_keys = {e.entity_key for e in entities if e.optional}
        optional_pairs: set[tuple[str, str]] = set()
        requirements: list[GoldRequirement] = []
        for raw, scene in zip(raw_scenes, ir.scenes, strict=True):
            extra = set(raw.get("optional_entities", ()))
            unknown = extra - set(scene.entity_keys)
            if unknown:
                raise ValidationError(
                    f"장면 '{scene.scene_key}' 의 optional_entities 가 장면에 없는 객체를 가리킵니다: "
                    f"{sorted(unknown)}"
                )
            optional_pairs.update(
                (scene.scene_key, key)
                for key in scene.entity_keys
                if key in optional_keys or key in extra
            )
            for raw_req, spec in zip(raw.get("requirements", []), scene.requirements, strict=True):
                refs = {
                    (scene.scene_key, k) for k in (spec.entity_key, spec.related_entity_key) if k
                }
                requirements.append(
                    GoldRequirement(
                        spec,
                        accept=tuple(str(v) for v in raw_req.get("accept", ())),
                        accept_predicates=frozenset(
                            DEFAULT_VOCABULARY.normalize(p)
                            for p in raw_req.get("accept_predicates", ())
                        ),
                        optional=bool(raw_req.get("optional")) or bool(refs & optional_pairs),
                    )
                )
        return cls(ir, entities, tuple(requirements), frozenset(optional_pairs))

    @property
    def required_entity_keys(self) -> tuple[str, ...]:
        return tuple(e.entity_key for e in self.entities if not e.optional)

    @property
    def required_appearances(self) -> frozenset[tuple[str, str]]:
        pairs = {(s.scene_key, key) for s in self.ir.scenes for key in s.entity_keys}
        return frozenset(pairs - self.optional_appearances)


def _gold_entity(raw: Mapping[str, Any], spec: EntitySpec) -> GoldEntity:
    aliases = tuple(str(a) for a in raw.get("aliases", ()) if str(a).strip())
    return GoldEntity(
        spec.entity_key,
        spec.kind,
        aliases or (spec.entity_key.replace("_", " "), spec.description),
        frozenset(EntityKind(k) for k in raw.get("accept_kinds", ())),
        bool(raw.get("optional")),
    )


@dataclass(frozen=True)
class GoldCase:
    case_id: str
    language: str
    brief: str
    target_duration_ms: int
    document: Mapping[str, Any]  # 주석이 붙은 기대 IR
    spec: GoldSpec
    covers: tuple[str, ...] = ()
    notes: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> GoldCase:
        document = data["expected"]
        return cls(
            case_id=str(data["case_id"]),
            language=str(data["language"]),
            brief=str(data["brief"]),
            target_duration_ms=int(data["target_duration_ms"]),
            document=document,
            spec=GoldSpec.from_document(document),
            covers=tuple(data.get("covers", ())),
            notes=str(data.get("notes", "")),
        )

    def request(self) -> StructuringRequest:
        return StructuringRequest(self.brief, target_duration_ms=self.target_duration_ms)


def load_gold_cases(directory: Path | str) -> tuple[GoldCase, ...]:
    """폴더의 *.json 을 파일 이름 순서로 읽는다. 기대 IR 이 IR 규칙을 어기면 ValidationError."""
    paths = sorted(Path(directory).glob("*.json"))
    return tuple(GoldCase.from_dict(json.loads(p.read_text(encoding="utf-8"))) for p in paths)


def gold_fingerprint(cases: Iterable[GoldCase]) -> str:
    """골드셋 내용의 해시. 지문이 다른 기록끼리는 같은 잣대로 잰 것이 아니다."""
    return canonical_hash(
        [
            {
                "case_id": c.case_id,
                "brief": c.brief,
                "target_duration_ms": c.target_duration_ms,
                "expected": c.document,
            }
            for c in cases
        ]
    )
