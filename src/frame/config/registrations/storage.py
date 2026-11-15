"""산출물 저장 (ArtifactStore) 의 등록."""

from __future__ import annotations

from frame.adapters.storage import InMemoryArtifactStore, LocalFileArtifactStore
from frame.application.ports import ArtifactStore

from ..registry import AdapterRegistry


def register(registry: AdapterRegistry) -> None:
    registry.register(ArtifactStore, "memory", lambda ctx: InMemoryArtifactStore())
    registry.register(
        ArtifactStore, "local_fs", lambda ctx: LocalFileArtifactStore(ctx.settings.artifact_dir)
    )
