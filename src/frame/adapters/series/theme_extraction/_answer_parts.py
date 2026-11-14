"""모델의 응답 조각을 IR 의 규칙에 맞게 읽는다 (ADR-0025). 결정적, 순수 함수.

모델이 낸 것을 그대로 믿지 않는다. 규칙에 맞는 것만 쓰고 버린 것은 메모로 남긴다.
- 자리는 'S3'·'slot 3'·숫자·프레임 이름으로만 읽는다 (시각을 자리 번호로 읽지 않는다).
- 객체 키는 영문 소문자·숫자·밑줄로 맞추고 겉모습 낱말을 걷어낸다. 영문이 아닌 키도 서로 가른다.
- 어휘에 없는 술어는 버린다. 객체가 아닌 것을 향한 관계는 행동으로 옮긴다.
- 응답의 큰 모양(목록·객체)을 입구에서 본다.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from frame.domain.enums import EntityKind
from frame.domain.errors import ValidationError
from frame.domain.ir import EntitySpec, RequirementSpec
from frame.domain.screen import Region
from frame.domain.series.theme import (
    Condition,
)
from frame.domain.vocabulary import DEFAULT_VOCABULARY

_CAST_KINDS = (EntityKind.PERSON, EntityKind.ANIMAL, EntityKind.PROP, EntityKind.OTHER)


_SCENE_LEVEL = ("location", "lighting", "mood", "camera", "time_of_day", "weather")


# 자리마다 달라지는 장면 조건: 스타일(모든 장면의 조건)에는 두지 않는다
_SLOT_ONLY = frozenset({"camera"})


_NON_KEY = re.compile(r"[^a-z0-9]+")


# 자리를 가리키는 말: 'S3' · 'slot 3' · 'slot_3 (closeup)' · 프레임 이름 'S3-F2 10.0s', 또는 숫자만 '3'
_SLOT_REF = re.compile(r"\s*(?:(?:slot|s)[\s_#:-]*(\d+)(?![.\d]).*|(\d+))\s*", re.IGNORECASE)


# 객체 키에 들어오면 걷어내는 겉모습 낱말 (색·크기·새것). 키는 '무엇인가' 만 말한다
_LOOK_WORDS = frozenset(
    "red blue green yellow orange purple pink brown black white gray grey teal beige cream gold "
    "silver navy dark light bright big small large little tiny new old".split()
)


def _slot_index(raw: Any, count: int) -> int | None:
    """모델이 가리킨 자리('S3', 'slot 3')의 0 기반 번호. 범위 밖이면 None."""
    number = _slot_number(raw)
    if number is None or not 0 < number <= count:
        return None
    return number - 1


def _slot_number(raw: Any) -> int | None:
    """자리 번호 (1부터). 'S3'·'slot 3'·'3', 그리고 프레임 이름 'S3-F2 10.0s' 를 읽는다.

    시각("5.3s")처럼 자리가 아닌 숫자는 읽지 않는다. 아무 숫자나 집으면 모델이 시각을 적어 왔을 때
    그 숫자가 자리 번호가 된다 (감사 BTS-32).
    """
    match = _SLOT_REF.fullmatch(str(raw or ""))
    if match is None:
        return None
    return int(match.group(1) or match.group(2))


def _squash(raw: Any) -> str:
    """문구를 견줄 수 있게: 공백을 없앤다."""
    return "".join(str(raw or "").split())


def _plain(raw: Any) -> str:
    """키를 견줄 수 있게: 소문자, 영문·숫자 아닌 것은 밑줄."""
    return _NON_KEY.sub("_", str(raw or "").strip().lower()).strip("_")


def _unique(items: Any) -> list[str]:
    return list(dict.fromkeys(items))


def _slot_answers(raw: Any, count: int) -> dict[int, Mapping[str, Any]]:
    """모델의 자리별 답을 자리 번호에 맞춘다. 'S2' 처럼 가리킨 것을 먼저 쓰고, 없으면 나온 순서로 채운다."""
    items = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
    answers: dict[int, Mapping[str, Any]] = {}
    unplaced: list[Mapping[str, Any]] = []
    for item in items:
        number = _slot_number(item.get("slot"))
        index = number - 1 if number is not None else -1
        if 0 <= index < count and index not in answers:
            answers[index] = item
        else:
            unplaced.append(item)
    for index in range(count):
        if index not in answers and unplaced:
            answers[index] = unplaced.pop(0)
    if len(answers) < count:
        missing = [f"S{i + 1}" for i in range(count) if i not in answers]
        raise ValidationError(f"자리마다 답이 있어야 합니다. 빠진 자리: {missing}")
    return answers


def _entities(
    raw: Any, notes: list[str]
) -> tuple[dict[str, str], list[EntitySpec], dict[str, Mapping[str, Any]]]:
    """(모델이 쓴 키 -> 다듬은 키, 객체 목록, 다듬은 키 -> 모델의 원래 항목)."""
    key_of: dict[str, str] = {}
    entities: list[EntitySpec] = []
    originals: dict[str, Mapping[str, Any]] = {}
    for position, item in enumerate(raw if isinstance(raw, list) else [], start=1):
        if not isinstance(item, dict):
            continue
        raw_key = _raw_key(item.get("entity_key"))
        ascii_key = _plain(raw_key)  # 한글 키는 빈 문자열이 된다
        plain = ascii_key or f"entity_{position}"
        if plain[0].isdigit():
            plain = f"entity_{plain}"
        bare = "_".join(word for word in plain.split("_") if word not in _LOOK_WORDS)
        if bare and bare != plain and bare not in originals and not bare[0].isdigit():
            notes.append(f"객체 키 '{plain}' 를 '{bare}' 로 고쳤습니다 (키에 겉모습을 넣지 않는다)")
            key_of[plain] = bare
            plain = bare
        if plain in originals:
            notes.append(f"객체 키 '{plain}' 가 두 번 나와 뒤의 것을 뺐습니다")
            continue
        try:
            kind = EntityKind(str(item.get("kind") or "").strip().upper())
        except ValueError:
            kind = EntityKind.OTHER
        # 모델이 쓴 키 그대로도 찾을 수 있게 한다. 빈 키는 넣지 않는다: 영문이 아닌 키가 모두 빈 키 하나로 모여
        # 마지막 객체를 가리켰다 (감사 BTS-25)
        for name in (raw_key, ascii_key, plain):
            if name:
                key_of.setdefault(name, plain)
        originals[plain] = item
        entities.append(
            EntitySpec(plain, kind, " ".join(str(item.get("description") or "").split()))
        )
    return key_of, entities, originals


def _conditions(raw: Any, notes: list[str], owner: str) -> list[Condition]:
    conditions: list[Condition] = []
    seen: set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            condition = Condition(str(item.get("predicate") or ""), str(item.get("value") or ""))
        except ValidationError:
            continue
        if condition.predicate in seen:
            notes.append(
                f"{owner} 의 조건 '{condition.predicate}' 가 두 번 나와 뒤의 것을 뺐습니다"
            )
            continue
        seen.add(condition.predicate)
        conditions.append(condition)
    return conditions


def _scene_conditions(raw: Any, notes: list[str], owner: str) -> list[Condition]:
    kept = []
    for condition in _conditions(raw, notes, owner):
        if DEFAULT_VOCABULARY.is_relational(condition.predicate):
            notes.append(f"{owner} 의 관계 조건 '{condition.predicate}' 를 뺐습니다")
            continue
        kept.append(condition)
    return kept


def _requirements(
    slot_key: str,
    raw: Any,
    key_of: Mapping[str, str],
    kinds: Mapping[str, EntityKind],
    entity_keys: list[str],
    notes: list[str],
) -> list[RequirementSpec]:
    """자리의 요구조건. IR 의 규칙에 맞지 않는 것은 버리고, 조건이 가리키는 객체는 그 장면에 연결한다.

    - 어휘에 없는 술어는 버린다 (모델이 지어낸 visible·blurred 같은 것).
    - 관계 술어의 상대가 객체가 아니면(카메라·시청자) 그 객체의 행동으로 옮긴다: looks_at camera -> action.
    """
    requirements: list[RequirementSpec] = []
    seen: set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        subject = _lookup(item.get("entity_key"), key_of)
        related = _lookup(item.get("related_entity_key"), key_of)
        if item.get("entity_key") and subject is None:
            notes.append(f"장면 '{slot_key}' 의 조건이 없는 객체를 가리켜 뺐습니다: {item}")
            continue
        try:
            predicate = DEFAULT_VOCABULARY.normalize(str(item.get("predicate") or ""))
        except ValidationError:
            continue
        relational = DEFAULT_VOCABULARY.is_relational(predicate)
        value = str(item.get("value") or "").strip()
        if relational is None:
            notes.append(f"장면 '{slot_key}' 의 어휘에 없는 술어 '{predicate}' 를 뺐습니다")
            continue
        if relational is True and related is None and subject is not None:
            # 상대가 객체가 아니다: 상대로 적어 온 말(또는 값)을 붙여 행동으로 옮긴다
            target = value or " ".join(str(item.get("related_entity_key") or "").split("_"))
            if not target:
                notes.append(
                    f"장면 '{slot_key}' 의 관계 조건 '{predicate}' 를 뺐습니다 (상대 없음)"
                )
                continue
            predicate, value, relational = (
                "action",
                f"{predicate.replace('_', ' ')} {target}",
                False,
            )
        if relational is True:
            background = EntityKind.BACKGROUND in (
                kinds.get(subject or ""),
                kinds.get(related or ""),
            )
            if subject is None or related is None or subject == related or background:
                notes.append(
                    f"장면 '{slot_key}' 의 관계 조건 '{predicate}' 를 뺐습니다 (상대 객체 규칙)"
                )
                continue
            value = related
        else:
            related = None  # 속성 조건은 상대 객체를 갖지 않는다
        try:
            requirement = RequirementSpec(slot_key, predicate, value, subject, related)
        except ValidationError:
            continue
        if not requirement.expected_value or requirement.key in seen:
            continue
        seen.add(requirement.key)
        for key in (subject, related):
            if key is not None and key not in entity_keys:
                entity_keys.append(key)
        requirements.append(requirement)
    return requirements


def _lookup(raw: Any, key_of: Mapping[str, str]) -> str | None:
    """모델이 가리킨 객체 키 -> 다듬은 키. 없으면 None.

    영문·숫자로 다듬을 수 없는 키(한글 등)가 정의된 객체와 맞지 않으면 ValidationError 로 되돌려 보낸다.
    그런 키를 조용히 버리면 장면에서 객체가 빠진 테마가 오류 없이 저장된다.
    """
    if not raw:
        return None
    raw_key = _raw_key(raw)
    found = key_of.get(raw_key)
    if found is not None:
        return found
    ascii_key = _plain(raw_key)
    if not ascii_key:
        raise ValidationError(
            f"entity key '{raw_key}' is not defined in entities. Refer to entities by the exact "
            "entity_key you defined (lowercase English snake_case)"
        )
    return key_of.get(ascii_key)


def _raw_key(raw: Any) -> str:
    """모델이 준 키 그대로 (공백만 다듬는다). 영문이 아닌 키를 서로 가르는 데 쓴다."""
    return " ".join(str(raw or "").split())


def _mapping(raw: Any) -> Mapping[str, Any]:
    return raw if isinstance(raw, Mapping) else {}


_LISTS = ("entities", "slots", "screen_texts", "text_rules", "variables")


_OBJECTS = ("style", "music")


def _check_shapes(document: Mapping[str, Any]) -> None:
    """응답의 큰 모양을 본다. strict 가 아닌 서버는 목록 자리에 글이나 객체를 줄 수 있다 (감사 BTS-15)."""
    for name in _LISTS:
        if document.get(name) is not None and not isinstance(document[name], list):
            raise ValidationError(f"'{name}' must be a list")
    for name in _OBJECTS:
        if document.get(name) is not None and not isinstance(document[name], Mapping):
            raise ValidationError(f"'{name}' must be an object")


def _region(raw: Any) -> Region | None:
    if not isinstance(raw, dict):
        return None
    return Region.clamped(raw.get("x"), raw.get("y"), raw.get("w"), raw.get("h"))
