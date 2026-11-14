"""장면 사이 객체 일관성 (US-03, ADR-0015): 정체성 기준 이미지를 뽑고, 물려주고, 다른 장면 생성에 넘긴다.

카페 예시: intro(바리스타·컵·카페) -> closeup(컵·카페) -> outro(바리스타·카페).
"""

import pytest

from frame.adapters.generator import FakeVideoGenerator
from frame.adapters.identity import FakeIdentityExtractor
from frame.adapters.storage import InMemoryArtifactStore
from frame.application.dto import CreateRevisionCommand
from frame.application.errors import ConfigurationError
from frame.application.ports.structurer import ReferenceImageInput
from frame.config import Settings
from frame.domain.enums import ChangeScope, ReferenceOrigin, VersionState
from frame.domain.services.propagation import AttributeChange
from tests.support.factories import create_draft, generate, make_container


class Rig:
    """생성기·추출기 스파이를 꽂은 컨테이너."""

    def __init__(self, *, identity: bool = True, settings: Settings | None = None, **extractor):
        self.store = InMemoryArtifactStore()
        self.generator = FakeVideoGenerator(self.store)
        self.extractor = FakeIdentityExtractor(self.store, **extractor) if identity else None
        self.container = make_container(
            settings,
            artifact_store=self.store,
            generator=self.generator,
            identity_extractor=self.extractor,
        )

    def without_identity(self) -> "Rig":
        """같은 저장소·DB 를 쓰되 기준 이미지 기능이 꺼진 컨테이너 (기능을 켜기 전에 만든 버전 흉내)."""
        other = Rig.__new__(Rig)
        other.store, other.generator, other.extractor = self.store, self.generator, None
        other.container = make_container(
            artifact_store=self.store,
            generator=self.generator,
            identity_extractor=None,
            uow_factory=self.container.uow_factory,
            ids=self.container.ids,
            clock=self.container.clock,
        )
        return other

    def original(self, references=()):
        draft = create_draft(self.container, references=references)
        return generate(self.container, draft.version_id)

    def revise(self, version_id, change):
        revision = self.container.create_revision.execute(CreateRevisionCommand(version_id, change))
        self.generator.requests.clear()
        if self.extractor is not None:
            self.extractor.requests.clear()
        return generate(self.container, revision.version_id)

    def requests(self):
        """장면 키 -> 그 장면 생성 요청."""
        return {r.scene.scene_key: r for r in self.generator.requests}

    def identity_of(self, scene_key):
        return sorted(r.entity_key for r in self.requests()[scene_key].identity_references)

    def anchors(self, version_id):
        refs = self.container.queries.get_version(version_id).reference_images
        return [r for r in refs if r.origin is ReferenceOrigin.DERIVED]

    def extracted(self):
        """(장면, 뽑아 달라고 한 객체들) 목록."""
        return [
            (r.scene.scene_key, tuple(e.entity_key for e in r.entities))
            for r in self.extractor.requests
        ]


def test_identity_references_are_off_by_default(container, draft):
    version = generate(container, draft.version_id)

    assert version.state is VersionState.COMPLETED
    assert container.identity_extractor is None
    assert container.queries.get_version(draft.version_id).reference_images == ()


@pytest.mark.ac("AC-31")
def test_first_appearance_anchors_the_entity_in_later_scenes():
    rig = Rig()
    version = rig.original()

    assert version.state is VersionState.COMPLETED
    assert rig.identity_of("intro") == []  # 처음 나오는 장면은 기준 없이 만든다
    assert rig.identity_of("closeup") == ["cup"]
    assert rig.identity_of("outro") == ["barista"]
    assert rig.extracted() == [("intro", ("barista", "cup"))]  # 한 번만 뽑는다

    anchors = {a.entity_key: a for a in rig.anchors(version.version_id)}
    assert set(anchors) == {"barista", "cup"}
    for anchor in anchors.values():
        assert anchor.source_scene_key == "intro"
        assert anchor.source_fingerprint == version.clips["intro"].fingerprint
        assert anchor.appearance_fingerprint
        assert rig.store.exists(anchor.image_uri)
    sent = {r.entity_key: r.image_uri for r in rig.requests()["outro"].identity_references}
    assert sent == {"barista": anchors["barista"].image_uri}


