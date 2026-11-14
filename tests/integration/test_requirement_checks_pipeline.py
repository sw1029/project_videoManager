"""실제 요구 반영·유지 요소 검수기를 꽂은 파이프라인 (ADR-0018): 진짜 ffmpeg, VLM 은 재생 클라이언트.

컨셉 화면의 흐름을 검수 쪽에서 본다: 요구와 어긋난 장면이 FAIL 로 드러나고, 수정 버전에서는
다시 만든 장면만 새로 판정하며(바뀌지 않은 장면은 부모 판정을 물려받는다) 유지 요소가 지켜졌는지 판정한다.
"""

from __future__ import annotations

import json

import pytest

from frame.adapters.checker import VlmPreservationChecker, VlmRequirementChecker
from frame.adapters.llm import ReplayLLMClient
from frame.adapters.storage import InMemoryArtifactStore
from frame.application.dto import CreateRevisionCommand
from frame.domain.enums import CheckType, KeepAspect, RevisionKind, Verdict, VersionState
from frame.domain.revision import AttributeChange, KeepItem
from tests.integration.test_real_checkers_pipeline import ColorClipGenerator
from tests.support.factories import create_draft, generate, make_container
from tests.support.video import find_ffmpeg

FFMPEG = find_ffmpeg()
pytestmark = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg 없음 (video extra 미설치)")
FRAMES = 2


def _requirements(*verdicts, observed=""):
    items = [
        {"id": f"R{n}", "verdicts": [v] * FRAMES, "observed": observed if v == "no" else ""}
        for n, v in enumerate(verdicts, start=1)
    ]
    return json.dumps({"requirements": items})


def _elements(*kept):
    items = [{"id": f"E{n}", "kept": v, "difference": ""} for n, v in enumerate(kept, start=1)]
    return json.dumps({"elements": items})


@pytest.mark.ac("AC-01")
@pytest.mark.ac("AC-33")
def test_mismatch_is_detected_then_repaired_and_only_the_repaired_scenes_are_judged_again(tmp_path):
    store = InMemoryArtifactStore()
    requirement_llm = ReplayLLMClient(
        [
            # 원본: intro·closeup 의 컵 색이 요구(red)와 다르게 나왔다
            _requirements("no", "yes", "yes", "yes", observed="teal mug"),  # intro
            _requirements("no", "yes", observed="teal mug"),  # closeup
            _requirements("yes", "yes"),  # outro
            # REPAIR 버전: 다시 만든 두 장면만 판정한다
            _requirements("yes", "yes", "yes", "yes"),
            _requirements("yes", "yes"),
        ]
    )
    keep_llm = ReplayLLMClient([_elements("yes"), _elements("yes")])
    checkers = (
        VlmRequirementChecker(
            store, FFMPEG, requirement_llm, frames_per_scene=FRAMES, threshold=0.5
        ),
        VlmPreservationChecker(store, FFMPEG, keep_llm, frames_per_scene=FRAMES),
    )
    container = make_container(
        artifact_store=store, generator=ColorClipGenerator(store, tmp_path), checkers=checkers
    )
    draft = create_draft(container)
    assert generate(container, draft.version_id).state is VersionState.COMPLETED

    before = container.queries.get_checks(draft.version_id)
    failed = [r for r in before.results if r.verdict is Verdict.FAIL]
    assert [(r.requirement_key, r.scene_key) for r in failed] == [
        ("intro:cup:color", "intro"),
        ("closeup:cup:color", "closeup"),
    ]
    assert failed[0].warnings[0].message == "cup color: 요구 'red', 화면: teal mug"
    assert before.summary.requirement_reflection_rate == pytest.approx(6 / 8)

    repair = container.create_revision.execute(
        CreateRevisionCommand(
            draft.version_id,
            AttributeChange("cup", "color", "red"),
            kind=RevisionKind.REPAIR,
            keep=(KeepItem(KeepAspect.LIGHTING),),
        )
    )
    assert generate(container, repair.version_id).state is VersionState.COMPLETED

    after = container.queries.get_checks(repair.version_id)
    assert after.summary.requirement_reflection_rate == 1.0
    assert len(requirement_llm.calls) == 5  # outro 는 다시 묻지 않았다
    outro = [
        r for r in after.results if r.check_type is CheckType.REQUIREMENT and r.scene_key == "outro"
    ]
    assert {r.detail["inherited_from"] for r in outro} == {draft.version_id}
    keep = [r for r in after.results if r.check_type is CheckType.PRESERVATION]
    assert [(r.scene_key, r.detail.get("aspect"), r.verdict) for r in keep] == [
        ("intro", "LIGHTING", Verdict.PASS),
        ("closeup", "LIGHTING", Verdict.PASS),
        ("outro", None, Verdict.PASS),
    ]
    assert after.summary.preservation_rate == 1.0
    comparison = container.queries.compare_versions(repair.version_id, None)
    assert comparison.checks_delta.requirement_reflection_rate == pytest.approx(0.25)
