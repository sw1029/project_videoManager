"""VLM 유지 요소 검수기 (vlm_preservation, ADR-0018). 진짜 ffmpeg 로 합성 클립을 읽고, VLM 은 재생 클라이언트.

컨셉 화면의 '유지할 요소'(인물·동작 / 배경·조명 / 컵 형태)와 수정 뒤 이미지의 '인물·배경 유지' 표시.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from frame.adapters.checker import VlmPreservationChecker
from frame.adapters.checker.vlm_preservation import RESPONSE_SCHEMA, SYSTEM_PROMPT
from frame.adapters.llm import ReplayLLMClient
from frame.adapters.storage import InMemoryArtifactStore
from frame.application.errors import LLMTimeout
from frame.application.ports.checker import CheckContext
from frame.domain.artifacts import GeneratedClip
from frame.domain.enums import CheckType, KeepAspect, RevisionKind, Verdict, VersionState
from frame.domain.models import VideoVersion
from frame.domain.revision import KeepItem, RevisionIntent
from frame.domain.services.propagation import AttributeChange, DefaultPropagationPolicy
from tests.support.clock import ManualClock
from tests.support.video import find_ffmpeg, make_clip

FFMPEG = find_ffmpeg()
pytestmark = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg 없음 (video extra 미설치)")
NOW = ManualClock().now()
FRAMES = 2
ACTION = KeepItem(KeepAspect.ACTION, "barista")
LIGHTING = KeepItem(KeepAspect.LIGHTING)
CUP_SHAPE = KeepItem(KeepAspect.SHAPE, "cup")
TARGETS = ("intro", "closeup")


@pytest.fixture(scope="module")
def videos(tmp_path_factory):
    folder = tmp_path_factory.mktemp("clips")
    return {
        "red": make_clip(FFMPEG, folder / "red.mp4", source="color=c=red"),
        "blue": make_clip(FFMPEG, folder / "blue.mp4", source="color=c=blue"),
    }


@pytest.fixture
def store():
    return InMemoryArtifactStore()


def _clips(store, ir, version_id, video, suffix="mp4", media="video/mp4"):
    clips = {}
    for scene in ir.scenes:
        uri = store.put(f"clips/{version_id}/{scene.scene_key}.{suffix}", video, media)
        clips[scene.scene_key] = GeneratedClip(scene.scene_key, uri, scene.duration_ms, "fp")
    return clips


def _revision(store, ir, videos, *, keep=(), kind=RevisionKind.CHANGE, with_intent=True):
    """부모(빨간 컵) -> 자식(파란 컵). intro·closeup 을 다시 만들고 outro 는 부모 클립을 그대로 쓴다."""
    parent_clips = _clips(store, ir, "ver_1", videos["red"])
    child_clips = _clips(store, ir, "ver_2", videos["blue"])
    child_clips["outro"] = parent_clips["outro"]
    child_ir = ir
    intent = None
    if kind is RevisionKind.CHANGE:
        child_ir = DefaultPropagationPolicy().apply(ir, AttributeChange("cup", "color", "blue")).ir
    if with_intent:
        value = "blue" if kind is RevisionKind.CHANGE else "red"
        intent = RevisionIntent(
            kind, "color", value, entity_key="cup", target_scene_keys=TARGETS, keep=tuple(keep)
        )
    parent = VideoVersion("ver_1", "prj_1", VersionState.COMPLETED, NOW, ir=ir, clips=parent_clips)
    version = VideoVersion(
        "ver_2",
        "prj_1",
        VersionState.PROCESSING,
        NOW,
        parent_version_id="ver_1",
        ir=child_ir,
        intent=intent,
    )
    return CheckContext(
        version=version,
        ir=child_ir,
        clips=child_clips,
        tracks=(),
        parent=parent,
        parent_ir=ir,
        parent_clips=parent_clips,
        changed_scene_keys=frozenset(TARGETS),
        reused_scene_keys=frozenset({"outro"}),
    )


def _reply(*kept, difference=""):
    items = [
        {"id": f"E{n}", "kept": verdict, "difference": difference if verdict == "no" else ""}
        for n, verdict in enumerate(kept, start=1)
    ]
    return json.dumps({"elements": items})


def _checker(store, *replies):
    llm = ReplayLLMClient(list(replies))
    return VlmPreservationChecker(store, FFMPEG, llm, frames_per_scene=FRAMES), llm


def test_original_versions_have_nothing_to_preserve(store, ir, videos):
    version = VideoVersion("ver_1", "prj_1", VersionState.PROCESSING, NOW, ir=ir)
    ctx = CheckContext(
        version=version, ir=ir, clips=_clips(store, ir, "ver_1", videos["red"]), tracks=()
    )
    checker, llm = _checker(store)

    assert checker.check(ctx) == [] and llm.calls == []


@pytest.mark.ac("AC-01")
def test_each_kept_element_is_judged_between_the_parent_and_the_new_clip(store, ir, videos):
    """컨셉 화면의 '유지할 요소': 인물·동작 / 조명 / 컵 형태."""
    ctx = _revision(store, ir, videos, keep=(ACTION, LIGHTING, CUP_SHAPE))
    replies = [_reply("yes", "yes", "no", difference="cup is taller"), _reply("yes", "yes")]
    checker, llm = _checker(store, *replies)

    outcomes = checker.check(ctx)

    assert all(o.check_type is CheckType.PRESERVATION for o in outcomes)
    intro = [o for o in outcomes if o.scene_key == "intro"]
    assert [(o.entity_key, o.detail["aspect"], o.verdict, o.score) for o in intro] == [
        ("barista", "ACTION", Verdict.PASS, 1.0),
        (None, "LIGHTING", Verdict.PASS, 1.0),
        ("cup", "SHAPE", Verdict.FAIL, 0.0),
    ]
    (warning,) = intro[2].warnings
    assert (warning.scene_key, warning.start_ms, warning.end_ms) == ("intro", 0, 4000)
    assert warning.message == "cup: 형태이(가) 달라짐 (cup is taller)"
    assert all(o.detail["requested"] and o.detail["method"] == "vlm_keep" for o in intro)
    # 바리스타가 없는 closeup 은 조명·컵 형태만 본다
    closeup = [o for o in outcomes if o.scene_key == "closeup"]
    assert [(o.entity_key, o.detail["aspect"]) for o in closeup] == [
        (None, "LIGHTING"),
        ("cup", "SHAPE"),
    ]
    # 다시 만들지 않은 장면은 그대로다
    (outro,) = [o for o in outcomes if o.scene_key == "outro"]
    assert (outro.verdict, outro.score, outro.detail["reason"]) == (
        Verdict.PASS,
        1.0,
        "clip_reused",
    )

    first = llm.calls[0]
    labels = [image.label for image in first["images"]]
    assert labels == [
        "B1 — BEFORE at 1.0s",
        "B2 — BEFORE at 3.0s",
        "A1 — AFTER at 1.0s",
        "A2 — AFTER at 3.0s",
    ]
    lines = first["user"].splitlines()
    assert "Requested change (expected to differ): cup color: red -> blue" in lines
    assert lines[-3:] == [
        "E1. barista — action and pose",
        "E2. the scene — lighting",
        "E3. cup — shape and size",
    ]
    assert len(llm.calls) == 2  # 다시 만든 장면마다 한 번
    assert [("usage" in o.detail) for o in intro] == [True, False, False]


def test_without_a_keep_list_the_untouched_subjects_and_the_background_are_judged(
    store, ir, videos
):
    """유지할 요소를 고르지 않아도 '비대상' 은 본다: 바꾸지 않은 객체의 모습과 배경."""
    ctx = _revision(store, ir, videos)
    checker, llm = _checker(store, _reply("yes", "no", difference="different cafe"), _reply("yes"))

    outcomes = [o for o in checker.check(ctx) if o.scene_key != "outro"]

    assert [(o.scene_key, o.entity_key, o.detail["aspect"], o.verdict) for o in outcomes] == [
        ("intro", "barista", "APPEARANCE", Verdict.PASS),
        ("intro", None, "BACKGROUND", Verdict.FAIL),
        ("closeup", None, "BACKGROUND", Verdict.PASS),  # 컵(바꾸는 객체)만 나오는 장면
    ]
    assert not any(o.detail["requested"] for o in outcomes)
    assert "E1. barista — appearance" in llm.calls[0]["user"]


def test_the_target_is_read_from_the_requirement_changes_when_there_is_no_intent(store, ir, videos):
    """수정 의도를 저장하기 전에 만든 수정 버전: 요구조건이 달라진 객체를 대상으로 본다."""
    ctx = _revision(store, ir, videos, with_intent=False)
    checker, _ = _checker(store, _reply("yes", "yes"), _reply("yes"))

    intro = [o for o in checker.check(ctx) if o.scene_key == "intro"]

    assert [(o.entity_key, o.detail["aspect"]) for o in intro] == [
        ("barista", "APPEARANCE"),
        (None, "BACKGROUND"),
    ]


def test_repair_tells_the_judge_what_should_now_hold(store, ir, videos):
    ctx = _revision(store, ir, videos, kind=RevisionKind.REPAIR, keep=(ACTION,))
    checker, llm = _checker(store, _reply("yes"))

    outcomes = [o for o in checker.check(ctx) if o.scene_key != "outro"]

    assert [(o.scene_key, o.verdict) for o in outcomes] == [("intro", Verdict.PASS)]
    assert len(llm.calls) == 1  # closeup 에는 바리스타가 없어 볼 요소가 없다
    assert (
        "Requested change (expected to differ): cup color: should now be red "
        "(the BEFORE version may not show it)"
    ) in llm.calls[0]["user"].splitlines()


@pytest.mark.parametrize(
    ("parent_seconds", "child_seconds"),
    [(4.0, 2.0), (2.0, 4.0)],
    ids=["child-shorter", "parent-shorter"],
)
def test_frames_are_taken_where_both_clips_have_them(
    store, ir, videos, tmp_path, parent_seconds, child_seconds
):
    """부모와 자식 클립의 길이가 다르면 (장면 길이를 바꾼 수정) 긴 쪽의 길이로 잡은 시각에는 짧은 쪽의 프레임이 없다.

    두 길이 가운데 짧은 쪽 안에서 뽑는다. 부모 길이로만 잡으면 자식이 짧을 때 프레임을 못 뽑아 미판정이 됐다
    (감사 A-28).
    """
    ctx = _revision(store, ir, videos, keep=(ACTION,))

    def clip(version_id, seconds, color):
        data = make_clip(
            FFMPEG, tmp_path / f"{version_id}.mp4", seconds=seconds, source=f"color=c={color}"
        )
        uri = store.put(f"clips/{version_id}/intro.mp4", data, "video/mp4")
        return GeneratedClip("intro", uri, int(seconds * 1000), "fp")

    parent_clips = {**ctx.parent_clips, "intro": clip("ver_1", parent_seconds, "red")}
    child_clips = {**ctx.clips, "intro": clip("ver_2", child_seconds, "blue")}
    ctx = replace(ctx, parent_clips=parent_clips, clips=child_clips)
    checker, llm = _checker(store, _reply("yes"))

    (intro,) = [o for o in checker.check(ctx) if o.scene_key == "intro"]

    assert intro.verdict is Verdict.PASS
    assert [image.label for image in llm.calls[0]["images"]] == [
        "B1 — BEFORE at 0.5s",
        "B2 — BEFORE at 1.5s",
        "A1 — AFTER at 0.5s",
        "A2 — AFTER at 1.5s",
    ]


def test_what_cannot_be_judged_is_undetermined(store, ir, videos):
    ctx = _revision(store, ir, videos, keep=(ACTION, LIGHTING))
    checker, _ = _checker(store, _reply("unclear"), LLMTimeout("VLM 응답 없음"))

    outcomes = [o for o in checker.check(ctx) if o.scene_key != "outro"]

    intro = [o for o in outcomes if o.scene_key == "intro"]
    assert [(o.verdict, o.score) for o in intro] == [(Verdict.UNDETERMINED, None)] * 2
    assert intro[1].detail["reason"] == "응답에 이 요소의 판정이 없음"
    (closeup,) = [o for o in outcomes if o.scene_key == "closeup"]
    assert closeup.verdict is Verdict.UNDETERMINED and "LLM_TIMEOUT" in closeup.detail["reason"]


def test_once_the_model_does_not_answer_the_remaining_scenes_are_not_asked(store, ir, videos):
    """다시 만든 장면 둘 가운데 첫 장면에서 모델이 답하지 않았다: 둘째 장면은 묻지 않는다 (감사 A-24)."""
    checker, llm = _checker(store, LLMTimeout("VLM 응답 없음"))

    outcomes = checker.check(_revision(store, ir, videos, keep=(ACTION, LIGHTING)))

    remade = [o for o in outcomes if o.scene_key in TARGETS]
    assert [o.verdict for o in remade] == [Verdict.UNDETERMINED] * 2
    assert remade[0].detail["reason"] == "LLM_TIMEOUT: VLM 응답 없음"
    assert (
        remade[1].detail["reason"].startswith("LLM_TIMEOUT: ")
        and "묻지 않" in remade[1].detail["reason"]
    )
    assert len(llm.calls) == 1
    # 부모 클립을 그대로 쓴 장면은 모델 없이 판정한다
    assert [o.verdict for o in outcomes if o.scene_key == "outro"] == [Verdict.PASS]


def test_an_unexpected_error_in_one_scene_keeps_the_verdicts_of_the_others(store, ir, videos):
    checker, llm = _checker(store, KeyError("elements"), _reply("yes"))

    outcomes = checker.check(_revision(store, ir, videos, keep=(ACTION, LIGHTING)))

    intro = [o for o in outcomes if o.scene_key == "intro"]
    closeup = [o for o in outcomes if o.scene_key == "closeup"]
    assert [o.verdict for o in intro] == [Verdict.UNDETERMINED]
    assert intro[0].detail["reason"] == "UNEXPECTED_ERROR: KeyError"
    assert closeup and {o.verdict for o in closeup} == {Verdict.PASS}
    assert len(llm.calls) == 2


def test_clips_that_cannot_be_compared_are_undetermined_without_asking(store, ir, videos):
    ctx = _revision(store, ir, videos, keep=(LIGHTING,))
    json_clips = _clips(store, ir, "ver_3", b"{}", suffix="json", media="application/json")
    missing_parent = replace(ctx, parent_clips={})
    not_video = replace(ctx, clips={**ctx.clips, "intro": json_clips["intro"]})
    checker, llm = _checker(store, _reply("yes"))

    no_parent = [o for o in checker.check(missing_parent) if o.scene_key == "intro"]
    json_intro = [o for o in checker.check(not_video) if o.scene_key == "intro"]

    assert [o.verdict for o in no_parent] == [Verdict.UNDETERMINED]
    assert [o.verdict for o in json_intro] == [Verdict.UNDETERMINED]
    assert len(llm.calls) == 1  # 견줄 수 있는 closeup 만 한 번 물었다


def test_differences_are_written_down_before_the_verdict():
    """2차 실호출: 편집하지 못해 새로 만든 장면(자세·구도·컵 모양이 달라짐)도 요소가 모두 '그대로' 로 나왔다.

    같은 시각의 프레임을 짝지어 다른 점을 먼저 적게 하고, 비슷하게 다시 찍은 장면은 달라진 것으로 보게 한다.
    """
    element = RESPONSE_SCHEMA["properties"]["elements"]["items"]
    assert list(element["properties"]) == ["id", "difference", "kept"]
    assert element["required"] == ["id", "difference", "kept"]
    assert "B1 with A1" in SYSTEM_PROMPT and "shot again" in SYSTEM_PROMPT
