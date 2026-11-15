"""테마의 고정 출연과 그 정체성 기준 이미지 (ADR-0025·0026).

고정 출연은 모든 편에 같은 객체·같은 고정 속성으로 나온다. 기준 이미지의 출처(AnchorOrigin)는 셋이고,
외부 영상에서 온 테마는 원본에서 잘라 낸 이미지를 쓸 수 없다 (Theme.validate 가 막는다).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from frame.domain.enums import EntityKind
from frame.domain.ir import EntitySpec

from .conditions import Condition, _conditions


class AnchorOrigin(StrEnum):
    """고정 출연의 기준 이미지가 어디서 왔는가."""

    SOURCE = "SOURCE"  # 테마를 뽑은 원본 영상에서 잘라 냄 (FRAME 이 만든 영상일 때만)
    PILOT = "PILOT"  # 이 테마로 처음 만든 편의 클립에서 뽑음
    USER = "USER"  # 사용자가 준 이미지


@dataclass(frozen=True)
class CastMember:
    """편마다 다시 나오는 고정 출연 (인물·소품 등). 고정 속성은 그 출연이 나오는 모든 장면의 요구조건이 된다."""

    entity_key: str
    kind: EntityKind = EntityKind.OTHER
    description: str = ""
    role: str = ""  # 시리즈에서의 구실 (진행자, 소개하는 제품 …)
    fixed_attributes: tuple[Condition, ...] = ()
    reference_image_uri: str | None = None  # 정체성 기준 이미지 (ADR-0015 를 편 사이로 넓힌 것)
    reference_origin: AnchorOrigin | None = None
    reference_description: str = ""  # 기준 이미지에서 읽은 외형

    def entity_spec(self) -> EntitySpec:
        return EntitySpec(self.entity_key, self.kind, self.description)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_key": self.entity_key,
            "kind": str(self.kind),
            "description": self.description,
            "role": self.role,
            "fixed_attributes": [c.to_dict() for c in self.fixed_attributes],
            "reference_image_uri": self.reference_image_uri,
            "reference_origin": str(self.reference_origin) if self.reference_origin else None,
            "reference_description": self.reference_description,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CastMember:
        origin = data.get("reference_origin")
        return cls(
            entity_key=str(data["entity_key"]),
            kind=EntityKind(data.get("kind", EntityKind.OTHER)),
            description=str(data.get("description", "")),
            role=str(data.get("role", "")),
            fixed_attributes=_conditions(data.get("fixed_attributes")),
            reference_image_uri=data.get("reference_image_uri"),
            reference_origin=AnchorOrigin(origin) if origin else None,
            reference_description=str(data.get("reference_description", "")),
        )
