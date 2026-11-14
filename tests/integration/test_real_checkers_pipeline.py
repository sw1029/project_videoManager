"""실제 검수기(VLM 일관성·SSIM 보존)를 꽂은 파이프라인: SQLite + 로컬 산출물 + 진짜 ffmpeg, VLM 은 재생 클라이언트.

Fake 일관성 검수기는 클립을 보지 않아 장면 사이에서 인물이 바뀌어도 1.0 을 준다 (2026-11-11 실호출에서 확인).
실제 검수기는 표본 프레임 판정을 CHECK_RESULT 로 남기고, 수정 버전에서는 다시 만든 장면이 부모와 얼마나 달라졌는지 잰다.
"""

from __future__ import annotations

import json

import pytest

from frame.adapters.checker import (
    FakeRequirementChecker,
    SsimPreservationChecker,
    VlmConsistencyChecker,
)
from frame.adapters.llm import ReplayLLMClient
from frame.adapters.storage import LocalFileArtifactStore
from frame.application.dto import CreateRevisionCommand
from frame.application.ports.generator import GeneratedClip, VideoGenerator
from frame.config import Settings
from frame.domain.enums import CheckType, Verdict, VersionState
from frame.domain.revision import AttributeChange
from tests.support.factories import create_draft, generate, make_container
from tests.support.video import find_ffmpeg, make_clip

FFMPEG = find_ffmpeg()
pytestmark = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg 없음 (video extra 미설치)")
FRAMES = 2


class ColorClipGenerator(VideoGenerator):
    """장면에 나오는 컵의 색으로 칠한 단색 mp4 를 만든다 (컵이 없으면 회색)."""

    def __init__(self, store, folder):
        self._store, self._folder = store, folder
        self.requests = []

    def generate_scene(self, request):
        self.requests.append(request)
        color = next(
            (r.expected_value for r in request.scene.requirements if r.predicate == "color"), "gray"
        )
        path = self._folder / f"{request.version_id}-{request.scene.scene_key}.mp4"
        seconds = request.scene.duration_ms / 1000  # 계약: 클립 길이 = 장면 길이
        data = make_clip(FFMPEG, path, seconds=seconds, fps=5, source=f"color=c={color}")
        uri = self._store.put(
            f"clips/{request.version_id}/{request.scene.scene_key}.mp4", data, "video/mp4"
        )
        return GeneratedClip(
            request.scene.scene_key, uri, request.scene.duration_ms, request.fingerprint
        )


def _reply(frame_ids, different=()):
    return json.dumps(
        {
            "reference_frame_id": frame_ids[0],
            "summary": "",
            "frames": [
                {
                    "frame_id": f,
                    "visible": True,
                    "same_subject": f not in different,
                    "differences": "different person" if f in different else "",
                    "attributes": [],
                }
                for f in frame_ids
            ],
        }
    )


def _frames(*scene_keys):
    return [f"{key}#{n}" for key in scene_keys for n in range(1, FRAMES + 1)]


def _container(tmp_path, replies):
    settings = Settings(
        jobs="recording",
        persistence="sqlalchemy",
        db_url=f"sqlite+pysqlite:///{(tmp_path / 'frame.db').as_posix()}",
        artifact_store="local_fs",
        artifact_dir=str(tmp_path / "artifacts"),
        generator_cache=False,
    )
    store = LocalFileArtifactStore(tmp_path / "artifacts")
    llm = ReplayLLMClient(replies)
    checkers = (
        FakeRequirementChecker(),
        VlmConsistencyChecker(store, FFMPEG, llm, frames_per_scene=FRAMES),
        SsimPreservationChecker(store, FFMPEG),
    )
    generator = ColorClipGenerator(store, tmp_path)
    container = make_container(
        settings, artifact_store=store, generator=generator, checkers=checkers
    )
    return container, llm


def _by_type(container, version_id, check_type):
    return [
        r for r in container.queries.get_checks(version_id).results if r.check_type is check_type
    ]


@pytest.mark.ac("AC-32")
@pytest.mark.ac("AC-33")
def test_real_consistency_checker_catches_a_subject_that_changes_between_scenes(tmp_path):
    """바리스타가 outro 에서 다른 인물로 나왔다: Fake 는 1.0 이지만 실제 검수기는 FAIL + 경고 구간을 남긴다."""
    replies = [
        _reply(_frames("intro", "outro"), different=_frames("outro")),  # barista
        _reply(_frames("intro", "closeup")),  # cup
        _reply(_frames("intro", "closeup", "outro")),  # cafe
    ]
    container, llm = _container(tmp_path, replies)
    draft = create_draft(container)

    version = generate(container, draft.version_id)

    assert version.state is VersionState.COMPLETED  # 검수 FAIL 은 실행 오류가 아니다
    results = {
        r.entity_key: r for r in _by_type(container, draft.version_id, CheckType.CONSISTENCY)
    }
    barista = results["barista"]
    assert (barista.verdict, barista.score) == (Verdict.FAIL, 0.5)
    assert [(w.scene_key, w.start_ms, w.end_ms) for w in barista.warnings] == [
        ("outro", 8000, 12000)
    ]
    assert barista.detail["judged_frames"] == 4 and barista.detail["method"] == "vlm"
    assert results["cup"].verdict is Verdict.PASS and results["cafe"].verdict is Verdict.PASS
    summary = container.queries.get_checks(draft.version_id).summary
    assert summary.consistency_rate == pytest.approx((0.5 + 1.0 + 1.0) / 3)
    assert len(llm.calls) == 3 and all(len(c["images"]) >= 2 * FRAMES for c in llm.calls)


@pytest.mark.ac("AC-01")
def test_real_preservation_checker_measures_regenerated_scenes_against_the_parent(tmp_path):
    replies = [_reply(_frames("intro", "outro")), _reply(_frames("intro", "closeup"))] * 2
    replies[2:2] = [_reply(_frames("intro", "closeup", "outro"))]
    replies.append(_reply(_frames("intro", "closeup", "outro")))
    container, _ = _container(tmp_path, replies)
    draft = create_draft(container)
    generate(container, draft.version_id)

    revision = container.create_revision.execute(
        CreateRevisionCommand(draft.version_id, AttributeChange("cup", "color", "blue"))
    )
    revised = generate(container, revision.version_id)

    assert revised.state is VersionState.COMPLETED
    preservation = {
        r.scene_key: r for r in _by_type(container, revision.version_id, CheckType.PRESERVATION)
    }
    assert preservation["outro"].detail["reason"] == "clip_reused"
    assert preservation["outro"].score == 1.0
    for scene_key in ("intro", "closeup"):  # 빨강 -> 파랑: 화면이 통째로 달라졌다
        result = preservation[scene_key]
        assert result.verdict is Verdict.FAIL and result.score < 0.9
        assert result.detail["method"] == "ssim"
        assert result.warnings and result.warnings[0].scene_key == scene_key
    assert container.queries.get_checks(revision.version_id).summary.preservation_rate < 0.9
