"""수정 의도가 생성 단계에 닿는다 (ADR-0017).

- REPAIR: 요구조건은 그대로인데 대상 장면만 다른 seed 로 다시 만든다. 나머지 장면은 부모 클립을 그대로 쓴다.
- 다시 만드는 장면의 요청에는 수정 지시(꼭 지켜야 할 요구조건·그대로 둘 요소)가 실린다.
"""

from dataclasses import replace

import pytest

from frame.adapters.generator import FakeVideoGenerator
from frame.adapters.storage import InMemoryArtifactStore
from frame.application.dto import CreateRevisionCommand
from frame.application.ports.generator import RevisionDirective, SceneGenerationRequest
from frame.domain.enums import ChangeScope, KeepAspect, RevisionKind
from frame.domain.revision import KeepItem
from frame.domain.services.propagation import AttributeChange
from tests.support.factories import create_draft, generate, make_container

ACTION = KeepItem(KeepAspect.ACTION, "barista")
LIGHTING = KeepItem(KeepAspect.LIGHTING)
CUP_SHAPE = KeepItem(KeepAspect.SHAPE, "cup")


class Rig:
    def __init__(self):
        self.store = InMemoryArtifactStore()
        self.generator = FakeVideoGenerator(self.store)
        self.container = make_container(artifact_store=self.store, generator=self.generator)
        self.draft = create_draft(self.container)
        self.original = generate(self.container, self.draft.version_id)
        self.original_requests = list(self.generator.requests)
        self.generator.requests.clear()

    def revise(self, version_id=None, *, kind=RevisionKind.CHANGE, keep=(), **change):
        fields = {"entity_key": "cup", "predicate": "color", "new_value": "teal", **change}
        command = CreateRevisionCommand(
            version_id or self.draft.version_id, AttributeChange(**fields), kind=kind, keep=keep
        )
        result = self.container.create_revision.execute(command)
        self.generator.requests.clear()
        return generate(self.container, result.version_id)

    def requests(self):
        return {r.scene.scene_key: r for r in self.generator.requests}


@pytest.mark.ac("AC-01")
def test_repair_regenerates_only_the_target_scenes_with_another_seed():
    rig = Rig()

    repaired = rig.revise(
        kind=RevisionKind.REPAIR, new_value="red", scope=ChangeScope.SCENES, scene_keys=("closeup",)
    )

    assert list(rig.requests()) == ["closeup"]
    request = rig.requests()["closeup"]
    assert request.seed == repaired.seed_for("closeup") != rig.original.seed_for("closeup")
    # 부모 클립이 함께 간다 (편집하는 생성기는 그것을 고친다)
    assert request.parent.clip == rig.original.clips["closeup"]
    assert repaired.clips["closeup"].uri != rig.original.clips["closeup"].uri
    for reused in ("intro", "outro"):
        assert repaired.clips[reused] == rig.original.clips[reused]
    # 요구조건은 그대로다: 조건 변경은 없고 장면 상태만 REGENERATED
    comparison = rig.container.queries.compare_versions(repaired.version_id, None).comparison
    assert {s.scene_key: (str(s.status), s.conditions_changed) for s in comparison.scenes} == {
        "intro": ("REUSED", False),
        "closeup": ("REGENERATED", False),
        "outro": ("REUSED", False),
    }


def test_repair_tells_the_generator_what_to_enforce_and_what_to_keep():
    rig = Rig()

    rig.revise(kind=RevisionKind.REPAIR, new_value="red", keep=(ACTION, LIGHTING, CUP_SHAPE))

    directives = {key: request.directive for key, request in rig.requests().items()}
    assert set(directives) == {"intro", "closeup"}
    intro = directives["intro"]
    assert intro.kind is RevisionKind.REPAIR and intro.previous_value is None
    assert [(r.entity_key, r.predicate, r.expected_value) for r in intro.enforce] == [
        ("cup", "color", "red")
    ]
    assert intro.keep == (ACTION, LIGHTING, CUP_SHAPE)
    assert directives["closeup"].keep == (LIGHTING, CUP_SHAPE)  # 바리스타가 없는 장면


def test_change_revision_carries_the_new_value_and_the_previous_one():
    rig = Rig()

    rig.revise(keep=(ACTION,))

    directive = rig.requests()["intro"].directive
    assert directive.kind is RevisionKind.CHANGE and directive.previous_value == "red"
    assert [(r.predicate, r.expected_value) for r in directive.enforce] == [("color", "teal")]
    assert directive.keep == (ACTION,)


def test_requests_outside_a_revision_have_no_directive():
    rig = Rig()
    assert all(request.directive is None for request in rig.original_requests)


def test_a_later_change_reuses_the_repaired_scenes():
    """REPAIR 로 다시 만든 장면은 다음 수정 버전에서도 그대로 쓰인다 (장면별 seed 를 물려받는다)."""
    rig = Rig()
    repaired = rig.revise(kind=RevisionKind.REPAIR, new_value="red")

    changed = rig.revise(
        repaired.version_id, entity_key="barista", predicate="shirt_color", new_value="blue"
    )

    assert sorted(rig.requests()) == ["intro", "outro"]  # 바리스타가 나오는 장면만
    assert changed.clips["closeup"] == repaired.clips["closeup"]
    assert rig.requests()["intro"].seed == repaired.seed_for("intro")
    assert rig.requests()["intro"].directive.enforce[0].predicate == "shirt_color"


def test_keep_items_change_the_generation_key_but_not_the_condition_fingerprint(ir):
    scene = ir.scene("intro")
    color = next(r for r in scene.requirements if r.predicate == "color")
    base = SceneGenerationRequest(
        "ver_1", scene, tuple(ir.entity(k) for k in scene.entity_keys), seed=7
    )
    plain = replace(base, directive=RevisionDirective(RevisionKind.CHANGE, enforce=(color,)))
    keeping = replace(
        base,
        directive=RevisionDirective(RevisionKind.CHANGE, enforce=(color,), keep=(LIGHTING,)),
    )

    assert keeping.fingerprint == plain.fingerprint == base.fingerprint  # 재사용 판단은 조건만 본다
    # 유지할 요소가 없으면 예전(지시가 없던 때)의 캐시·원장 키 그대로다
    assert plain.generation_fingerprint == base.generation_fingerprint == base.fingerprint
    assert keeping.generation_fingerprint != base.generation_fingerprint


def test_fake_generator_records_the_directive():
    rig = Rig()

    repaired = rig.revise(kind=RevisionKind.REPAIR, new_value="red", keep=(CUP_SHAPE,))

    import json

    payload = json.loads(rig.store.get(repaired.clips["closeup"].uri))
    assert payload["directive"] == {
        "kind": "REPAIR",
        "enforce": ["closeup:cup:color=red"],
        "keep": [{"aspect": "SHAPE", "entity_key": "cup"}],
    }
