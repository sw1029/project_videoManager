"""테마·편 저장 포트 (ADR-0025·0026). 구현: 메모리(테스트), 산출물 저장소의 JSON 문서.

테마와 편은 프로젝트·버전(관계형 저장소)과 달리 문서 하나로 읽고 쓴다. 편은 프로젝트·버전을 id 로 가리킬 뿐이다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from frame.domain.ir import IntermediateRepresentation
from frame.domain.series import Episode
from frame.domain.theme import Theme
from frame.domain.video_analysis import ScreenText, VideoAnalysis


@dataclass(frozen=True)
class ThemeRecord:
    """테마와 그것을 뽑을 때의 근거: 원본 IR, 관찰값, 화면에서 읽은 문구, 숏폼 규격 판정."""

    theme: Theme
    source_ir: IntermediateRepresentation
    created_at: datetime
    analysis: VideoAnalysis | None = None
    screen_texts: tuple[ScreenText, ...] = ()
    shortform: Mapping[str, Any] = field(default_factory=dict)  # ShortformVerdict.to_dict()
    notes: tuple[str, ...] = ()
    detail: Mapping[str, Any] = field(default_factory=dict)  # 추출 방법·모델·토큰 수

    def to_dict(self) -> dict[str, Any]:
        return {
            "theme": self.theme.to_dict(),
            "source_ir": self.source_ir.to_dict(),
            "created_at": self.created_at.isoformat(),
            "analysis": self.analysis.to_dict() if self.analysis is not None else None,
            "screen_texts": [t.to_dict() for t in self.screen_texts],
            "shortform": dict(self.shortform),
            "notes": list(self.notes),
            "detail": dict(self.detail),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ThemeRecord:
        analysis = data.get("analysis")
        return cls(
            theme=Theme.from_dict(data["theme"]),
            source_ir=IntermediateRepresentation.from_dict(data["source_ir"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            analysis=VideoAnalysis.from_dict(analysis) if analysis is not None else None,
            screen_texts=tuple(ScreenText.from_dict(t) for t in data.get("screen_texts", ())),
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


class EpisodeRepository(ABC):
    """계약: save 는 같은 episode_id 의 편을 덮어쓴다. get 은 없으면 None. list_by_theme 은 순번 순서."""

    @abstractmethod
    def save(self, episode: Episode) -> None: ...

    @abstractmethod
    def get(self, episode_id: str) -> Episode | None: ...

    @abstractmethod
    def list_by_theme(self, theme_id: str) -> list[Episode]: ...
