"""VLM 요구 반영 검수기 (vlm_requirement, ADR-0018). 진짜 ffmpeg 로 합성 클립을 읽고, VLM 은 재생 클라이언트.

컨셉 화면의 '컵 색상 불일치' 경고가 이 검수의 FAIL 이다: 요구조건별로 '반영됐는가' 를 실제 영상으로 판정한다.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from frame.adapters.checker import VlmRequirementChecker
from frame.adapters.llm import ReplayLLMClient
from frame.adapters.storage import InMemoryArtifactStore
from frame.application.errors import LLMTimeout
from frame.application.ports.checker import CheckContext
from frame.domain.artifacts import GeneratedClip
from frame.domain.checks import CheckResult
from frame.domain.enums import CheckType, Verdict, VersionState
from frame.domain.ir import RequirementSpec
from frame.domain.models import VideoVersion
from frame.domain.services.propagation import AttributeChange, DefaultPropagationPolicy
from tests.support.clock import ManualClock
from tests.support.video import find_ffmpeg, make_clip

FFMPEG = find_ffmpeg()
pytestmark = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg 없음 (video extra 미설치)")
NOW = ManualClock().now()
FRAMES = 2
# 카페 IR 의 장면별 요구조건 순서 (R1, R2 …)
INTRO = ("cup color", "barista shirt_color", "barista holds cup", "cafe lighting")
ALL_YES = {"intro": 4, "closeup": 2, "outro": 2}


@pytest.fixture(scope="module")
def video(tmp_path_factory):
    return make_clip(FFMPEG, tmp_path_factory.mktemp("clips") / "red.mp4", source="color=c=red")


@pytest.fixture
def store():
    return InMemoryArtifactStore()


def _context(store, ir, video, **extra):
    clips = {}
    for scene in ir.scenes:
        uri = store.put(f"clips/ver_1/{scene.scene_key}.mp4", video, "video/mp4")
        clips[scene.scene_key] = GeneratedClip(scene.scene_key, uri, scene.duration_ms, "fp")
    version = VideoVersion("ver_1", "prj_1", VersionState.PROCESSING, NOW, ir=ir)
    return CheckContext(version=version, ir=ir, clips=clips, tracks=(), **extra)


def _reply(*verdicts, observed="", ids=None):
    """요구조건 순서대로 프레임별 판정을 준다. 판정 하나만 쓰면 모든 프레임에 그 값."""
    items = []
    for index, verdict in enumerate(verdicts, start=1):
        rows = [verdict] * FRAMES if isinstance(verdict, str) else list(verdict)
        rid = ids[index - 1] if ids else f"R{index}"
        items.append({"id": rid, "verdicts": rows, "observed": observed})
    return json.dumps({"requirements": items})


def _checker(store, *replies, **options):
    llm = ReplayLLMClient(list(replies))
    return VlmRequirementChecker(store, FFMPEG, llm, frames_per_scene=FRAMES, **options), llm


def _all_yes():
    return [_reply(*["yes"] * count) for count in ALL_YES.values()]


def test_every_requirement_gets_a_verdict_from_the_frames_of_its_scene(store, ir, video):
    checker, llm = _checker(store, *_all_yes())

    outcomes = checker.check(_context(store, ir, video))

    assert [o.requirement_key for o in outcomes] == [r.key for r in ir.iter_requirements()]
    assert all(o.check_type is CheckType.REQUIREMENT for o in outcomes)
    assert {(o.verdict, o.score) for o in outcomes} == {(Verdict.PASS, 1.0)}
    assert len(llm.calls) == 3  # 장면마다 한 번
    first = llm.calls[0]
    assert len(first["images"]) == FRAMES and first["images"][0].label.startswith("F1")
    lines = first["user"].splitlines()
    assert "R1. cup — color: red" in lines and "R3. barista — holds cup" in lines
    # 토큰 수는 장면의 첫 요구조건에만 적는다 (장면당 한 번 부른 호출)
    assert [("usage" in o.detail) for o in outcomes[:4]] == [True, False, False, False]


@pytest.mark.ac("AC-33")
def test_a_requirement_the_video_contradicts_fails_with_a_warning_on_that_scene(store, ir, video):
    """컨셉 화면의 '컵 색상 불일치': 요구는 red 인데 화면의 컵은 청록."""
    replies = _all_yes()
    replies[0] = _reply("no", "yes", "yes", "yes", observed="teal ceramic mug")
    checker, _ = _checker(store, *replies)

    cup = checker.check(_context(store, ir, video))[0]

    assert (cup.verdict, cup.score) == (Verdict.FAIL, 0.0)
    assert (cup.entity_key, cup.scene_key, cup.requirement_key) == (
        "cup",
        "intro",
        "intro:cup:color",
    )
    (warning,) = cup.warnings
    assert (warning.scene_key, warning.start_ms, warning.end_ms) == ("intro", 0, 4000)
    assert warning.message == "cup color: 요구 'red', 화면: teal ceramic mug"
    assert cup.detail["observed"] == "teal ceramic mug"
    assert [f["verdict"] for f in cup.detail["frames"]] == ["no", "no"]
    assert (cup.detail["predicate"], cup.detail["expected_value"]) == ("color", "red")


def test_fixed_attributes_need_most_judged_frames_and_actions_need_one(store, ir, video):
    """색 같은 고정 속성은 프레임의 과반이 지켜야 하고, 관계·행동은 장면 안에서 한 번 보이면 된다."""
    waving = replace(
        ir,
        scenes=tuple(
            replace(
                s,
                requirements=(
                    *s.requirements,
                    RequirementSpec("outro", "action", "waves", "barista"),
                ),
            )
            if s.scene_key == "outro"
            else s
            for s in ir.scenes
        ),
    )
    replies = [
        _reply(["yes", "no"], ["no", "unclear"], ["no", "yes"], "yes"),  # intro
        _reply(["no", "no"], "yes"),  # closeup
        _reply("yes", "yes", ["no", "no"]),  # outro: 손 흔드는 장면이 한 번도 안 보임
    ]
    checker, _ = _checker(store, *replies)

    by_key = {o.requirement_key: o for o in checker.check(_context(store, waving, video))}

    assert (by_key["intro:cup:color"].verdict, by_key["intro:cup:color"].score) == (
        Verdict.PASS,
        0.5,
    )
    assert by_key["intro:barista:shirt_color"].verdict is Verdict.FAIL  # 판정된 한 프레임이 no
    holds = by_key["intro:barista:holds:cup"]
    assert (holds.verdict, holds.detail["rule"]) == (Verdict.PASS, "any")
    assert by_key["closeup:cup:color"].verdict is Verdict.FAIL
    waves = by_key["outro:barista:action"]
    assert (waves.verdict, waves.score, waves.detail["rule"]) == (Verdict.FAIL, 0.0, "any")
    assert [(w.start_ms, w.end_ms) for w in waves.warnings] == [(8000, 12000)]  # 장면 전체


def test_only_the_contradicting_part_of_the_scene_is_marked(store, ir, video):
    checker, _ = _checker(
        store, _reply(["yes", "no"], "yes", "yes", "yes"), *_all_yes()[1:], threshold=0.9
    )

    cup = checker.check(_context(store, ir, video))[0]

    assert cup.verdict is Verdict.FAIL and cup.detail["threshold"] == 0.9
    assert [(w.start_ms, w.end_ms) for w in cup.warnings] == [(2000, 4000)]  # 뒤쪽 절반


def test_frames_that_cannot_be_judged_are_left_out(store, ir, video):
    replies = _all_yes()
    replies[1] = _reply(["unclear", "yes"], "unclear")
    checker, _ = _checker(store, *replies)

    by_key = {o.requirement_key: o for o in checker.check(_context(store, ir, video))}

    assert (by_key["closeup:cup:color"].verdict, by_key["closeup:cup:color"].score) == (
        Verdict.PASS,
        1.0,
    )
    lighting = by_key["closeup:cafe:lighting"]
    assert lighting.verdict is Verdict.UNDETERMINED and lighting.score is None


def test_answers_are_matched_to_requirements_even_when_the_id_carries_extra_text(store, ir, video):
    """실제 모델은 키에 질의 문구를 그대로 붙여 돌려줄 때가 있다."""
    replies = _all_yes()
    replies[1] = _reply("no", "yes", ids=["R1. cup — color: red", "R2"])
    checker, _ = _checker(store, *replies)

    by_key = {o.requirement_key: o for o in checker.check(_context(store, ir, video))}

    assert by_key["closeup:cup:color"].verdict is Verdict.FAIL
    assert by_key["closeup:cafe:lighting"].verdict is Verdict.PASS


def test_missing_answers_and_failed_calls_are_undetermined_not_errors(store, ir, video):
    replies = [_reply("yes"), LLMTimeout("VLM 응답 없음"), *_all_yes()[2:]]
    checker, _ = _checker(store, *replies)

    outcomes = checker.check(_context(store, ir, video))

    intro = [o for o in outcomes if o.scene_key == "intro"]
    assert [o.verdict for o in intro] == [Verdict.PASS] + [Verdict.UNDETERMINED] * 3
    closeup = [o for o in outcomes if o.scene_key == "closeup"]
    assert {o.verdict for o in closeup} == {Verdict.UNDETERMINED}
    assert "LLM_TIMEOUT" in closeup[0].detail["reason"]
    assert {o.verdict for o in outcomes if o.scene_key == "outro"} == {Verdict.PASS}


def test_every_result_names_its_requirement_the_same_way(store, ir, video):
    """판정이 났든 장면 전체를 판정하지 못했든, 결과의 detail 로 어느 요구조건인지 알 수 있다 (감사 A-16).

    장면 전체가 미판정일 때만 predicate 가 없어서, detail.predicate 로 대상을 찾던 평가 코드가 그 장면을 빼고 셌다.
    """
    replies = [_reply("yes"), LLMTimeout("VLM 응답 없음"), *_all_yes()[2:]]
    checker, _ = _checker(store, *replies)

    outcomes = checker.check(_context(store, ir, video))

    by_key = {r.key: r for r in ir.iter_requirements()}
    for outcome in outcomes:
        requirement = by_key[outcome.requirement_key]
        assert outcome.detail["method"] == "vlm"
        assert outcome.detail["predicate"] == requirement.predicate
        assert outcome.detail["expected_value"] == requirement.expected_value
    undetermined = [o for o in outcomes if o.scene_key == "closeup"]
    assert {o.verdict for o in undetermined} == {Verdict.UNDETERMINED}


def test_clips_that_are_not_video_are_undetermined_without_asking(store, ir):
    clips = {}
    for scene in ir.scenes:
        uri = store.put(f"clips/ver_1/{scene.scene_key}.json", b"{}", "application/json")
        clips[scene.scene_key] = GeneratedClip(scene.scene_key, uri, scene.duration_ms, "fp")
    version = VideoVersion("ver_1", "prj_1", VersionState.PROCESSING, NOW, ir=ir)
    checker, llm = _checker(store)

    outcomes = checker.check(CheckContext(version=version, ir=ir, clips=clips, tracks=()))

    assert len(outcomes) == 8 and {o.verdict for o in outcomes} == {Verdict.UNDETERMINED}
    assert llm.calls == []


def _parent_result(key, verdict, score, **detail):
    return CheckResult(
        f"chk_{key}",
        "ver_0",
        CheckType.REQUIREMENT,
        verdict,
        score=score,
        requirement_key=key,
        scene_key="outro",
        detail={"method": "vlm", "usage": {"input_tokens": 9}, **detail},
    )


def test_scenes_that_were_not_regenerated_inherit_the_parent_verdicts(store, ir, video):
    """같은 클립을 다시 물으면 답이 흔들린다. 바뀌지 않은 장면의 판정은 부모 것을 그대로 쓴다."""
    parent = VideoVersion("ver_0", "prj_1", VersionState.COMPLETED, NOW, ir=ir)
    parent_checks = (
        _parent_result("outro:barista:shirt_color", Verdict.FAIL, 0.0, observed="blue shirt"),
        _parent_result("outro:cafe:lighting", Verdict.PASS, 1.0),
    )
    ctx = _context(
        store,
        ir,
        video,
        parent=parent,
        parent_ir=ir,
        parent_checks=parent_checks,
        reused_scene_keys=frozenset({"outro"}),
        changed_scene_keys=frozenset({"intro", "closeup"}),
    )
    checker, llm = _checker(store, *_all_yes()[:2])

    outro = [o for o in checker.check(ctx) if o.scene_key == "outro"]

    assert len(llm.calls) == 2  # outro 는 묻지 않았다
    assert [(o.requirement_key, o.verdict, o.score) for o in outro] == [
        ("outro:barista:shirt_color", Verdict.FAIL, 0.0),
        ("outro:cafe:lighting", Verdict.PASS, 1.0),
    ]
    assert outro[0].detail["inherited_from"] == "ver_0" and "usage" not in outro[0].detail
    assert outro[0].detail["observed"] == "blue shirt" and outro[0].entity_key == "barista"


def test_a_reused_scene_is_judged_when_the_parent_has_no_verdict_for_it(store, ir, video):
    parent = VideoVersion("ver_0", "prj_1", VersionState.COMPLETED, NOW, ir=ir)
    ctx = _context(
        store, ir, video, parent=parent, parent_ir=ir, reused_scene_keys=frozenset({"outro"})
    )
    checker, llm = _checker(store, *_all_yes())

    assert len(checker.check(ctx)) == 8 and len(llm.calls) == 3


def test_an_undetermined_parent_verdict_is_not_inherited(store, ir, video):
    """부모를 검수할 때 VLM 이 답하지 않아 미판정이 된 장면. 미판정은 판정이 아니므로 물려받지 않고 새로 판정한다.

    물려받으면 그 장면을 다시 만들지 않는 모든 수정 버전에서 계속 미판정으로 남는다 (감사 A-23, ADR-0018).
    """
    parent = VideoVersion("ver_0", "prj_1", VersionState.COMPLETED, NOW, ir=ir)
    parent_checks = (
        _parent_result(
            "outro:barista:shirt_color", Verdict.UNDETERMINED, None, reason="LLM_TIMEOUT: 응답 없음"
        ),
        _parent_result("outro:cafe:lighting", Verdict.PASS, 1.0),
    )
    ctx = _context(
        store,
        ir,
        video,
        parent=parent,
        parent_ir=ir,
        parent_checks=parent_checks,
        reused_scene_keys=frozenset({"outro"}),
        changed_scene_keys=frozenset({"intro", "closeup"}),
    )
    checker, llm = _checker(store, *_all_yes())

    outro = [o for o in checker.check(ctx) if o.scene_key == "outro"]

    assert len(llm.calls) == 3  # outro 도 물었다
    assert [(o.requirement_key, o.verdict) for o in outro] == [
        ("outro:barista:shirt_color", Verdict.PASS),
        ("outro:cafe:lighting", Verdict.PASS),
    ]
    assert all("inherited_from" not in o.detail for o in outro)


def test_decided_parent_verdicts_are_inherited_whether_they_pass_or_fail(store, ir, video):
    parent = VideoVersion("ver_0", "prj_1", VersionState.COMPLETED, NOW, ir=ir)
    parent_checks = (
        _parent_result("outro:barista:shirt_color", Verdict.FAIL, 0.0),
        _parent_result("outro:cafe:lighting", Verdict.PASS, 1.0),
    )
    ctx = _context(
        store,
        ir,
        video,
        parent=parent,
        parent_ir=ir,
        parent_checks=parent_checks,
        reused_scene_keys=frozenset({"outro"}),
    )
    checker, llm = _checker(store, *_all_yes()[:2])

    outro = [o for o in checker.check(ctx) if o.scene_key == "outro"]

    assert len(llm.calls) == 2
    assert [o.verdict for o in outro] == [Verdict.FAIL, Verdict.PASS]


# ---- 이 버전에서 바뀐 요구조건 ------------------------------------------------------------


def _teal(ir):
    return DefaultPropagationPolicy().apply(ir, AttributeChange("cup", "color", "teal")).ir


def test_a_requirement_changed_in_this_version_says_what_it_was(store, ir, video):
    """객체 이름·설명에는 예전 표현이 남는다.

    2차 실호출: red_mug 의 색을 teal 로 바꾼 뒤 한 장면을 '청록이지 빨강이 아니다' 로 FAIL 판정했다.
    이 버전에서 값이 바뀐 요구조건에는 예전 값을 알려 준다.
    """
    checker, llm = _checker(store, *_all_yes())

    checker.check(_context(store, _teal(ir), video, parent_ir=ir))

    intro = llm.calls[0]["user"].splitlines()
    assert 'R1. cup — color: teal (changed in this version; it was "red" before)' in intro
    assert "R3. barista — holds cup" in intro  # 바뀌지 않은 요구조건에는 붙지 않는다
    assert sum("changed in this version" in line for line in intro) == 1
    assert "outdated" in llm.calls[0]["system"]


def test_a_requirement_added_by_the_change_says_what_the_subject_had_elsewhere(store, ir, video):
    """모든 등장 구간에 적용하면서 그 장면에 새로 생긴 요구조건: 그 객체가 다른 장면에서 갖던 값이 예전 값이다."""
    parent = replace(
        ir,
        scenes=tuple(
            replace(s, requirements=tuple(r for r in s.requirements if r.key != "intro:cup:color"))
            if s.scene_key == "intro"
            else s
            for s in ir.scenes
        ),
    )
    revised = _teal(parent)
    assert any(r.key == "intro:cup:color" for r in revised.scene("intro").requirements)
    checker, llm = _checker(store, *_all_yes())

    checker.check(_context(store, revised, video, parent_ir=parent))

    intro = llm.calls[0]["user"].splitlines()
    note = 'cup — color: teal (changed in this version; it was "red" before)'
    assert sum(line.endswith(note) for line in intro) == 1


def test_requirements_that_kept_their_value_carry_no_note(store, ir, video):
    """다시 맞추기(REPAIR)는 요구조건이 그대로다. 새로 생긴 행동 조건도 다른 장면의 행동과 견주지 않는다."""
    acting = replace(
        ir,
        scenes=tuple(
            replace(
                s,
                requirements=(
                    *s.requirements,
                    RequirementSpec(s.scene_key, "action", "waves", "barista"),
                ),
            )
            if s.scene_key == "outro"
            else s
            for s in ir.scenes
        ),
    )
    parent = replace(
        ir,
        scenes=tuple(
            replace(
                s,
                requirements=(
                    *s.requirements,
                    RequirementSpec(s.scene_key, "action", "pours coffee", "barista"),
                ),
            )
            if s.scene_key == "intro"
            else s
            for s in ir.scenes
        ),
    )
    checker, llm = _checker(store, *_all_yes()[:2], _reply("yes", "yes", "yes"))

    checker.check(_context(store, acting, video, parent_ir=parent))

    assert not any("changed in this version" in call["user"] for call in llm.calls)
