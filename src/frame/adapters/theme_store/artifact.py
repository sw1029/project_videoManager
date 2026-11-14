"""산출물 저장소(ArtifactStore)에 JSON 문서로 두는 테마·편 저장소 (ADR-0025).

- themes/<theme_id>/theme.json, episodes/<episode_id>/episode.json
- 목록은 색인 문서(themes/index.json, episodes/index.json)로 찾는다. ArtifactStore 에는 '목록 보기' 가 없기 때문이다.
- 키로 URI 를 미리 알 수 있는 저장소(메모리·로컬 파일)에서만 쓴다. 그렇지 않으면 기동 때 ConfigurationError.
- 서버 프로세스 하나가 쓰는 것을 전제로 한다 (색인 갱신은 프로세스 안의 잠금으로만 지킨다).
  관계형 저장소로 옮기는 것은 뒤의 일이다 (새 테이블이 필요하다).
"""

from __future__ import annotations

import json
import re
import threading
from typing import Any

from frame.application.errors import ConfigurationError, StorageError
from frame.application.ports.artifact_store import ArtifactStore
from frame.application.ports.theme_store import EpisodeRepository, ThemeRecord, ThemeRepository
from frame.domain.errors import ValidationError
from frame.domain.series import Episode

_SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]+$")
_LOCK = threading.Lock()


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
        if not _SAFE_ID.match(doc_id) or doc_id in {".", ".."}:
            raise ValidationError(f"저장할 수 없는 id 입니다: '{doc_id}'")
        return f"{self._folder}/{doc_id}/{self._name}.json"

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
        index = self._read(f"{self._folder}/index.json")
        return [str(i) for i in index] if isinstance(index, list) else []

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

    def all(self) -> list[dict[str, Any]]:
        found = (self.get(doc_id) for doc_id in self.ids())
        return [document for document in found if document is not None]


class ArtifactThemeRepository(ThemeRepository):
    def __init__(self, store: ArtifactStore) -> None:
        self._documents = _Documents(store, "themes", "theme")

    def save(self, record: ThemeRecord) -> None:
        self._documents.save(record.theme.theme_id, record.to_dict())

    def get(self, theme_id: str) -> ThemeRecord | None:
        document = self._documents.get(theme_id)
        return ThemeRecord.from_dict(document) if document is not None else None

    def list(self) -> list[ThemeRecord]:
        records = [ThemeRecord.from_dict(d) for d in self._documents.all()]
        return sorted(records, key=lambda r: (r.created_at, r.theme.theme_id))


class ArtifactEpisodeRepository(EpisodeRepository):
    def __init__(self, store: ArtifactStore) -> None:
        self._documents = _Documents(store, "episodes", "episode")

    def save(self, episode: Episode) -> None:
        self._documents.save(episode.episode_id, episode.to_dict())

    def get(self, episode_id: str) -> Episode | None:
        document = self._documents.get(episode_id)
        return Episode.from_dict(document) if document is not None else None

    def list_by_theme(self, theme_id: str) -> list[Episode]:
        episodes = [Episode.from_dict(d) for d in self._documents.all()]
        return sorted(
            (e for e in episodes if e.theme_id == theme_id), key=lambda e: (e.index, e.episode_id)
        )
