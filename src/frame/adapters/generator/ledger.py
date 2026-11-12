"""외부 생성 작업 원장 (#2, ADR-0013): 제출한 작업 id 를 폴링 전에 남겨 재시작 뒤 이어 받는다.

- 키는 생성 캐시와 같은 generation_key(fingerprint, 생성기 프로필)이다. 같은 조건·같은 설정의 장면은
  원장에 쓸 수 있는 작업이 있으면 다시 제출(과금)하지 않고 그 작업을 이어서 폴링·다운로드한다.
- 상태: submitted(접수됨, 결과 미회수) -> completed(클립 저장) | failed(작업이 실패·만료돼 이어 받을 수 없음).
  만료 규칙(TTL)은 원장을 쓰는 어댑터가 정한다 (OpenRouter: 제출 뒤 24시간).
- ArtifactJobLedger 는 ArtifactStore 의 ledger/<이름공간>/<키>.json 에 둔다. 클립과 같은 저장소라 함께 지워지고
  local_fs 면 재시작해도 남는다. 키·서명 URL 같은 비밀은 넣지 않는다 (작업 id·요청 정보·클립 메타데이터만).
"""

from __future__ import annotations

import json
import logging
import threading
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from frame.application.ports.artifact_store import ArtifactStore

log = logging.getLogger(__name__)

LEDGER_PREFIX = "ledger"
_SCHEMA = 1

SUBMITTED = "submitted"
COMPLETED = "completed"
FAILED = "failed"
_STATUSES = frozenset({SUBMITTED, COMPLETED, FAILED})


@dataclass(frozen=True)
class JobRecord:
    key: str
    job_id: str
    status: str
    submitted_at: float  # 벽시계(epoch 초). 재시작을 넘어 비교해야 하므로 monotonic 이 아니다
    version_id: str
    scene_key: str
    profile: Mapping[str, object] = field(default_factory=dict)
    details: Mapping[str, object] = field(
        default_factory=dict
    )  # 제출 때 정한 값 (과금 초, seed, 프롬프트 등)
    clip_uri: str | None = None
    clip_metadata: Mapping[str, object] = field(default_factory=dict)
    error: str | None = None
    updated_at: float | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "schema": _SCHEMA,
            "key": self.key,
            "job_id": self.job_id,
            "status": self.status,
            "submitted_at": self.submitted_at,
            "updated_at": self.updated_at,
            "version_id": self.version_id,
            "scene_key": self.scene_key,
            "profile": dict(self.profile),
            "details": dict(self.details),
            "clip_uri": self.clip_uri,
            "clip_metadata": dict(self.clip_metadata),
            "error": self.error,
        }

    @classmethod
    def from_json(cls, data: Any) -> JobRecord:
        if not isinstance(data, dict) or data.get("schema") != _SCHEMA:
            raise ValueError("알 수 없는 원장 형식")
        status = str(data["status"])
        if status not in _STATUSES:
            raise ValueError(f"알 수 없는 원장 상태: {status}")
        return cls(
            key=str(data["key"]),
            job_id=str(data["job_id"]),
            status=status,
            submitted_at=float(data["submitted_at"]),
            version_id=str(data["version_id"]),
            scene_key=str(data["scene_key"]),
            profile=dict(data.get("profile") or {}),
            details=dict(data.get("details") or {}),
            clip_uri=data.get("clip_uri"),
            clip_metadata=dict(data.get("clip_metadata") or {}),
            error=data.get("error"),
            updated_at=data.get("updated_at"),
        )


class JobLedger(ABC):
    @abstractmethod
    def get(self, key: str) -> JobRecord | None:
        """없으면 None. 읽을 수 없는 항목은 ValueError·StorageError 등으로 알린다 (쓰는 쪽이 판단)."""

    @abstractmethod
    def put(self, record: JobRecord) -> None: ...


class InMemoryJobLedger(JobLedger):
    """프로세스 메모리 원장 (키로 URI 를 계산할 수 없는 저장소의 대안, 재시작하면 사라진다)."""

    def __init__(self) -> None:
        self._records: dict[str, JobRecord] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> JobRecord | None:
        with self._lock:
            return self._records.get(key)

    def put(self, record: JobRecord) -> None:
        with self._lock:
            self._records[record.key] = record


class ArtifactJobLedger(JobLedger):
    def __init__(self, store: ArtifactStore, namespace: str) -> None:
        if store.uri_for(f"{LEDGER_PREFIX}/{namespace}/probe.json") is None:
            raise ValueError(
                "ArtifactJobLedger 는 키로 URI 를 계산하는 저장소(uri_for)가 필요합니다"
            )
        self._store = store
        self._namespace = namespace.strip("/")

    def get(self, key: str) -> JobRecord | None:
        uri = self._uri(key)
        if not self._store.exists(uri):
            return None
        record = JobRecord.from_json(json.loads(self._store.get(uri).decode("utf-8")))
        if record.key != key:
            raise ValueError(f"원장 항목의 키가 파일 이름과 다릅니다: {uri}")
        return record

    def put(self, record: JobRecord) -> None:
        data = json.dumps(record.to_json(), ensure_ascii=False, sort_keys=True, default=str)
        self._store.put(self._path(record.key), data.encode("utf-8"), "application/json")

    def _path(self, key: str) -> str:
        return f"{LEDGER_PREFIX}/{self._namespace}/{key}.json"

    def _uri(self, key: str) -> str:
        uri = self._store.uri_for(self._path(key))
        assert uri is not None  # 생성자에서 확인했다
        return uri


def build_job_ledger(store: ArtifactStore, namespace: str = "openrouter") -> JobLedger:
    """저장소가 키로 URI 를 계산할 수 있으면 영속 원장, 아니면 프로세스 메모리 원장."""
    if store.uri_for(f"{LEDGER_PREFIX}/{namespace}/probe.json") is not None:
        return ArtifactJobLedger(store, namespace)
    log.warning(
        "산출물 저장소가 uri_for 를 지원하지 않아 작업 원장을 프로세스 메모리에만 둡니다 "
        "(재시작하면 진행 중인 외부 작업을 이어 받지 못합니다)"
    )
    return InMemoryJobLedger()
