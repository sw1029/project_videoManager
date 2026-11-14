"""어긋난 곳: 종류, 고치는 방법, 문구를 견주는 규칙 (ADR-0027).

편에서 '화면 정보가 잘못된 곳' 은 다섯 가지로 나뉘고, 종류마다 가장 좁고 싼 방법으로 고친다.

- OVERLAY_TEXT: 얹은 화면 문구(자막·가격·라벨)가 틀리거나 읽히지 않는다.
  -> OVERLAY_EDIT: 그 문구 항목을 고친다. 다시 만드는 것은 그 장면의 합성뿐이다 (무료).
- SCENE_TEXT: 영상 안에 찍힌 글자가 요구와 다르다.
  -> PATCH: 그 영역·시간을 가리고 다시 쓴다 (그 장면의 합성만, 무료). 영역을 모르면 CLIP_EDIT.
- ATTRIBUTE: 객체의 속성·장면 조건이 요구와 다르다.
  -> CLIP_EDIT: 그 장면의 클립만 다시 만드는 수정 버전(REPAIR, 과금). 부모 클립 편집이 켜져 있으면
     (FRAME_VIDEO_EDIT_MODE) 편집으로, 꺼져 있으면(기본) 그 장면의 재생성으로 만든다.
- CONSISTENCY: 같은 객체가 장면마다 다르게 보인다 (요구조건은 맞는데 기준과 다른 모습).
  -> CLIP_EDIT: 그 장면만 다시 맞추는 수정 버전(REPAIR). 그 장면에서 그 객체의 모습 조건 하나를 축으로 삼는다.
- NARRATION: 녹음이 대본과 다르거나, 없거나, 장면보다 길다. 화면이 아니라 소리지만 같은 흐름으로 알린다.
  -> RERECORD: 그 줄만 다시 녹음한다. 다시 만드는 것은 소리 트랙뿐이다.

문구를 견줄 때는 띄어쓰기를 보지 않고 전각·반각 차이를 없앤 뒤 글자를 그대로 견준다
(화면 문구는 철자가 곧 내용이다).
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from frame.domain.ir import RequirementSpec
from frame.domain.screen import Region, region_or_none
from frame.domain.vocabulary import DEFAULT_VOCABULARY, ValueKind


class IssueKind(StrEnum):
    OVERLAY_TEXT = "OVERLAY_TEXT"
    SCENE_TEXT = "SCENE_TEXT"
    ATTRIBUTE = "ATTRIBUTE"
    CONSISTENCY = "CONSISTENCY"  # 같은 객체가 장면마다 다르게 보인다
    NARRATION = "NARRATION"


class CorrectionMethod(StrEnum):
    OVERLAY_EDIT = "OVERLAY_EDIT"  # 화면 문구 항목을 고치고 그 장면만 다시 합성
    PATCH = "PATCH"  # 영상 안의 글자를 가리고 다시 쓰는 문구를 얹음
    CLIP_EDIT = "CLIP_EDIT"  # 그 장면의 클립만 다시 만듦 (REPAIR 수정 버전: 편집 또는 재생성)
    RERECORD = "RERECORD"  # 그 줄만 다시 녹음


@dataclass(frozen=True)
class ScreenIssue:
    """어긋난 곳 하나: 무엇이, 어느 장면의 언제·어디에서, 어떻게 달랐고, 어떻게 고칠 수 있는가."""

    issue_id: str
    kind: IssueKind
    scene_key: str
    start_ms: int  # 편의 타임라인에서의 구간
    end_ms: int
    expected: str
    observed: str = ""
    method: CorrectionMethod = CorrectionMethod.CLIP_EDIT
    region: Region | None = None
    requirement_key: str | None = None
    entity_key: str | None = None
    predicate: str | None = None
    overlay_id: str | None = None
    line_key: str | None = None
    detail: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_id": self.issue_id,
            "kind": str(self.kind),
            "scene_key": self.scene_key,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "expected": self.expected,
            "observed": self.observed,
            "method": str(self.method),
            "region": self.region.to_dict() if self.region else None,
            "requirement_key": self.requirement_key,
            "entity_key": self.entity_key,
            "predicate": self.predicate,
            "overlay_id": self.overlay_id,
            "line_key": self.line_key,
            "detail": dict(self.detail),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ScreenIssue:
        return cls(
            issue_id=str(data["issue_id"]),
            kind=IssueKind(data["kind"]),
            scene_key=str(data["scene_key"]),
            start_ms=int(data["start_ms"]),
            end_ms=int(data["end_ms"]),
            expected=str(data.get("expected", "")),
            observed=str(data.get("observed", "")),
            method=CorrectionMethod(data.get("method", CorrectionMethod.CLIP_EDIT)),
            region=region_or_none(data.get("region")),
            requirement_key=data.get("requirement_key"),
            entity_key=data.get("entity_key"),
            predicate=data.get("predicate"),
            overlay_id=data.get("overlay_id"),
            line_key=data.get("line_key"),
            detail=dict(data.get("detail") or {}),
        )


def patch_overlay_id(scene_key: str, entity_key: str | None, predicate: str | None) -> str:
    """화면 속 글자를 가리고 다시 쓰는 문구 항목의 id. 요구조건 하나에 가린 문구는 하나다 (다시 고치면 갈아 끼운다)."""
    return f"{scene_key}:patch:{entity_key or 'scene'}:{predicate}"


def normalize_screen_text(text: str) -> str:
    """화면 문구를 견줄 수 있게: 전각·반각을 맞추고 공백을 없앤다. 글자·숫자·문장부호는 그대로 둔다."""
    return "".join(unicodedata.normalize("NFKC", text or "").split())


def screen_text_matches(expected: str, observed: str) -> bool:
    return normalize_screen_text(expected) == normalize_screen_text(observed)


def is_text_requirement(requirement: RequirementSpec) -> bool:
    """철자가 곧 내용인 요구조건인가 (화면 문구·브랜드)."""
    return DEFAULT_VOCABULARY.value_kind(requirement.predicate) is ValueKind.LITERAL