def test_anchors_do_not_change_the_condition_fingerprint_of_a_clip():
    with_identity, plain = Rig(), Rig(identity=False)
    anchored, baseline = with_identity.original(), plain.original()

    assert {k: c.fingerprint for k, c in anchored.clips.items()} == {
        k: c.fingerprint for k, c in baseline.clips.items()
    }
    outro = with_identity.requests()["outro"]
    assert outro.generation_fingerprint != outro.fingerprint  # 생성 키만 달라진다
    assert plain.requests()["outro"].generation_fingerprint == plain.requests()["outro"].fingerprint


@pytest.mark.ac("AC-01")
def test_revision_reuses_untouched_scenes_and_carries_identity_into_regenerated_ones():
    rig = Rig()
    original = rig.original()
    old = {a.entity_key: a for a in rig.anchors(original.version_id)}

    revised = rig.revise(original.version_id, AttributeChange("cup", "color", "teal"))

    assert revised.state is VersionState.COMPLETED
    assert sorted(rig.requests()) == ["closeup", "intro"]  # outro 는 다시 만들지 않는다
    assert revised.clips["outro"].uri == original.clips["outro"].uri
    # 다시 만드는 intro 는 부모와 같은 바리스타를 이어 가고, 색이 바뀐 컵의 옛 기준은 넘기지 않는다
    assert rig.identity_of("intro") == ["barista"]
    intro_refs = rig.requests()["intro"].identity_references
    assert [r.image_uri for r in intro_refs] == [old["barista"].image_uri]
    # 새 컵 기준은 새 intro 에서 뽑아 closeup 에 넘긴다
    assert rig.extracted() == [("intro", ("cup",))]
    closeup_refs = rig.requests()["closeup"].identity_references
    assert [r.entity_key for r in closeup_refs] == ["cup"]
    assert closeup_refs[0].image_uri != old["cup"].image_uri

    cups = [a for a in rig.anchors(revised.version_id) if a.entity_key == "cup"]
    assert len(cups) == 2  # 물려받은 옛 기준 + 새 기준 (옛 기준은 이 버전에서 무효)
    assert revised.clips["intro"].fingerprint in {c.source_fingerprint for c in cups}


def test_scene_limited_change_keeps_every_other_scene_reusable():
    rig = Rig()
    original = rig.original()

    revised = rig.revise(
        original.version_id,
        AttributeChange("cup", "color", "blue", ChangeScope.SCENES, ("closeup",)),
    )

    assert list(rig.requests()) == ["closeup"]
    assert rig.identity_of("closeup") == []  # 빨간 컵 기준은 파란 컵 장면과 어긋나 넘기지 않는다
    assert revised.clips["intro"].uri == original.clips["intro"].uri
    assert revised.clips["outro"].uri == original.clips["outro"].uri


def test_grandchild_revision_still_reuses_scenes_regenerated_with_anchors():
    rig = Rig()
    original = rig.original()
    child = rig.revise(original.version_id, AttributeChange("cup", "color", "teal"))

    grandchild = rig.revise(child.version_id, AttributeChange("barista", "action", "waves"))

    # 바리스타의 행동만 바뀌었다: 바리스타가 나오는 intro·outro 만 다시 만든다
    assert sorted(rig.requests()) == ["intro", "outro"]
    assert grandchild.clips["closeup"].uri == child.clips["closeup"].uri
    assert rig.identity_of("outro") == ["barista"]
    assert rig.identity_of("intro") == ["barista", "cup"]  # 외형은 그대로라 기준이 계속 유효하다
    assert rig.extracted() == []


