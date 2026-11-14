"""산출물 저장소(ArtifactStore)에 JSON 문서로 두는 테마·편 저장소 (ADR-0025).

- themes/<theme_id>/theme.json, episodes/<episode_id>/episode.json
- 목록은 색인 문서(themes/index.json, episodes/index.json)로 찾는다. ArtifactStore 에는 '목록 보기' 가 없기 때문이다.
- 키로 URI 를 미리 알 수 있는 저장소(메모리·로컬 파일)에서만 쓴다. 그렇지 않으면 기동 때 ConfigurationError.
- 서버 프로세스 하나가 쓰는 것을 전제로 한다 (색인 갱신과 편의 키 잠금은 프로세스 안에서만 지킨다).
- id 는 소문자·숫자·밑줄·붙임표만 받는다. 점을 받으면 'index.json' 이 색인 파일과 부딪히고, 대소문자를 가리지 않는
  파일 시스템에서는 'THM_A' 와 'thm_a' 가 같은 문서를 연다 (감사 BTS-14).
- 색인이 목록이 아니면 오류로 알린다 (빈 목록으로 읽으면 다음 저장이 색인을 덮어써 다른 문서가 목록에서 사라진다).
  문서 하나가 깨졌으면 그 문서만 건너뛰고 기록한다.
  관계형 저장소로 옮기는 것은 뒤의 일이다 (새 테이블이 필요하다).
"""

from __future__ import annotations

import json
import logging
import re
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any, TypeVar

from frame.adapters.series.store._key_locks import KeyLocks
from frame.application.errors import ConfigurationError, StorageError
from frame.application.ports.artifact_store import ArtifactStore
from frame.application.ports.series.repositories import (
    EpisodeRepository,
    ThemeRecord,
    ThemeRepository,
)
from frame.domain.errors import DomainError, ValidationError
from frame.domain.series.record import Episode

log = logging.getLogger(__name__)
T = TypeVar("T")

_SAFE_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_LOCK = threading.Lock()
# 저장된 문서를 타입으로 옮기다 나는 오류 (손으로 고쳤거나 잘린 문서)
_BROKEN = (DomainError, KeyError, TypeError, ValueError, AttributeError)


def check_document_id(doc_id: str) -> str:
    """저장할 수 있는 id 인지 본다 (테마·편). 아니면 ValidationError. 메모리 저장소도 같은 규칙을 쓴다."""
    if not _SAFE_ID.fullmatch(doc_id):  # match 는 줄 끝의 \n 앞에서도 맞는다
        raise ValidationError(
            f"저장할 수 없는 id 입니다: {doc_id!r} (소문자·숫자·밑줄·붙임표, 64자까지)"
        )
    return doc_id


class _Documents:
    """한 종류의 문서 묶음: <folder>/<id>/<name>.json 과 <folder>/index.json."""

    def __init__(self, store: ArtifactStore, folder: str, name: str) -> None:
        if store.uri_for(f"{folder}/index.json") is None:
            raise ConfigurationError(
                "테마 저장소는 키로 URI 를 알 수 있는 산출물 저장소(memory·local_fs)가 필요합니다"
            )
        self._store = store
        self._folder = folder
        self._name = name

    def _key(self, doc_id: str) -> str:
        return f"{self._folder}/{check_document_id(doc_id)}/{self._name}.json"

    def parsed(self, doc_id: str, parse: Callable[[dict[str, Any]], T]) -> T | None:
        """문서 하나를 읽어 parse 로 옮긴다. 없으면 None, 깨졌으면 StorageError (감사 BTS-14 의 남은 것).

        깨진 문서에 이름 없는 KeyError 를 그대로 내지 않는다. 목록(all)은 깨진 문서를 건너뛴다.
        """
        document = self.get(doc_id)
        if document is None:
            return None
        try:
            return parse(document)
        except _BROKEN as exc:
            raise StorageError(
                f"저장된 문서가 깨졌습니다: {self._folder}/{doc_id} ({type(exc).__name__})"
            ) from exc

    def _read(self, key: str) -> Any | None:
        uri = self._store.uri_for(key)
        if uri is None or not self._store.exists(uri):
            return None
        try:
            return json.loads(self._store.get(uri).decode("utf-8"))
        except (StorageError, ValueError) as exc:
            raise StorageError(f"문서를 읽지 못했습니다: {key} ({exc})") from exc

    def _write(self, key: str, document: Any) -> None:
        data = json.dumps(document, ensure_ascii=False, sort_keys=True, indent=1).encode("utf-8")
        self._store.put(key, data, "application/json")

    def ids(self) -> list[str]:
        key = f"{self._folder}/index.json"
        index = self._read(key)
        if index is None:
            return []
        if not isinstance(index, list):
            raise StorageError(f"색인 문서가 목록이 아닙니다: {key}")
        return [str(i) for i in index]

    def save(self, doc_id: str, document: dict[str, Any]) -> None:
        key = self._key(doc_id)
        with _LOCK:
            self._write(key, document)
            ids = self.ids()
            if doc_id not in ids:
                self._write(f"{self._folder}/index.json", [*ids, doc_id])

    def get(self, doc_id: str) -> dict[str, Any] | None:
        document = self._read(self._key(doc_id))
        return document if isinstance(document, dict) else None

    def all(self, parse: Callable[[dict[str, Any]], T]) -> list[T]:
        """색인에 있는 문서를 모두 읽어 parse 로 옮긴다. 깨진 문서는 건너뛰고 기록한다."""
        parsed: list[T] = []
        for doc_id in self.ids():
            try:
                document = self.get(doc_id)
                if document is not None:
                    parsed.append(parse(document))
            except (StorageError, DomainError, KeyError, TypeError, ValueError) as exc:
                log.warning(
                    "깨진 문서를 건너뜁니다: %s/%s (%s)", self._folder, doc_id, type(exc).__name__
                )
        return parsed


class ArtifactThemeRepository(ThemeRepository):
    def __init__(self, store: ArtifactStore) -> None:
        self._documents = _Documents(store, "themes", "theme")

    def save(self, record: ThemeRecord) -> None:
        self._documents.save(record.theme.theme_id, record.to_dict())

    def get(self, theme_id: str) -> ThemeRecord | None:
        return self._documents.parsed(theme_id, ThemeRecord.from_dict)

    def list(self) -> list[ThemeRecord]:
        records = self._documents.all(ThemeRecord.from_dict)
        return sorted(records, key=lambda r: (r.created_at, r.theme.theme_id))


class ArtifactEpisodeRepository(EpisodeRepository):
    def __init__(self, store: ArtifactStore) -> None:
        self._documents = _Documents(store, "episodes", "episode")
        self._locks = KeyLocks()

    def lock(self, key: str) -> AbstractContextManager[None]:
        return self._locks.hold(key)

    def save(self, episode: Episode) -> None:
        self._documents.save(episode.episode_id, episode.to_dict())

    def get(self, episode_id: str) -> Episode | None:
        return self._documents.parsed(episode_id, Episode.from_dict)

    def list_by_theme(self, theme_id: str) -> list[Episode]:
        episodes = self._documents.all(Episode.from_dict)
        return sorted(
            (e for e in episodes if e.theme_id == theme_id), key=lambda e: (e.index, e.episode_id)
        )
