"""정체성 기준 추출의 고정 속성 확인을 파이프라인에서 본다 (ADR-0015): 진짜 ffmpeg, VLM 은 재생 클라이언트.

2026-11-12 실호출: 컵을 청록으로 바꾸는 수정에서 다시 만든 장면에 빨간 컵이 그대로 나왔고, 그 컵이 기준 이미지로
저장됐다. 요구와 다르게 나온 객체는 기준으로 삼지 않아야 그 모습이 다음 장면으로 번지지 않는다.
"""

from __future__ import annotations

import json

import pytest

from frame.adapters.identity import VlmCropIdentityExtractor
from frame.adapters.llm import ReplayLLMClient
from frame.adapters.storage import InMemoryArtifactStore
from frame.application.dto import CreateRevisionCommand
from frame.application.ports.generator import GeneratedClip, VideoGenerator
from frame.domain.enums import ReferenceOrigin, VersionState
from frame.domain.revision import AttributeChange
from tests.support.factories import create_draft, generate, make_container
from tests.support.video import find_ffmpeg, make_clip

FFMPEG = find_ffmpeg()
pytestmark = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg 없음 (video extra 미설치)")


class SameClipGenerator(VideoGenerator):
    """모든 장면에 같은 4초 합성 클립을 준다 (무엇이 찍혔는지는 VLM 재생 답이 정한다)."""

    def __init__(self, store, data):
        self._store, self._data = store, data
        self.requests = []

    def generate_scene(self, request):
        self.requests.append(request)
        scene = request.scene
        uri = self._store.put(
            f"clips/{request.version_id}/{scene.scene_key}.mp4", self._data, "video/mp4"
        )
        return GeneratedClip(scene.scene_key, uri, scene.duration_ms, request.fingerprint)


def _seen(**verdicts_by_entity):
    """VLM 답: 객체마다 화면 가운데에서 찾았고, 고정 속성 판정은 주어진 대로다."""
    return json.dumps(
        {
            "entities": [
                {
                    "entity_key": key,
                    "visible": True,
                    "box": {"x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5},
                    "appearance": f"{key} appearance",
                    "attributes": [{"name": n, "verdict": v} for n, v in verdicts.items()],
                }
                for key, verdicts in verdicts_by_entity.items()
            ]
        }
    )


@pytest.mark.ac("AC-31")
def test_a_subject_that_came_out_wrong_is_not_the_reference_for_later_scenes(tmp_path):
    store = InMemoryArtifactStore()
    llm = ReplayLLMClient(
        [
            # 원본: intro 에서 바리스타와 빨간 컵을 기준으로 뽑는다
            _seen(barista={"shirt color: white": "yes"}, cup={"color: red": "yes"}),
            # 수정(컵 -> 파랑): 다시 만든 intro 의 컵이 빨강 그대로 나왔다
            _seen(cup={"color: blue": "no"}),
            # closeup 의 컵은 파랑으로 나왔다
            _seen(cup={"color: blue": "yes"}),
        ]
    )
    generator = SameClipGenerator(store, make_clip(FFMPEG, tmp_path / "clip.mp4"))
    container = make_container(
        artifact_store=store,
        generator=generator,
        identity_extractor=VlmCropIdentityExtractor(store, FFMPEG, llm, min_side=8),
    )
    draft = create_draft(container)
    assert generate(container, draft.version_id).state is VersionState.COMPLETED
    assert len(llm.calls) == 1

    revision = container.create_revision.execute(
        CreateRevisionCommand(draft.version_id, AttributeChange("cup", "color", "blue"))
    )
    generator.requests.clear()
    revised = generate(container, revision.version_id)

    assert revised.state is VersionState.COMPLETED
    requests = {r.scene.scene_key: r for r in generator.requests}
    assert sorted(requests) == ["closeup", "intro"]  # outro 는 부모 클립을 다시 쓴다
    # 빨갛게 나온 intro 의 컵은 기준이 되지 못했다: closeup 은 컵 기준 없이 만들어진다
    assert [ref.entity_key for ref in requests["closeup"].identity_references] == []
    assert len(llm.calls) == 3
    assert "  fixed attributes to check: color: blue" in llm.calls[1]["user"].splitlines()
    # 새 컵 기준은 요구대로 나온 closeup 에서만 생긴다 (intro 것은 부모 버전에서 물려받은 빨간 컵 기준)
    cup_anchors = [
        ref
        for ref in container.queries.get_version(revision.version_id).reference_images
        if ref.origin is ReferenceOrigin.DERIVED and ref.entity_key == "cup"
    ]
    assert sorted(ref.source_scene_key for ref in cup_anchors) == ["closeup", "intro"]
    parent_cup = [
        ref.image_uri
        for ref in container.queries.get_version(draft.version_id).reference_images
        if ref.entity_key == "cup"
    ]
    inherited = [ref for ref in cup_anchors if ref.source_scene_key == "intro"]
    assert [ref.image_uri for ref in inherited] == parent_cup