def test_revision_of_a_version_made_without_anchors_takes_them_from_existing_clips():
    """기능을 켜기 전에 만든 버전을 수정: 재사용할 장면과 부모 클립에서 기준을 먼저 뽑는다."""
    rig = Rig()
    original = rig.without_identity().original()
    assert rig.anchors(original.version_id) == []

    revised = rig.revise(original.version_id, AttributeChange("cup", "color", "teal"))

    # 바리스타는 부모의 intro 클립에서 (외형 조건이 같다), 컵은 색이 바뀌었으니 새 intro 에서
    assert rig.extracted() == [("intro", ("barista",)), ("intro", ("cup",))]
    first = rig.extractor.requests[0]
    assert first.clip.uri == original.clips["intro"].uri
    assert rig.identity_of("intro") == ["barista"]
    barista = next(a for a in rig.anchors(revised.version_id) if a.entity_key == "barista")
    assert barista.source_fingerprint == original.clips["intro"].fingerprint


def test_user_reference_is_the_anchor_for_its_entity():
    rig = Rig()
    user = ReferenceImageInput("https://example.com/refs/barista.png", "barista")

    rig.original(references=[user])

    assert rig.extracted() == [("intro", ("cup",))]  # 바리스타는 뽑지 않는다
    assert rig.identity_of("outro") == []
    assert [r.image_uri for r in rig.requests()["outro"].references] == [
        "https://example.com/refs/barista.png"
    ]
    assert rig.identity_of("closeup") == ["cup"]


def test_entity_missing_from_its_first_clip_is_anchored_from_a_later_one():
    rig = Rig(missing_entity_keys={"cup"})
    rig.original()

    assert rig.extracted() == [("intro", ("barista", "cup")), ("closeup", ("cup",))]
    assert rig.identity_of("closeup") == []


def test_extractor_failure_never_fails_the_generation():
    rig = Rig(error=RuntimeError("추출기 고장"))
    version = rig.original()

    assert version.state is VersionState.COMPLETED
    assert rig.anchors(version.version_id) == []
    assert rig.identity_of("outro") == []


def test_anchor_description_reaches_the_generation_request():
    rig = Rig(describe=True)
    rig.original()

    (reference,) = rig.requests()["outro"].identity_references
    assert reference.description == "barista 의 모습"


def test_policy_follows_settings():
    settings = Settings(jobs="recording", identity_kinds=("PERSON",), identity_max_references=1)
    rig = Rig(settings=settings)
    rig.original()

    assert rig.extracted() == [("intro", ("barista",))]
    assert rig.identity_of("closeup") == []
    assert rig.identity_of("outro") == ["barista"]


def test_unknown_identity_kind_fails_at_startup():
    with pytest.raises(ConfigurationError, match="FRAME_IDENTITY_KINDS"):
        make_container(Settings(jobs="recording", identity_kinds=("ROBOT",)))


def test_identity_settings_are_read_from_the_environment():
    settings = Settings.from_env(
        {
            "FRAME_IDENTITY": "fake",
            "FRAME_IDENTITY_KINDS": "person, prop",
            "FRAME_IDENTITY_MAX_REFERENCES": "2",
        }
    )
    assert settings.identity == "fake"
    assert settings.identity_kinds == ("PERSON", "PROP")
    assert settings.identity_max_references == 2
    defaults = Settings.from_env({})
    assert (defaults.identity, defaults.identity_max_references) == ("off", 3)

    container = make_container(Settings(jobs="recording", identity="fake"))
    assert isinstance(container.identity_extractor, FakeIdentityExtractor)


def test_failed_generation_keeps_the_anchors_taken_so_far():
    store = InMemoryArtifactStore()
    container = make_container(
        artifact_store=store,
        generator=FakeVideoGenerator(store, fail_scene_keys={"outro"}),
        identity_extractor=FakeIdentityExtractor(store),
    )
    draft = create_draft(container)
    version = generate(container, draft.version_id)

    assert version.state is VersionState.FAILED
    refs = container.queries.get_version(draft.version_id).reference_images
    assert sorted(r.entity_key for r in refs) == ["barista", "cup"]
