"""정체성 기준 이미지와 재시작 복구 (ADR-0015 + ADR-0013), SQLite 파일 DB.

생성 도중 프로세스가 죽어도 그때까지 만든 클립과 거기서 뽑은 기준 이미지는 함께 남는다.
다시 실행하면 이미 만든 장면은 다시 만들지 않고, 남은 장면은 같은 기준 이미지로 만든다.
"""

from __future__ import annotations

import pytest

from frame.adapters.common import UuidIdGenerator
from frame.adapters.generator import FakeVideoGenerator
from frame.adapters.identity import FakeIdentityExtractor
from frame.adapters.storage import LocalFileArtifactStore
from frame.application.dto import CreateRevisionCommand
from frame.config import Settings
from frame.domain.enums import ReferenceOrigin, VersionState
from frame.domain.services.propagation import AttributeChange
from tests.support.factories import (
    create_draft,
    dispatcher_of,
    generate,
    make_container,
    version_of,
)


class ProcessDied(BaseException):
    """작업 도중 프로세스가 죽었다 (어떤 처리기도 잡지 않는다)."""


class DiesAt(FakeVideoGenerator):
    def __init__(self, store, scene_key):
        super().__init__(store)
        self._dies_at = scene_key

    def generate_scene(self, request):
        if request.scene.scene_key == self._dies_at:
            raise ProcessDied
        return super().generate_scene(request)


def _settings(tmp_path) -> Settings:
    return Settings(
        jobs="recording",
        persistence="sqlalchemy",
        db_url=f"sqlite+pysqlite:///{(tmp_path / 'frame.db').as_posix()}",
        artifact_store="local_fs",
        artifact_dir=str(tmp_path / "artifacts"),
        generator_cache=False,  # 캐시 없이도 다시 만들지 않는지 본다
    )


def _container(tmp_path, generator_factory, **overrides):
    store = LocalFileArtifactStore(tmp_path / "artifacts")
    generator = generator_factory(store)
    extractor = FakeIdentityExtractor(store)
    container = make_container(
        _settings(tmp_path),
        artifact_store=store,
        generator=generator,
        identity_extractor=extractor,
        **overrides,
    )
    return container, generator, extractor


def _anchors(container, version_id):
    refs = container.queries.get_version(version_id).reference_images
    return sorted(
        (r.entity_key, r.source_scene_key) for r in refs if r.origin is ReferenceOrigin.DERIVED
    )


def test_rerun_after_a_crash_keeps_finished_scenes_and_their_anchors(tmp_path):
    before, _, _ = _container(tmp_path, lambda store: DiesAt(store, "outro"))
    draft = create_draft(before)
    before.accept_generation.execute(draft.version_id)
    with pytest.raises(ProcessDied):
        dispatcher_of(before).run_all()

    crashed = version_of(before, draft.version_id)
    assert crashed.state is VersionState.PROCESSING
    assert set(crashed.clips) == {"intro", "closeup"}
    # 클립과 같은 트랜잭션으로 남았다
    assert _anchors(before, draft.version_id) == [("barista", "intro"), ("cup", "intro")]

    after, generator, extractor = _container(tmp_path, FakeVideoGenerator, ids=UuidIdGenerator())
    report = after.recover_pending_versions.execute(startup=True)
    assert report.requeued == (draft.version_id,)
    dispatcher_of(after).run_all()

    version = version_of(after, draft.version_id)
    assert version.state is VersionState.COMPLETED and version.attempt == 2
    assert [r.scene.scene_key for r in generator.requests] == ["outro"]  # 끝난 장면은 그대로
    assert version.clips["intro"].uri == crashed.clips["intro"].uri
    (outro,) = generator.requests
    assert [r.entity_key for r in outro.identity_references] == ["barista"]
    assert extractor.requests == []  # 기준은 이미 있다
    assert _anchors(after, draft.version_id) == [("barista", "intro"), ("cup", "intro")]


def test_anchors_survive_a_database_round_trip_into_a_revision(tmp_path):
    first, _, _ = _container(tmp_path, FakeVideoGenerator)
    draft = create_draft(first)
    original = generate(first, draft.version_id)

    # 새 프로세스: 메모리에 남은 것이 없다
    second, generator, _ = _container(tmp_path, FakeVideoGenerator, ids=UuidIdGenerator())
    revision = second.create_revision.execute(
        CreateRevisionCommand(draft.version_id, AttributeChange("cup", "color", "teal"))
    )
    revised = generate(second, revision.version_id)

    assert revised.state is VersionState.COMPLETED
    assert sorted(r.scene.scene_key for r in generator.requests) == ["closeup", "intro"]
    assert revised.clips["outro"].uri == original.clips["outro"].uri
    intro = next(r for r in generator.requests if r.scene.scene_key == "intro")
    assert [r.entity_key for r in intro.identity_references] == ["barista"]
    assert intro.parent is not None and intro.parent.clip.uri == original.clips["intro"].uri
