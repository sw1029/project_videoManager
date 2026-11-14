"""메모리 테마·편 저장소 (테스트용). 저장할 때와 읽을 때 문서로 한 번 옮겨, 직렬화가 빠뜨리는 것을 드러낸다."""

from __future__ import annotations

import json
from contextlib import AbstractContextManager
from typing import Any

from frame.adapters.series.store.key_locks import KeyLocks
from frame.application.ports.series.repositories import (
    EpisodeRepository,
    ThemeRecord,
    ThemeRepository,
)
from frame.domain.series.record import Episode


def _copy(document: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(document, ensure_ascii=False))


class InMemoryThemeRepository(ThemeRepository):
    def __init__(self) -> None:
        self._documents: dict[str, dict[str, Any]] = {}

    def save(self, record: ThemeRecord) -> None:
        self._documents[record.theme.theme_id] = _copy(record.to_dict())

    def get(self, theme_id: str) -> ThemeRecord | None:
        document = self._documents.get(theme_id)
        return ThemeRecord.from_dict(document) if document is not None else None

    def list(self) -> list[ThemeRecord]:
        records = [ThemeRecord.from_dict(d) for d in self._documents.values()]
        return sorted(records, key=lambda r: (r.created_at, r.theme.theme_id))


class InMemoryEpisodeRepository(EpisodeRepository):
    def __init__(self) -> None:
        self._documents: dict[str, dict[str, Any]] = {}
        self._locks = KeyLocks()

    def lock(self, key: str) -> AbstractContextManager[None]:
        return self._locks.hold(key)

    def save(self, episode: Episode) -> None:
        self._documents[episode.episode_id] = _copy(episode.to_dict())

    def get(self, episode_id: str) -> Episode | None:
        document = self._documents.get(episode_id)
        return Episode.from_dict(document) if document is not None else None

    def list_by_theme(self, theme_id: str) -> list[Episode]:
        episodes = [Episode.from_dict(d) for d in self._documents.values()]
        return sorted(
            (e for e in episodes if e.theme_id == theme_id), key=lambda e: (e.index, e.episode_id)
        )
