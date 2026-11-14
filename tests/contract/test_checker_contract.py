"""Checker 계약 테스트 (US-03). Fake 와 실제 검수기가 같은 규약을 지킨다.

- 판정할 수 없으면 FAIL 이 아니라 UNDETERMINED (점수 없음).
- score 는 0..1, 경고 구간은 그 장면의 시간 구간 안.
실제 검수기는 진짜 ffmpeg 로 합성 클립을 읽는다 (없으면 건너뜀). VLM 은 재생 클라이언트.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from frame.adapters.checker import (
    FakeConsistencyChecker,
    FakePreservationChecker,
    SsimPreservationChecker,
    VlmConsistencyChecker,
)
from frame.adapters.llm import ReplayLLMClient
from frame.adapters.storage import InMemoryArtifactStore, LocalFileArtifactStore
from frame.adapters.tracker import FakeObjectTracker
from frame.application.errors import LLMTimeout
from frame.application.ports.checker import CheckContext
from frame.application.ports.tracker import TrackingRequest
from frame.domain.artifacts import GeneratedClip
from frame.domain.checks import WarningInterval
from frame.domain.enums import CheckType, Verdict, VersionState
from frame.domain.models import ReferenceImage, VideoVersion
from frame.domain.revision import AttributeChange, DefaultPropagationPolicy
from tests.support.clock import ManualClock
from tests.support.video import find_ffmpeg, make_clip

FFMPEG = find_ffmpeg()
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg 없음 (video extra 미설치)")
NOW = ManualClock().now()
SCENES = ("intro", "closeup", "outro")
FRAMES = 2  # 장면마다 뽑는 표본 프레임 수


@pytest.fixture(scope="module")
def videos(tmp_path_factory):
    if FFMPEG is None:
        return dict.fromkeys(("red", "blue", "pattern"), b"not-a-video")
    folder = tmp_path_factory.mktemp("clips")
    return {
        "red": make_clip(FFMPEG, folder / "red.mp4", source="color=c=red"),
        "blue": make_clip(FFMPEG, folder / "blue.mp4", source="color=c=blue"),
        "pattern": make_clip(FFMPEG, folder / "pattern.mp4"),
    }


def _version(ir, version_id="ver_1", parent=None):
    return VideoVersion(
        version_id, "prj_1", VersionState.PROCESSING, NOW, ir=ir, parent_version_id=parent
    )


def _clips(store, ir, version_id, videos, colors=None):
    clips = {}
    for scene in ir.scenes:
        video = videos[(colors or {}).get(scene.scene_key, "red")]
        uri = store.put(f"clips/{version_id}/{scene.scene_key}.mp4", video, "video/mp4")
        clips[scene.scene_key] = GeneratedClip(scene.scene_key, uri, scene.duration_ms, "fp")
    return clips


def _tracks(ir, clips, version_id="ver_1", fail=()):
    tracker = FakeObjectTracker(fail_entity_keys=fail)
    records = []
    for scene in ir.scenes:
        records += tracker.track(
            TrackingRequest(version_id, scene, clips[scene.scene_key], scene.entity_keys)
        )
    return tuple(records)


def _context(store, ir, videos, *, fail=(), references=(), colors=None):
    clips = _clips(store, ir, "ver_1", videos, colors)
    return CheckContext(
        version=_version(ir),
        ir=ir,
        clips=clips,
        tracks=_tracks(ir, clips, fail=fail),
        references=tuple(references),
    )


def _frames(ir, entity_key):
    """검수기가 그 객체에 대해 뽑을 프레임 id (장면 순, 장면마다 FRAMES 장)."""
    return [
        f"{scene.scene_key}#{n}"
        for scene in ir.scenes_with_entity(entity_key)
        for n in range(1, FRAMES + 1)
    ]


def _answer(frame_ids, *, different=(), hidden=(), unsure=(), dropped_attributes=None):
    """VLM 응답: different 는 다른 객체로 보인 프레임, hidden 은 안 보인 프레임."""
    frames = []
    for frame_id in frame_ids:
        visible = frame_id not in hidden
        same = None if (not visible or frame_id in unsure) else frame_id not in different
        frames.append(
            {
                "frame_id": frame_id,
                "visible": visible,
                "same_subject": same,
                "differences": "hair is longer, different face" if same is False else "",
                "attributes": [
                    {"name": name, "kept": False}
                    for name in (dropped_attributes or {}).get(frame_id, ())
                ],
            }
        )
    return json.dumps(
        {"reference_frame_id": frame_ids[0], "frames": frames, "summary": "test answer"}
    )


def _vlm_checker(store, ir, **answers):
    """객체 순서대로 답하는 재생 VLM 을 꽂은 검수기. answers: 객체 키 -> _answer 인자."""
    replies = [
        _answer(_frames(ir, entity.entity_key), **answers.get(entity.entity_key, {}))
        for entity in ir.entities
    ]
    llm = ReplayLLMClient(replies)
    return VlmConsistencyChecker(store, FFMPEG, llm, frames_per_scene=FRAMES), llm


def _fake_consistency(store, ir):
    return FakeConsistencyChecker()


def _vlm_consistency(store, ir):
    return _vlm_checker(store, ir)[0]


CONSISTENCY = [
    pytest.param(_fake_consistency, id="fake"),
    pytest.param(_vlm_consistency, id="vlm", marks=needs_ffmpeg),
]


@pytest.fixture
def store():
    return InMemoryArtifactStore()


@pytest.fixture(params=CONSISTENCY)
def consistency(request, store, ir):
    return request.param(store, ir)


# ---- 일관성 검수 공통 계약 ---------------------------------------------------------------


@pytest.mark.ac("AC-32")
def test_consistent_entities_pass_with_a_score_of_one(consistency, store, ir, videos):
    outcomes = consistency.check(_context(store, ir, videos))

    assert {o.entity_key for o in outcomes} == {"barista", "cup", "cafe"}
    for outcome in outcomes:
        assert outcome.check_type is CheckType.CONSISTENCY
        assert (outcome.verdict, outcome.score) == (Verdict.PASS, 1.0)
        assert outcome.warnings == ()


def test_failed_tracking_makes_the_entity_undetermined(consistency, store, ir, videos):
    outcomes = consistency.check(_context(store, ir, videos, fail={"cup"}))

    cup = next(o for o in outcomes if o.entity_key == "cup")
    assert (cup.verdict, cup.score) == (Verdict.UNDETERMINED, None)
    assert next(o for o in outcomes if o.entity_key == "barista").verdict is Verdict.PASS


def test_scores_are_between_zero_and_one(consistency, store, ir, videos):
    for outcome in consistency.check(_context(store, ir, videos)):
        assert outcome.score is None or 0.0 <= outcome.score <= 1.0


# ---- VLM 일관성 검수기 (지표 3: 객체가 보이는 표본 프레임 중 고정 속성을 유지한 비율) --------------


@needs_ffmpeg
@pytest.mark.ac("AC-32")
def test_vlm_score_is_the_share_of_visible_frames_that_keep_the_subject(store, ir, videos):
    # 바리스타: intro 2장 + outro 2장. outro 두 장이 다른 인물로 보인다
    checker, _ = _vlm_checker(store, ir, barista={"different": ("outro#1", "outro#2")})

    barista = next(
        o for o in checker.check(_context(store, ir, videos)) if o.entity_key == "barista"
    )

    assert barista.score == 0.5 and barista.verdict is Verdict.FAIL
    assert barista.detail["judged_frames"] == 4 and barista.detail["consistent_frames"] == 2
    assert barista.detail["identity_rate"] == 0.5
    assert barista.detail["reference"] == "first_visible_frame"
    assert barista.detail["model"] == "replay"
    assert set(barista.detail["usage"]) == {"input_tokens", "output_tokens"}


@needs_ffmpeg
@pytest.mark.ac("AC-33")
def test_vlm_marks_the_broken_scene_interval_with_a_warning(store, ir, videos):
    checker, _ = _vlm_checker(store, ir, barista={"different": ("outro#2",)})

    barista = next(
        o for o in checker.check(_context(store, ir, videos)) if o.entity_key == "barista"
    )

    (warning,) = barista.warnings
    outro = ir.scene("outro")
    assert warning.scene_key == "outro"
    # 둘째 표본 프레임이 대표하는 구간: 장면의 뒤쪽 절반
    assert (warning.start_ms, warning.end_ms) == (10000, 12000)
    assert outro.planned_start_ms <= warning.start_ms < warning.end_ms <= outro.planned_end_ms
    assert "hair is longer" in warning.message
    assert barista.score == 0.75


@needs_ffmpeg
def test_vlm_ignores_frames_where_the_subject_is_not_visible(store, ir, videos):
    checker, _ = _vlm_checker(
        store, ir, barista={"hidden": ("intro#2",), "different": ("outro#1",)}
    )

    barista = next(
        o for o in checker.check(_context(store, ir, videos)) if o.entity_key == "barista"
    )

    assert barista.detail["judged_frames"] == 3  # 안 보인 프레임은 분모에서 뺀다
    assert barista.score == pytest.approx(2 / 3)


@needs_ffmpeg
def test_vlm_counts_a_dropped_fixed_attribute_as_inconsistent(store, ir, videos):
    """같은 컵으로 보여도 지정한 색을 잃으면 유지하지 못한 프레임이다."""
    checker, llm = _vlm_checker(
        store, ir, cup={"dropped_attributes": {"closeup#1": ("color",), "closeup#2": ("color",)}}
    )

    cup = next(o for o in checker.check(_context(store, ir, videos)) if o.entity_key == "cup")

    assert cup.score == 0.5 and cup.verdict is Verdict.FAIL
    assert cup.detail["identity_rate"] == 1.0 and cup.detail["attribute_rate"] == 0.5
    cup_call = next(c for c in llm.calls if "- cup (prop)" in c["user"])
    assert "color: red" in cup_call["user"]  # 고정 속성을 알려 준다
    assert [image.label.split(" ")[1] for image in cup_call["images"]] == _frames(ir, "cup")


@needs_ffmpeg
def test_vlm_does_not_list_transient_predicates_as_fixed_attributes(store, ir, videos):
    acting = DefaultPropagationPolicy().apply(ir, AttributeChange("barista", "action", "waves")).ir
    checker, llm = _vlm_checker(store, acting)

    checker.check(_context(store, acting, videos))

    barista_call = next(c for c in llm.calls if "- barista (person)" in c["user"])
    assert "shirt color: white" in barista_call["user"]
    assert "action" not in barista_call["user"]


@needs_ffmpeg
def test_vlm_compares_against_a_user_reference_image_when_there_is_one(store, ir, videos):
    frame = store.put("refs/barista.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 16, "image/png")
    reference = ReferenceImage("img_1", "ver_1", frame, "barista")
    checker, llm = _vlm_checker(store, ir)

    outcomes = checker.check(_context(store, ir, videos, references=(reference,)))

    barista = next(o for o in outcomes if o.entity_key == "barista")
    assert barista.detail["reference"] == "user_reference"
    barista_call = next(c for c in llm.calls if "- barista (person)" in c["user"])
    assert barista_call["images"][0].label.startswith("REF")
    assert len(barista_call["images"]) == 1 + 2 * FRAMES


@needs_ffmpeg
@pytest.mark.parametrize("kind", ["memory", "local_fs"])
def test_vlm_judges_an_entity_whose_user_reference_is_an_external_address(
    kind, ir, videos, tmp_path
):
    """사용자 참조가 외부 주소면 그 이미지를 읽지 못할 뿐, 객체는 판정한다 (감사 A-21).

    로컬 저장소가 다른 곳의 주소에 예외를 내던 때에는 그 저장소에서만 이 객체가 늘 미판정이었다.
    """
    store = InMemoryArtifactStore() if kind == "memory" else LocalFileArtifactStore(tmp_path)
    reference = ReferenceImage("img_1", "ver_1", "https://example.com/barista.png", "barista")
    checker, _ = _vlm_checker(store, ir)

    outcomes = checker.check(_context(store, ir, videos, references=(reference,)))

    barista = next(o for o in outcomes if o.entity_key == "barista")
    assert barista.verdict is Verdict.PASS, barista.detail


@needs_ffmpeg
def test_vlm_stops_asking_once_the_model_does_not_answer(store, ir, videos):
    """객체 셋 가운데 첫 객체에서 모델이 답하지 않았다: 남은 객체는 묻지 않는다 (감사 A-24)."""
    llm = ReplayLLMClient([LLMTimeout("응답 없음")])
    checker = VlmConsistencyChecker(store, FFMPEG, llm, frames_per_scene=1)

    outcomes = checker.check(_context(store, ir, videos))

    assert [o.verdict for o in outcomes] == [Verdict.UNDETERMINED] * 3
    assert outcomes[0].detail["reason"] == "LLM_TIMEOUT: 응답 없음"
    assert all("묻지 않" in o.detail["reason"] for o in outcomes[1:])
    assert len(llm.calls) == 1


@needs_ffmpeg
def test_vlm_keeps_the_other_verdicts_when_one_subject_fails_unexpectedly(store, ir, videos):
    """예전에는 검수기 전체가 예외로 끝나 다른 객체의 판정까지 버려졌다 (감사 A-13)."""
    llm = ReplayLLMClient(
        [KeyError("frames")]
    )  # 첫 객체에서만 터지고, 남은 둘은 재생할 응답이 없다
    checker = VlmConsistencyChecker(store, FFMPEG, llm, frames_per_scene=1)

    outcomes = checker.check(_context(store, ir, videos))

    assert [o.verdict for o in outcomes] == [Verdict.UNDETERMINED] * 3
    assert outcomes[0].detail["reason"] == "UNEXPECTED_ERROR: KeyError"
    assert all(o.detail["reason"].startswith("LLM_BAD_RESPONSE") for o in outcomes[1:])
    assert len(llm.calls) == 3  # 남은 객체는 그대로 물었다


@needs_ffmpeg
def test_vlm_failure_is_undetermined_not_an_error(store, ir, videos):
    llm = ReplayLLMClient([LLMTimeout("응답 없음")] * 3)
    checker = VlmConsistencyChecker(store, FFMPEG, llm, frames_per_scene=FRAMES)

    outcomes = checker.check(_context(store, ir, videos))

    assert [o.verdict for o in outcomes] == [Verdict.UNDETERMINED] * 3
    assert all(o.score is None and "LLM_TIMEOUT" in o.detail["reason"] for o in outcomes)


@needs_ffmpeg
def test_vlm_is_undetermined_when_the_subject_is_never_visible(store, ir, videos):
    checker, _ = _vlm_checker(store, ir, cup={"hidden": tuple(_frames(ir, "cup"))})

    cup = next(o for o in checker.check(_context(store, ir, videos)) if o.entity_key == "cup")

    assert (cup.verdict, cup.score) == (Verdict.UNDETERMINED, None)


@needs_ffmpeg
def test_vlm_is_undetermined_for_clips_that_are_not_video(ir):
    store = InMemoryArtifactStore()
    clips = {
        s.scene_key: GeneratedClip(
            s.scene_key,
            store.put(f"clips/ver_1/{s.scene_key}.json", b"{}", "application/json"),
            s.duration_ms,
            "fp",
        )
        for s in ir.scenes
    }
    ctx = CheckContext(version=_version(ir), ir=ir, clips=clips, tracks=_tracks(ir, clips))
    llm = ReplayLLMClient([])

    outcomes = VlmConsistencyChecker(store, FFMPEG, llm).check(ctx)

    assert {o.verdict for o in outcomes} == {Verdict.UNDETERMINED}
    assert llm.calls == []  # 영상이 아니면 VLM 을 부르지 않는다


# ---- 보존 검수 -------------------------------------------------------------------------


def _revision_context(store, ir, videos, *, child_colors, reused=("outro",)):
    """부모는 모두 빨강 클립. 자식은 reused 장면은 부모 클립 그대로, 나머지는 child_colors 의 클립."""
    parent_clips = _clips(store, ir, "ver_1", videos)
    child_clips = _clips(store, ir, "ver_2", videos, child_colors)
    for scene_key in reused:
        child_clips[scene_key] = parent_clips[scene_key]
    parent = replace(_version(ir), clips=parent_clips)
    return CheckContext(
        version=_version(ir, "ver_2", parent="ver_1"),
        ir=ir,
        clips=child_clips,
        tracks=(),
        parent=parent,
        parent_ir=ir,
        parent_clips=parent_clips,
        changed_scene_keys=frozenset(s for s in SCENES if s not in reused),
        reused_scene_keys=frozenset(reused),
    )


def _fake_preservation(store):
    return FakePreservationChecker()


def _ssim_preservation(store):
    return SsimPreservationChecker(store, FFMPEG)


PRESERVATION = [
    pytest.param(_fake_preservation, id="fake"),
    pytest.param(_ssim_preservation, id="ssim", marks=needs_ffmpeg),
]


@pytest.fixture(params=PRESERVATION)
def preservation(request, store):
    return request.param(store)


def test_original_versions_have_no_preservation_check(preservation, store, ir, videos):
    assert list(preservation.check(_context(store, ir, videos))) == []


@pytest.mark.ac("AC-01")
def test_reused_scenes_are_fully_preserved(preservation, store, ir, videos):
    ctx = _revision_context(store, ir, videos, child_colors={})

    outcomes = {o.scene_key: o for o in preservation.check(ctx)}

    assert set(outcomes) == set(SCENES)
    reused = outcomes["outro"]
    assert (reused.verdict, reused.score) == (Verdict.PASS, 1.0)
    assert reused.detail["reason"] == "clip_reused"
    assert all(o.check_type is CheckType.PRESERVATION for o in outcomes.values())


@needs_ffmpeg
@pytest.mark.ac("AC-01")
def test_ssim_measures_how_much_a_regenerated_scene_kept_of_its_parent(store, ir, videos):
    """intro 는 내용이 같은 클립, closeup 은 전혀 다른 클립으로 다시 만들어졌다."""
    ctx = _revision_context(store, ir, videos, child_colors={"closeup": "pattern"})

    outcomes = {o.scene_key: o for o in SsimPreservationChecker(store, FFMPEG).check(ctx)}

    same, changed = outcomes["intro"], outcomes["closeup"]
    assert same.verdict is Verdict.PASS and same.score == pytest.approx(1.0, abs=1e-3)
    assert changed.verdict is Verdict.FAIL and changed.score < 0.5
    assert changed.detail["reason"] == "regenerated" and changed.detail["method"] == "ssim"
    assert changed.detail["change_rate"] == pytest.approx(1 - changed.score)
    (warning,) = changed.warnings
    closeup = ir.scene("closeup")
    assert (warning.scene_key, warning.start_ms, warning.end_ms) == (
        "closeup",
        closeup.planned_start_ms,
        closeup.planned_end_ms,
    )
    assert isinstance(warning, WarningInterval)


@needs_ffmpeg
def test_ssim_is_undetermined_without_a_comparable_parent_clip(store, ir, videos):
    ctx = _revision_context(store, ir, videos, child_colors={})
    del ctx.parent_clips["intro"]  # 부모에 없던 장면
    ctx.clips["closeup"] = GeneratedClip(
        "closeup", store.put("clips/ver_2/closeup.json", b"{}", "application/json"), 4000, "fp"
    )

    outcomes = {o.scene_key: o for o in SsimPreservationChecker(store, FFMPEG).check(ctx)}

    assert (outcomes["intro"].verdict, outcomes["intro"].score) == (Verdict.UNDETERMINED, None)
    assert outcomes["closeup"].verdict is Verdict.UNDETERMINED
