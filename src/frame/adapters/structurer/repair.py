"""모델 출력(raw IR 문서)에 대한 결정적 보정.

v0 (현재): 타임라인 스냅, 요구조건의 객체를 장면에 자동 연결, 관계 값 보충, 요구조건 scene_key 정합.
v1 (STRUCTURING_DESIGN.md 6장): 규칙별 RepairLog·경고·근거 검사로 확장한다.
"""

from __future__ import annotations

import copy
from typing import Any

MIN_SCENE_MS = 500


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def light_repair(document: dict[str, Any]) -> dict[str, Any]:
    """IR 문서(dict)를 고친 복사본을 돌려준다. 고칠 수 없는 문제는 그대로 두어 validate 가 잡게 한다."""
    doc = copy.deepcopy(document)
    scenes = doc.get("scenes")
    if not isinstance(scenes, list):
        return doc

    # 1) 타임라인 스냅: 시작 시각 순으로 정렬한 뒤 각 장면의 길이를 유지하며 0 ms 부터 이어 붙인다
    scenes.sort(key=lambda s: _int(s.get("planned_start_ms")) if isinstance(s, dict) else 0)
    cursor = 0
    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        duration = max(
            _int(scene.get("planned_end_ms")) - _int(scene.get("planned_start_ms")), MIN_SCENE_MS
        )
        scene["planned_start_ms"] = cursor
        scene["planned_end_ms"] = cursor + duration
        cursor += duration

    # 2) 요구조건 정합: scene_key 맞춤, 관계 값 보충, 참조 객체를 장면에 연결
    known = {
        e.get("entity_key")
        for e in doc.get("entities", [])
        if isinstance(e, dict) and e.get("entity_key")
    }
    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        keys = [k for k in (scene.get("entity_keys") or []) if isinstance(k, str)]
        for req in scene.get("requirements") or []:
            if not isinstance(req, dict):
                continue
            if scene.get("scene_key"):
                req["scene_key"] = scene["scene_key"]
            related = req.get("related_entity_key")
            if related and not str(req.get("expected_value") or "").strip():
                req["expected_value"] = related
            for key in (req.get("entity_key"), related):
                if key and key in known and key not in keys:
                    keys.append(key)
        scene["entity_keys"] = keys
    return doc
