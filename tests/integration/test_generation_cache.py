"""#7 생성 결과 캐시 영속화: 컨테이너를 다시 조립(재시작)해도 같은 조건의 장면은 다시 생성하지 않는다."""

from __future__ import annotations

from frame.adapters.common import UuidIdGenerator
from frame.adapters.generator import FakeVideoGenerator
from frame.adapters.storage import LocalFileArtifactStore
from frame.config import Settings
from frame.domain.enums import VersionState
from tests.support.factories import create_draft, generate, make_container


def _settings(artifact_dir) -> Settings:
    # 캐시는 기본으로 켜져 있다. 산출물은 파일에 남지만 DB 는 메모리라 재시작하면 버전 기록은 사라진다
    return Settings(jobs="recording", artifact_store="local_fs", artifact_dir=str(artifact_dir))


def test_same_conditions_are_not_regenerated_after_restart(tmp_path):
    store = LocalFileArtifactStore(tmp_path)
    first_generator = FakeVideoGenerator(store)
    first = make_container(_settings(tmp_path), artifact_store=store, generator=first_generator)
    before = generate(first, create_draft(first).version_id)
    assert before.state is VersionState.COMPLETED and len(first_generator.requests) == 3

    store_after = LocalFileArtifactStore(tmp_path)
    second_generator = FakeVideoGenerator(store_after)
    second = make_container(
        _settings(tmp_path),
        artifact_store=store_after,
        generator=second_generator,
        ids=UuidIdGenerator(),
    )
    after = generate(second, create_draft(second).version_id)

    assert after.state is VersionState.COMPLETED
    assert second_generator.requests == []  # 세 장면 모두 캐시 적중
    assert {k: c.uri for k, c in after.clips.items()} == {k: c.uri for k, c in before.clips.items()}
    assert all(store_after.exists(c.uri) for c in after.clips.values())


def test_changed_scene_is_generated_and_the_rest_come_from_the_cache(tmp_path):
    store = LocalFileArtifactStore(tmp_path)
    first = make_container(_settings(tmp_path), artifact_store=store)
    generate(first, create_draft(first).version_id)

    store_after = LocalFileArtifactStore(tmp_path)
    spy = FakeVideoGenerator(store_after)
    second = make_container(
        _settings(tmp_path), artifact_store=store_after, generator=spy, ids=UuidIdGenerator()
    )
    prompt = (
        "scene intro 0-4000 : 바리스타가 카운터에서 컵을 들어 보인다\n"
        "scene closeup 4000-8000 : 컵 클로즈업\n"
        "scene outro 8000-12000 : 바리스타가 손을 흔든다\n"
        "entity barista PERSON : 흰 셔츠를 입은 바리스타\n"
        "entity cup PROP : 세라믹 머그컵\n"
        "entity cafe BACKGROUND : 따뜻한 조명의 카페\n"
        "in intro: barista, cup, cafe\n"
        "in closeup: cup, cafe\n"
        "in outro: barista, cafe\n"
        "cup.color = teal\n"  # 컵이 나오는 intro·closeup 만 조건이 바뀐다
        "barista.shirt_color = white\n"
        "barista holds -> cup @ intro\n"
        "cafe.lighting = warm\n"
    )
    after = generate(second, create_draft(second, prompt=prompt).version_id)
    assert after.state is VersionState.COMPLETED
    assert sorted(r.scene.scene_key for r in spy.requests) == ["closeup", "intro"]
