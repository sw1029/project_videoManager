"""모델 출력(raw IR 문서)에 대한 결정적 보정.

v0: 객체 종류 별칭 정리, 타임라인 스냅, 요구조건 scene_key 정합·관계 값 보충·객체 자동 연결,
    배경 객체와의 관계·미정의 객체 관계 제거. 바뀐 것은 notes 로 남긴다 (구조화 보고서의 재료).
v1 (STRUCTURING_DESIGN.md 6장): 규칙별 RepairLog·경고 수준·근거 검사로 확장한다.
"""

from __future__ import annotations

import copy
from typing import Any

from frame.domain.enums import EntityKind
from frame.domain.ir import MIN_SCENE_MS

_KIND_ALIASES: dict[str, EntityKind] = {
    "person": EntityKind.PERSON,
    "people": EntityKind.PERSON,
    "human": EntityKind.PERSON,
    "character": EntityKind.PERSON,
    "animal": EntityKind.ANIMAL,
    "pet": EntityKind.ANIMAL,
    "dog": EntityKind.ANIMAL,
    "cat": EntityKind.ANIMAL,
    "prop": EntityKind.PROP,
    "object": EntityKind.PROP,
    "item": EntityKind.PROP,
    "product": EntityKind.PROP,
    "background": EntityKind.BACKGROUND,
    "environment": EntityKind.BACKGROUND,
    "place": EntityKind.BACKGROUND,
    "location": EntityKind.BACKGROUND,
    "scene": EntityKind.BACKGROUND,
    "setting": EntityKind.BACKGROUND,
    "text": EntityKind.TEXT,
    "caption": EntityKind.TEXT,
    "subtitle": EntityKind.TEXT,
    "other": EntityKind.OTHER,
}


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_kind(raw: Any, entity_key: str, notes: list[str]) -> str:
    text = str(raw or "").strip()
    try:
        return str(EntityKind(text.upper()))
    except ValueError:
        pass
    alias = _KIND_ALIASES.get(text.lower().replace("-", "_").replace(" ", "_"))
    if alias is not None:
        return str(alias)
    notes.append(f"객체 '{entity_key}' 의 종류 '{text}' 를 OTHER 로 바꿨습니다")
    return str(EntityKind.OTHER)


def light_repair_with_notes(document: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """IR 문서(dict)를 고친 복사본과 보정 메모를 돌려준다. 고칠 수 없는 문제는 validate 가 잡게 둔다."""
    doc = copy.deepcopy(document)
    notes: list[str] = []
    entities = [e for e in doc.get("entities", []) if isinstance(e, dict)]
    for entity in entities:
        entity["kind"] = _normalize_kind(entity.get("kind"), str(entity.get("entity_key")), notes)
    kinds = {e.get("entity_key"): e.get("kind") for e in entities if e.get("entity_key")}

    scenes = doc.get("scenes")
    if not isinstance(scenes, list):
        return doc, notes

    # 1) 타임라인 스냅: 시작 시각 순으로 정렬한 뒤 각 장면의 길이를 유지하며 0 ms 부터 이어 붙인다
    scenes.sort(key=lambda s: _int(s.get("planned_start_ms")) if isinstance(s, dict) else 0)
    cursor = 0
    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        start, end = _int(scene.get("planned_start_ms")), _int(scene.get("planned_end_ms"))
        duration = max(end - start, MIN_SCENE_MS)
        if (start, end) != (cursor, cursor + duration):
            notes.append(
                f"장면 '{scene.get('scene_key')}' 구간을 {start}-{end} ms 에서 "
                f"{cursor}-{cursor + duration} ms 로 옮겼습니다"
            )
        scene["planned_start_ms"] = cursor
        scene["planned_end_ms"] = cursor + duration
        cursor += duration

    # 2) 요구조건 정합: scene_key 맞춤, 관계 값 보충, 참조 객체 연결, 배경·미정의 객체 관계 제거
    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        scene_key = scene.get("scene_key")
        keys = [k for k in (scene.get("entity_keys") or []) if isinstance(k, str)]
        kept: list[Any] = []
        for req in scene.get("requirements") or []:
            if not isinstance(req, dict):
                continue
            if scene_key:
                req["scene_key"] = scene_key
            subject, related = req.get("entity_key"), req.get("related_entity_key")
            if related:
                if related not in kinds:
                    notes.append(
                        f"장면 '{scene_key}' 의 관계 {subject}.{req.get('predicate')} -> {related} 를 "
                        "제거했습니다 (정의되지 않은 객체)"
                    )
                    continue
                if kinds.get(related) == str(EntityKind.BACKGROUND) or (
                    subject and kinds.get(subject) == str(EntityKind.BACKGROUND)
                ):
                    notes.append(
                        f"장면 '{scene_key}' 의 관계 {subject}.{req.get('predicate')} -> {related} 를 "
                        "제거했습니다 (배경 객체는 관계에 쓸 수 없음)"
                    )
                    continue
                if not str(req.get("expected_value") or "").strip():
                    req["expected_value"] = related
            for key in (subject, related):
                if key and key in kinds and key not in keys:
                    keys.append(key)
                    notes.append(f"장면 '{scene_key}' 에 객체 '{key}' 를 연결했습니다")
            kept.append(req)
        scene["requirements"] = kept
        scene["entity_keys"] = keys
    return doc, notes


def light_repair(document: dict[str, Any]) -> dict[str, Any]:
    return light_repair_with_notes(document)[0]
