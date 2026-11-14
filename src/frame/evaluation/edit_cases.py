"""지표 1 평가 케이스 파일 (JSON) 읽기 (ADR-0022).

케이스 하나가 단일 속성 수정 하나다. 필드 이름은 `POST /versions/{id}/revisions` 와 같다.

    {
      "cases": [
        {
          "case_id": "cup-teal",                 // 생략하면 case_01, case_02 … (위치)
          "parent_version_id": "ver_…",          // 수정할 버전 (COMPLETED·FAILED·DRAFT)
          "entity_key": "cup",                   // 생략·null 이면 장면 수준 조건 (장소·조명 등)
          "predicate": "color",                  // 별칭은 API 처럼 정규화된다 ('색상' → color)
          "new_value": "teal",                   // CHANGE: 새 값 / REPAIR: 지켜져야 하는 값
          "kind": "CHANGE",                      // 선택. 생략하면 계획이 정한다 (지금 값과 같으면 REPAIR)
          "scope": "ALL_APPEARANCES",            // 선택. SCENES 면 scene_keys 로 장면을 고른다
          "scene_keys": [],
          "current_value": null,                 // 선택. 같은 술어의 조건이 여럿일 때 바꿀 것의 지금 값
          "keep": [{"aspect": "BACKGROUND"}],    // 선택. 다시 만드는 장면에서 그대로 둘 요소
          "note": "메모"                          // 선택. 결과에 그대로 남는다
        }
      ]
    }

맨 위를 케이스 목록으로 바로 써도 된다. 맨 위 객체의 cases 밖 필드(설명 등)는 읽지 않는다.
비용이 드는 실행 전에 형식 오류를 모두 걸러 내려고 케이스 안의 모르는 필드는 거부한다
(오타 난 keep 이 조용히 빠지면 그 실험은 다른 조건으로 돈 것이 된다). 부모 버전이 있는지, 객체가 그 버전에 있는지는
실행할 때 케이스마다 확인한다.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from frame.domain.enums import ChangeScope, RevisionKind
from frame.domain.errors import ValidationError
from frame.domain.revision import KeepItem
from frame.domain.services.propagation import AttributeChange

_FIELDS = frozenset(
    {
        "case_id",
        "parent_version_id",
        "entity_key",
        "predicate",
        "new_value",
        "kind",
        "scope",
        "scene_keys",
        "current_value",
        "keep",
        "note",
    }
)
_KEEP_FIELDS = frozenset({"aspect", "entity_key"})


class CaseFileError(ValueError):
    """케이스 파일의 형식 오류. 메시지에 몇 번째 케이스인지 적는다."""


@dataclass(frozen=True)
class EditCaseSpec:
    case_id: str
    parent_version_id: str
    change: AttributeChange  # 술어·값은 정규화된 것
    kind: RevisionKind | None = None  # None 이면 계획이 정한다
    keep: tuple[KeepItem, ...] = ()
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """결과 파일에 남기는 입력 (정규화된 값). 그대로 다시 읽을 수 있다."""
        change = self.change
        return {
            "case_id": self.case_id,
            "parent_version_id": self.parent_version_id,
            "entity_key": change.entity_key,
            "predicate": change.predicate,
            "new_value": change.new_value,
            "kind": str(self.kind) if self.kind is not None else None,
            "scope": str(change.scope),
            "scene_keys": list(change.scene_keys),
            "current_value": change.current_value,
            "keep": [item.to_dict() for item in self.keep],
            "note": self.note,
        }


def load_cases(path: str | Path) -> tuple[EditCaseSpec, ...]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise CaseFileError(f"케이스 파일을 읽지 못했습니다: {path}: {exc}") from exc
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise CaseFileError(f"케이스 파일이 올바른 JSON 이 아닙니다: {path}: {exc}") from exc
    return parse_cases(data)


def parse_cases(data: Any) -> tuple[EditCaseSpec, ...]:
    if isinstance(data, Mapping):
        if "cases" not in data:
            raise CaseFileError("케이스 파일의 맨 위 객체에는 cases 목록이 있어야 합니다")
        data = data["cases"]
    if not isinstance(data, list):
        raise CaseFileError("케이스는 목록(JSON 배열)이어야 합니다")
    if not data:
        raise CaseFileError("케이스 목록이 비어 있습니다")
    specs: list[EditCaseSpec] = []
    seen: set[str] = set()
    for index, item in enumerate(data, start=1):
        spec = _parse_case(index, item)
        if spec.case_id in seen:
            raise CaseFileError(f"{index}번째 케이스: case_id '{spec.case_id}' 가 이미 있습니다")
        seen.add(spec.case_id)
        specs.append(spec)
    return tuple(specs)


def _parse_case(index: int, item: Any) -> EditCaseSpec:
    where = f"{index}번째 케이스"
    if not isinstance(item, Mapping):
        raise CaseFileError(f"{where}: 케이스는 JSON 객체여야 합니다")
    case_id = _optional_text(item, "case_id", where) or f"case_{index:02d}"
    where = f"{index}번째 케이스({case_id})"
    unknown = sorted(set(item) - _FIELDS)
    if unknown:
        raise CaseFileError(
            f"{where}: 모르는 필드 {', '.join(unknown)} (쓸 수 있는 필드: {', '.join(sorted(_FIELDS))})"
        )
    parent = _required_text(item, "parent_version_id", where)
    predicate = _required_text(item, "predicate", where)
    new_value = _required_text(item, "new_value", where)
    kind = _enum(RevisionKind, item.get("kind"), "kind", where)
    scope = _enum(ChangeScope, item.get("scope"), "scope", where) or ChangeScope.ALL_APPEARANCES
    scene_keys = item.get("scene_keys") or []
    if not isinstance(scene_keys, list) or not all(isinstance(k, str) for k in scene_keys):
        raise CaseFileError(f"{where}: scene_keys 는 장면 키(문자열)의 목록이어야 합니다")
    try:
        change = AttributeChange(
            entity_key=_optional_text(item, "entity_key", where),
            predicate=predicate,
            new_value=new_value,
            scope=scope,
            scene_keys=tuple(scene_keys),
            current_value=_optional_text(item, "current_value", where),
        )
    except ValidationError as exc:
        raise CaseFileError(f"{where}: {exc}") from exc
    return EditCaseSpec(
        case_id=case_id,
        parent_version_id=parent,
        change=change,
        kind=kind,
        keep=_keep(item.get("keep"), where),
        note=_optional_text(item, "note", where),
    )


def _required_text(item: Mapping[str, Any], name: str, where: str) -> str:
    value = item.get(name)
    if not isinstance(value, str) or not value.strip():
        raise CaseFileError(f"{where}: {name} 은(는) 비어 있지 않은 문자열이어야 합니다")
    return value.strip()


def _optional_text(item: Mapping[str, Any], name: str, where: str) -> str | None:
    value = item.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise CaseFileError(f"{where}: {name} 은(는) 문자열이어야 합니다")
    return value.strip() or None


def _enum(enum: Any, value: Any, name: str, where: str) -> Any:
    if value is None:
        return None
    try:
        return enum(value)
    except ValueError as exc:
        allowed = ", ".join(str(member) for member in enum)
        raise CaseFileError(f"{where}: {name} 은(는) {allowed} 가운데 하나여야 합니다") from exc


def _keep(value: Any, where: str) -> tuple[KeepItem, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise CaseFileError(f"{where}: keep 은 유지할 요소의 목록이어야 합니다")
    items = []
    for position, raw in enumerate(value):
        label = f"{where}: keep[{position}]"
        if not isinstance(raw, Mapping):
            raise CaseFileError(f"{label} 은(는) {{aspect, entity_key}} 객체여야 합니다")
        unknown = sorted(set(raw) - _KEEP_FIELDS)
        if unknown:
            raise CaseFileError(f"{label}: 모르는 필드 {', '.join(unknown)} (aspect, entity_key)")
        aspect, entity = raw.get("aspect"), raw.get("entity_key")
        if not isinstance(aspect, str) or (entity is not None and not isinstance(entity, str)):
            raise CaseFileError(f"{label}: aspect 와 entity_key 는 문자열이어야 합니다")
        try:
            items.append(KeepItem(aspect, entity or None))  # type: ignore[arg-type]
        except ValidationError as exc:
            raise CaseFileError(f"{label}: {exc}") from exc
    return tuple(items)
