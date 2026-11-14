"""테마·편 저장 포트 (ADR-0025·0026). 구현: 메모리(테스트), 산출물 저장소의 JSON 문서.

테마와 편은 프로젝트·버전(관계형 저장소)과 달리 문서 하나로 읽고 쓴다. 편은 프로젝트·버전을 id 로 가리킬 뿐이다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from frame.domain.ir import IntermediateRepresentation
from frame.domain.script import SpokenScript
from frame.domain.series.record import Episode
from frame.domain.series.script_evidence import ScriptEvidence
from frame.domain.series.source_analysis import ScreenText, VideoAnalysis
from frame.domain.series.theme import Theme


@dataclass(frozen=True)
class ThemeRecord:
    """테마와 그것을 뽑을 때의 근거: 원본 IR, 관찰값, 화면에서 읽은 문구, 숏폼 규격 판정."""

    theme: Theme
    source_ir: IntermediateRepresentation
    created_at: datetime
    analysis: VideoAnalysis | None = None
    screen_texts: tuple[ScreenText, ...] = ()
    shortform: Mapping[str, Any] = field(default_factory=dict)  # ShortformVerdict.to_dict()
    script: SpokenScript | None = None  # 원본의 소리에서 뽑은 대본 (ADR-0028). 옛 기록에는 없다
    evidence: ScriptEvidence | None = None  # 화면에서 읽은 것과 대본을 견준 근거·어긋남
    notes: tuple[str, ...] = ()
    detail: Mapping[str, Any] = field(default_factory=dict)  # 추출 방법·모델·토큰 수

    def to_dict(self) -> dict[str, Any]:
        return {
            "theme": self.theme.to_dict(),
            "source_ir": self.source_ir.to_dict(),
            "created_at": self.created_at.isoformat(),
            "analysis": self.analysis.to_dict() if self.analysis is not None else None,
            "screen_texts": [t.to_dict() for t in self.screen_texts],
            "script": self.script.to_dict() if self.script is not None else None,
            "evidence": self.evidence.to_dict() if self.evidence is not None else None,
            "shortform": dict(self.shortform),
            "notes": list(self.notes),
            "detail": dict(self.detail),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ThemeRecord:
        analysis = data.get("analysis")
        script = data.get("script")
        evidence = data.get("evidence")
        return cls(
            theme=Theme.from_dict(data["theme"]),
            source_ir=IntermediateRepresentation.from_dict(data["source_ir"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            analysis=VideoAnalysis.from_dict(analysis) if analysis is not None else None,
            screen_texts=tuple(ScreenText.from_dict(t) for t in data.get("screen_texts", ())),
            script=SpokenScript.from_dict(script) if script is not None else None,
            evidence=ScriptEvidence.from_dict(evidence) if evidence is not None else None,
            shortform=dict(data.get("shortform") or {}),
            notes=tuple(str(n) for n in data.get("notes", ())),
            detail=dict(data.get("detail") or {}),
        )


class ThemeRepository(ABC):
    """계약: save 는 같은 theme_id 의 기록을 덮어쓴다. get 은 없으면 None. list 는 만든 순서."""

    @abstractmethod
    def save(self, record: ThemeRecord) -> None: ...

    @abstractmethod
    def get(self, theme_id: str) -> ThemeRecord | None: ...

    @abstractmethod
    def list(self) -> list[ThemeRecord]: ...


def theme_lock_key(theme_id: str) -> str:
    """테마의 키 (EpisodeRepository.lock 에 준다).

    그 테마의 편 순번을 매기는 유스케이스와, 테마 문서를 읽고 → 고쳐 → 덮어쓰는 유스케이스가 쥔다.
    키를 한 곳에서 지어, 같은 테마를 다루는 유스케이스가 서로 다른 키를 쥐는 일이 없게 한다 (감사 BTS-D38:
    한쪽은 "<id>", 다른 쪽은 "theme:<id>" 를 쥐었고 기준 이미지 채택은 아무 키도 쥐지 않았다).
    """
    return f"theme:{theme_id}"


class EpisodeRepository(ABC):
    """계약
    - save 는 같은 episode_id 의 편을 덮어쓴다. get 은 없으면 None. list_by_theme 은 순번 순서.
    - lock(key) 는 그 키를 쥐는 동안 같은 키의 다른 스레드를 기다리게 한다. 같은 스레드는 다시 들어갈 수 있다.
      편 문서는 읽고 → 고쳐 → 통째로 덮어쓰므로, 유스케이스는 편 id 를 쥐고 그 사이를 지난다.
      그러지 않으면 오래 걸리는 합성·인식 도중에 다른 유스케이스가 한 수정이 지워진다 (감사 BTS-24).
      편의 순번은 테마의 키(theme_lock_key)를 쥐고 매긴다. 테마 문서를 읽고 → 고쳐 → 덮어쓰는 유스케이스
      (기준 이미지 채택 · 원본 대본 교정)도 같은 키를 쥔다. 한 프로세스 안의 잠금이다.
    """

    @abstractmethod
    def lock(self, key: str) -> AbstractContextManager[None]: ...

    @abstractmethod
    def save(self, episode: Episode) -> None: ...

    @abstractmethod
    def get(self, episode_id: str) -> Episode | None: ...

    @abstractmethod
    def list_by_theme(self, theme_id: str) -> list[Episode]: ...
