"""구조화 골드셋 (US-06 AC-11, T2·T6). 지표 계산은 structuring_metrics 가 한다.

골드 사례 = 브리프 + 목표 길이 + 주석이 붙은 기대 IR (tests/evaluation/structuring_gold/*.json).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from frame.application.ports.structurer import StructuringRequest

from .structuring_metrics import GoldSpec


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
