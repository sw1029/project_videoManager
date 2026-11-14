"""CheckEpisodeScreen·CorrectEpisodeScreen: 화면 정보가 어긋난 곳을 찾아 그 부분만 고친다 (ADR-0027)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from frame.adapters.checker import (
    FakeConsistencyChecker,
    FakePreservationChecker,
    FakeRequirementChecker,
)
from frame.adapters.compositor import FakeOverlayRenderer, FakeScreenTextReader
from frame.adapters.fake_media import fake_recording
from frame.adapters.generator import FakeVideoGenerator
from frame.adapters.storage import InMemoryArtifactStore
from frame.application.errors import GenerationError, LLMTimeout
from frame.application.series import (
    AttachNarrationCommand,
    CorrectScreenCommand,
    PatchRegion,
    RecordingInput,
    StepStatus,
)
from frame.config.theme_wiring import ThemeSettings
from frame.domain.checks import WarningInterval
from frame.domain.correction import CorrectionMethod, IssueKind
from frame.domain.enums import KeepAspect, RevisionKind, VersionState
from frame.domain.episode import OverlayKind
from frame.domain.errors import NotFound, ValidationError
from frame.domain.ir import RequirementSpec
from frame.domain.revision import KeepItem
from frame.domain.screen import Region
from frame.domain.video_analysis import SpeechSegment, Transcript
from tests.support.factories import dispatcher_of, version_of
from tests.support.series import (
    GOOD_READING,
    generate_episode,
    new_episode,
    save_theme,
    series_container,
    series_of,
)
from tests.support.themes import cafe_theme, matcha_content


def _container(*, failing=(), reader=None, **overrides):
    """요구 반영 검수가 failing 의 요구조건을 FAIL 로 내는 조립."""
    store = InMemoryArtifactStore()
    overrides.setdefault("artifact_store", store)
    overrides.setdefault("generator", FakeVideoGenerator(overrides["artifact_store"]))
    overrides.setdefault(
        "checkers",
        (
            FakeRequirementChecker(failing_keys=failing),
            FakeConsistencyChecker(),
            FakePreservationChecker(),
        ),
    )
    if reader is not None:
        overrides["screen_text_reader"] = reader
    container = series_container(**overrides)
    save_theme(container)
    return container


def _spied(**overrides):
    """(컨테이너, 생성 요청을 기록하는 가짜 생성기). 컨테이너의 생성기는 캐시로 감싸여 있어 따로 쥔다."""
    store = InMemoryArtifactStore()
    generator = FakeVideoGenerator(store)
    return _container(artifact_store=store, generator=generator, **overrides), generator


def _ready(container, content=None):
    """생성하고 완성본까지 만든 편."""
    episode = generate_episode(container, new_episode(container, content=content))
    return series_of(container).render_episode.execute(episode.episode_id).episode


def _check(container, episode):
    return series_of(container).check_screen.execute(episode.episode_id)


def _correct(container, episode, **changes):
    command = CorrectScreenCommand(episode.episode_id, **changes)
    return series_of(container).correct_screen.execute(command)


def _tamper(container, episode, overlay_id: str, text: str):
    """계획의 문구를 틀리게 바꿔 둔다 (기획이 가격을 잘못 적은 경우)."""
    series = series_of(container)
    current = series.episodes.get(episode.episode_id)
    wrong = replace(current.plan.overlay(overlay_id), text=text)
    series.episodes.save(replace(current, plan=current.plan.with_overlay(wrong), render=None))
    return series.render_episode.execute(episode.episode_id).episode


# ---- 검수 ----
def test_clean_episode_has_no_issues():
    container = _container()
    report = _check(container, _ready(container))

    assert report.ok and report.issues == ()
    assert report.checked_requirements > 0 and report.checked_overlays == 3
    assert report.checked_lines == 0 and report.notes == ()


def test_overlay_that_differs_from_the_theme_template_is_found_without_looking_at_the_video():
    container = _container()
    episode = _tamper(container, _ready(container), "closeup:price", "말차 라떼 5,500원")
    report = _check(container, episode)

    assert [i.issue_id for i in report.issues] == ["overlay:closeup:price"]
    issue = report.issues[0]
    assert (issue.kind, issue.method) == (IssueKind.OVERLAY_TEXT, CorrectionMethod.OVERLAY_EDIT)
    assert (issue.expected, issue.observed) == ("말차 라떼 5,000원", "말차 라떼 5,500원")
    # 편의 타임라인에서의 구간과 영역: 세 번째 장면(8초부터)의 0.5~3.5초
    assert (issue.scene_key, issue.start_ms, issue.end_ms) == ("closeup", 8500, 11500)
    assert issue.region == Region(0.1, 0.72, 0.8, 0.08) and issue.detail["source"] == "plan"


def test_text_that_reads_differently_on_screen_is_found_by_reading_the_render():
    """계획은 맞는데 화면에서 다르게 보이는 문구 (글꼴에 글자가 없어 빈 네모로 나온 경우)."""
    reader = FakeScreenTextReader(misread={"greeting:title": "□□□ □□"})
    container = _container(reader=reader)
    report = _check(container, _ready(container))

    assert [i.issue_id for i in report.issues] == ["overlay:greeting:title"]
    issue = report.issues[0]
    assert (issue.expected, issue.observed) == ("오늘의 메뉴", "□□□ □□")
    assert issue.detail == {"source": "render", "read_at_ms": 2000}
    assert [call[0] for call in reader.calls] == [
        2000,
        2000,
        2000,
    ]  # 문구마다 그 구간의 가운데를 읽는다


def test_rendered_text_is_not_checked_without_a_render_or_a_reader():
    container = _container()
    episode = generate_episode(container, new_episode(container))
    report = _check(container, episode)
    assert report.ok and report.checked_overlays == 0
    assert any("완성본이 없어" in note for note in report.notes)

    off = _container(theme_settings=ThemeSettings(text_reader="off"))
    report = _check(off, _ready(off))
    assert report.checked_overlays == 0 and any("읽는 어댑터가 없어" in n for n in report.notes)


def test_unreadable_overlay_is_a_note_not_an_issue():
    container = _container(reader=FakeScreenTextReader(error=LLMTimeout("응답 없음")))
    report = _check(container, _ready(container))

    assert report.ok and report.checked_overlays == 0
    assert len(report.notes) == 3 and all("읽지 못했습니다" in note for note in report.notes)


def test_failed_requirement_and_bad_narration_are_issues_too():
    container = _container(failing=("closeup:latte:color",))
    episode = _ready(container)
    wrong = Transcript(
        "ko", (*GOOD_READING.segments[:2], SpeechSegment(5000, 6400, "가격은 육천 원"))
    )
    series_of(container).attach_narration.execute(
        AttachNarrationCommand(episode.episode_id, (RecordingInput(fake_recording(wrong)),))
    )
    report = _check(container, episode)

    by_id = {i.issue_id: i for i in report.issues}
    assert set(by_id) == {"requirement:closeup:latte:color", "narration:L3", "narration:L4"}
    color = by_id["requirement:closeup:latte:color"]
    assert (color.kind, color.method, color.expected) == (
        IssueKind.ATTRIBUTE,
        CorrectionMethod.CLIP_EDIT,
        "green",
    )
    assert (color.start_ms, color.end_ms) == (8000, 12000)
    price = by_id["narration:L3"]
    assert (price.kind, price.method) == (IssueKind.NARRATION, CorrectionMethod.RERECORD)
    assert price.expected == "가격은 오천 원." and price.observed == "가격은 육천 원"
    assert price.detail["reason"] == "mismatch" and price.detail["missing_terms"] == ["5,000원"]
    assert by_id["narration:L4"].detail["reason"] == "missing"
    assert report.checked_lines == 4
    assert report.to_dict()["ok"] is False


# ---- 국소 수정: 화면 문구 ----
def test_wrong_overlay_is_fixed_without_regenerating_any_clip():
    container, generator = _spied(overlay_renderer=FakeOverlayRenderer())
    episode = _tamper(container, _ready(container), "closeup:price", "말차 라떼 5,500원")
    before = episode.render
    generated = len(generator.requests)

    result = _correct(container, episode)

    assert [(s.issue_id, s.status, s.method) for s in result.steps] == [
        ("overlay:closeup:price", StepStatus.APPLIED, CorrectionMethod.OVERLAY_EDIT)
    ]
    assert "5,500원" in result.steps[0].note and "5,000원" in result.steps[0].note
    assert result.recomposed_scene_keys == ("closeup",) and result.pending_version_id is None
    assert result.episode.plan.overlay("closeup:price").text == "말차 라떼 5,000원"
    assert result.episode.version_id == episode.version_id and result.episode.render is None
    # 완성본을 다시 만들면 그 장면만 다시 합성된다. 영상은 한 장면도 다시 생성되지 않았다
    rendered = series_of(container).render_episode.execute(episode.episode_id)
    assert rendered.composed_now == () and set(rendered.reused) == {"greeting", "closeup"}
    after = rendered.episode.render
    assert {k: after.scene_uris[k] for k in ("greeting", "show", "outro")} == {
        k: before.scene_uris[k] for k in ("greeting", "show", "outro")
    }
    assert len(generator.requests) == generated
    assert _check(container, rendered.episode).ok


def test_text_broken_on_screen_is_reported_not_changed():
    container = _container(reader=FakeScreenTextReader(misread={"greeting:title": "□□□ □□"}))
    episode = _ready(container)
    result = _correct(container, episode)

    step = result.steps[0]
    assert step.status is StepStatus.SKIPPED and "글꼴" in step.note
    assert result.episode.plan == episode.plan and result.episode.render == episode.render


# ---- 국소 수정: 영상 안의 글자를 가리고 다시 쓰기 ----
def _content_with_scene_text():
    """컵에 FRAME 이라는 글자가 찍혀야 하는 편."""
    content = matcha_content()
    slots = tuple(
        replace(
            s, requirements=s.requirements + (RequirementSpec("closeup", "text", "FRAME", "mug"),)
        )
        if s.slot_key == "closeup"
        else s
        for s in content.slots
    )
    return replace(content, slots=slots)


def test_wrong_scene_text_is_patched_over_when_a_region_is_given():
    container, generator = _spied(failing=("closeup:mug:text",))
    episode = _ready(container, _content_with_scene_text())
    issue = _check(container, episode).issues[0]
    assert (issue.kind, issue.expected) == (IssueKind.SCENE_TEXT, "FRAME")
    generated = len(generator.requests)

    region = Region(0.35, 0.45, 0.3, 0.06)
    result = _correct(container, episode, patches=(PatchRegion(issue.issue_id, region),))

    step = result.steps[0]
    assert (step.status, step.method) == (StepStatus.APPLIED, CorrectionMethod.PATCH)
    patch = result.episode.plan.overlay(step.overlay_id)
    assert (patch.kind, patch.text, patch.region) == (OverlayKind.PATCH, "FRAME", region)
    assert (patch.start_ms, patch.end_ms) == (0, 4000)  # 그 장면 안에서의 시간
    assert patch.style.background_opacity == 1.0  # 틀린 글자를 덮는다
    assert result.pending_version_id is None and len(generator.requests) == generated
    rendered = series_of(container).render_episode.execute(episode.episode_id)
    assert rendered.composed_now == ("closeup",)


def _patched(reader=None):
    """컵의 글자가 틀려 가리고 다시 쓴 편. (컨테이너, 생성기, 편, 고친 어긋남의 id, 가린 문구의 id)"""
    container, generator = _spied(failing=("closeup:mug:text",), reader=reader)
    episode = _ready(container, _content_with_scene_text())
    issue = _check(container, episode).issues[0]
    patch = PatchRegion(issue.issue_id, Region(0.35, 0.45, 0.3, 0.06))
    step = _correct(container, episode, patches=(patch,)).steps[0]
    series_of(container).render_episode.execute(episode.episode_id)
    return container, generator, episode, issue.issue_id, step.overlay_id


def test_patched_scene_text_is_settled_and_the_patch_is_read_on_screen():
    """클립은 그대로이므로 요구 반영 검수는 여전히 FAIL 이다. 가린 뒤에는 그것을 다시 어긋난 곳으로 세지 않는다.

    대신 가린 문구가 화면에서 제대로 읽히는지를 다른 문구와 같이 완성본에서 확인한다.
    """
    container, generator, episode, issue_id, overlay_id = _patched()
    generated = len(generator.requests)

    report = _check(container, episode)

    assert overlay_id == "closeup:patch:mug:text"
    assert report.ok and report.patched == (issue_id,)
    assert report.checked_overlays == 4  # 문구 3 + 가린 문구 1
    assert report.to_dict()["patched"] == [issue_id]
    # 다시 고치라고 해도 고칠 것이 없다: 클립을 다시 만들지 않는다
    again = _correct(container, episode)
    assert again.steps == () and again.pending_version_id is None
    assert len(generator.requests) == generated


def test_patch_that_reads_wrong_on_screen_is_reported():
    """가린 자리에 쓴 글자가 잘려 보이면 그 문구가 어긋난 곳이 된다 (고친 것으로 치지 않는다)."""
    reader = FakeScreenTextReader(misread={"closeup:patch:mug:text": "FRAM"})
    container, _, episode, issue_id, overlay_id = _patched(reader)

    report = _check(container, episode)

    assert [i.issue_id for i in report.issues] == [f"overlay:{overlay_id}"]
    assert (report.issues[0].expected, report.issues[0].observed) == ("FRAME", "FRAM")
    assert report.patched == (issue_id,)


def test_patch_with_another_text_does_not_settle_the_requirement():
    """가린 문구가 요구한 글자와 다르면 (사람이 문구를 바꿔 둠) 요구조건은 여전히 어긋난 곳이다."""
    container, _, episode, issue_id, overlay_id = _patched()
    series = series_of(container)
    current = series.episodes.get(episode.episode_id)
    wrong = replace(current.plan.overlay(overlay_id), text="FLAME")
    series.episodes.save(replace(current, plan=current.plan.with_overlay(wrong), render=None))

    report = _check(container, episode)

    assert [i.issue_id for i in report.issues] == [issue_id] and report.patched == ()


def test_patch_is_only_for_scene_text_and_known_issues():
    container = _container(failing=("closeup:latte:color",))
    episode = _ready(container)
    region = Region(0.3, 0.4, 0.3, 0.1)

    with pytest.raises(ValidationError, match="글자에만"):
        _correct(
            container, episode, patches=(PatchRegion("requirement:closeup:latte:color", region),)
        )
    with pytest.raises(ValidationError, match="고칠 대상에 없습니다"):
        _correct(container, episode, patches=(PatchRegion("requirement:nope", region),))
    with pytest.raises(NotFound):
        _correct(container, episode, issue_ids=("requirement:nope",))


# ---- 국소 수정: 객체 속성 (그 장면의 클립만 다시 만든다) ----
def test_wrong_attribute_regenerates_only_its_scene_through_a_repair_revision():
    container, generator = _spied(failing=("closeup:latte:color",))
    episode = _ready(container)
    before = version_of(container, episode.version_id)
    generator.requests.clear()

    keep = (KeepItem(KeepAspect.BACKGROUND),)
    result = _correct(
        container,
        episode,
        issue_ids=("requirement:closeup:latte:color",),
        keep=keep,
        generate=True,
    )

    step = result.steps[0]
    assert (step.status, step.method) == (StepStatus.GENERATING, CorrectionMethod.CLIP_EDIT)
    revision_id = result.pending_version_id
    assert revision_id == step.version_id and revision_id != episode.version_id
    # 수정 버전이 끝나기 전에는 편의 기준 버전이 그대로다 (실패해도 편이 멈추지 않는다)
    assert result.episode.version_id == episode.version_id and result.episode.history == ()
    assert result.episode.pending_version_id == revision_id
    assert result.episode.render == episode.render

    dispatcher_of(container).run_all()
    revision = version_of(container, revision_id)
    assert (
        revision.state is VersionState.COMPLETED
        and revision.parent_version_id == episode.version_id
    )
    assert revision.intent.kind is RevisionKind.REPAIR and revision.intent.keep == keep
    assert revision.intent.target_scene_keys == ("closeup",)
    assert "requirement:closeup:latte:color" in revision.intent.request_text
    # 그 장면만 다시 만들었다. 나머지 장면은 부모의 클립 그대로다
    assert [r.scene.scene_key for r in generator.requests] == ["closeup"]
    assert generator.requests[0].parent.clip.uri == before.clips["closeup"].uri
    for scene_key in ("greeting", "show", "outro"):
        assert revision.clips[scene_key].uri == before.clips[scene_key].uri
    assert revision.clips["closeup"].uri != before.clips["closeup"].uri
    # 끝난 뒤 완성본을 다시 만들면 그 버전이 기준 버전이 되어 있고, 문구는 그대로 다시 얹힌다
    rendered = series_of(container).render_episode.execute(episode.episode_id).episode
    assert rendered.version_id == revision_id and rendered.history == (episode.version_id,)
    assert rendered.pending_version_id is None and rendered.render.version_id == revision_id
    assert rendered.plan.overlay("closeup:price").text == "말차 라떼 5,000원"


def test_same_condition_in_several_scenes_goes_into_one_revision_and_others_wait():
    failing = ("show:latte:color", "closeup:latte:color", "greeting:barista:outfit")
    container = _container(failing=failing)
    episode = _ready(container)

    # 짚은 순서대로 본다: 첫 조건이 한 수정 버전이 되고, 다른 조건은 다음으로 미룬다
    named = (
        "requirement:greeting:barista:outfit",
        "requirement:show:latte:color",
        "requirement:closeup:latte:color",
    )
    result = _correct(container, episode, issue_ids=named)

    statuses = {s.issue_id: s.status for s in result.steps}
    assert statuses == {
        "requirement:greeting:barista:outfit": StepStatus.DRAFTED,
        "requirement:show:latte:color": StepStatus.SKIPPED,
        "requirement:closeup:latte:color": StepStatus.SKIPPED,
    }
    revision = version_of(container, result.pending_version_id)
    assert revision.state is VersionState.DRAFT  # 생성은 요청해야 접수한다 (기본은 초안만)
    assert revision.intent.target_scene_keys == ("greeting",)
    # 골라서 고치면 같은 조건이 걸린 두 장면이 한 수정 버전으로 간다
    again = _container(failing=failing)
    episode = _ready(again)
    picked = _correct(
        again,
        episode,
        issue_ids=("requirement:show:latte:color", "requirement:closeup:latte:color"),
    )
    assert version_of(again, picked.pending_version_id).intent.target_scene_keys == (
        "show",
        "closeup",
    )
    assert {s.status for s in picked.steps} == {StepStatus.DRAFTED}


# ---- 국소 수정: 같은 객체가 장면마다 다르게 보임 ----
def _inconsistent_latte(**overrides):
    """말차 라떼가 클로즈업 장면에서 기준과 다르게 나온 편. 요구조건은 모두 맞다."""
    warning = WarningInterval("closeup", 8000, 12000, "latte: 기준과 다른 모습 (cup shape)")
    checkers = (
        FakeRequirementChecker(),
        FakeConsistencyChecker(warnings_by_entity={"latte": [warning]}),
        FakePreservationChecker(),
    )
    return _spied(checkers=checkers, **overrides)


def test_object_that_looks_different_in_one_scene_is_found_and_only_that_scene_is_remade():
    """둘째 실호출: 요구조건은 모두 통과했는데 같은 컵이 장면마다 달랐다. 화면 검수가 그것을 놓치면 안 된다.

    고칠 때는 그 장면에서 그 객체의 모습 조건 하나를 축으로 다시 맞추는(REPAIR) 수정 버전을 만든다.
    """
    container, generator = _inconsistent_latte()
    episode = _ready(container)
    before = version_of(container, episode.version_id)
    generator.requests.clear()

    report = _check(container, episode)
    assert [(i.issue_id, i.kind) for i in report.issues] == [
        ("consistency:closeup:latte", IssueKind.CONSISTENCY)
    ]

    result = _correct(container, episode, issue_ids=("consistency:closeup:latte",), generate=True)

    step = result.steps[0]
    assert (step.status, step.method) == (StepStatus.GENERATING, CorrectionMethod.CLIP_EDIT)
    dispatcher_of(container).run_all()
    revision = version_of(container, result.pending_version_id)
    assert revision.intent.kind is RevisionKind.REPAIR
    assert revision.intent.target_scene_keys == ("closeup",)
    assert (revision.intent.entity_key, revision.intent.predicate) == ("latte", "color")
    assert [r.scene.scene_key for r in generator.requests] == ["closeup"]
    for scene_key in ("greeting", "show", "outro"):
        assert revision.clips[scene_key].uri == before.clips[scene_key].uri


def test_inconsistent_object_without_a_look_condition_in_that_scene_is_reported_not_remade():
    """그 장면에 그 객체의 모습 조건이 없으면 다시 맞출 축이 없다. 알리기만 한다."""
    warning = WarningInterval("greeting", 0, 4000, "barista: 기준과 다른 모습")
    theme = replace(
        cafe_theme(),
        cast=tuple(replace(c, fixed_attributes=()) for c in cafe_theme().cast),
    )
    store = InMemoryArtifactStore()
    container = series_container(
        artifact_store=store,
        generator=FakeVideoGenerator(store),
        checkers=(
            FakeRequirementChecker(),
            FakeConsistencyChecker(warnings_by_entity={"barista": [warning]}),
            FakePreservationChecker(),
        ),
    )
    save_theme(container, theme)
    episode = _ready(container)

    result = _correct(container, episode, issue_ids=("consistency:greeting:barista",))

    assert [(s.issue_id, s.status) for s in result.steps] == [
        ("consistency:greeting:barista", StepStatus.SKIPPED)
    ]
    assert "조건이 없어" in result.steps[0].note and result.pending_version_id is None


# ---- 만들고 있는 수정 버전 (감사 BTS-16·17) ----
def test_failed_repair_does_not_strand_the_episode():
    """다시 만들기가 실패해도 편은 기준 버전 그대로다. 완성본이 되고, 검수가 여전히 그 어긋남을 알려 준다."""
    container, generator = _spied(failing=("closeup:latte:color",))
    episode = _ready(container)
    generator._error = GenerationError("생성 서버 오류", retryable=False)
    result = _correct(
        container, episode, issue_ids=("requirement:closeup:latte:color",), generate=True
    )
    dispatcher_of(container).run_all()
    assert version_of(container, result.pending_version_id).state is VersionState.FAILED

    report = _check(container, episode)

    assert report.version_id == episode.version_id and report.pending_version_id is None
    assert [i.issue_id for i in report.issues] == ["requirement:closeup:latte:color"]
    after = series_of(container).episodes.get(episode.episode_id)
    assert (after.version_id, after.history, after.render) == (
        episode.version_id,
        (),
        episode.render,
    )
    assert series_of(container).render_episode.execute(episode.episode_id).composed_now == ()


def test_clip_fix_waits_while_a_revision_is_being_made():
    container = _container(failing=("closeup:latte:color", "greeting:barista:outfit"))
    episode = _ready(container)
    first = _correct(container, episode, issue_ids=("requirement:closeup:latte:color",))
    assert first.steps[0].status is StepStatus.DRAFTED

    second = _correct(container, episode, issue_ids=("requirement:greeting:barista:outfit",))

    assert second.steps[0].status is StepStatus.SKIPPED
    assert first.pending_version_id in second.steps[0].note
    assert second.pending_version_id is None  # 새 수정 버전을 만들지 않았다
    assert _check(container, episode).pending_version_id == first.pending_version_id


def test_overlay_fix_is_kept_when_the_generation_request_fails():
    """생성 접수가 실패해도 같은 호출에서 한 문구 수정은 남는다 (문구 수정을 먼저 저장한다)."""
    container = _container(failing=("closeup:latte:color",))
    episode = _tamper(container, _ready(container), "closeup:price", "말차 라떼 6,000원")
    series = series_of(container)

    def refuse(version_id: str):
        raise GenerationError("대기열이 가득 찼습니다")

    series.correct_screen._accept_generation.execute = refuse
    named = ("overlay:closeup:price", "requirement:closeup:latte:color")
    with pytest.raises(GenerationError):
        _correct(container, episode, issue_ids=named, generate=True)

    after = series.episodes.get(episode.episode_id)
    assert after.plan.overlay("closeup:price").text == "말차 라떼 5,000원"
    assert after.version_id == episode.version_id and after.pending_version_id is not None


def test_screen_of_an_episode_that_is_not_generated_yet_is_unknown_not_fine():
    """판정할 수 없는 것을 맞다고 하지 않는다: 아직 만들지 않은 편의 검수는 '어긋난 곳 없음' 이 아니라 '모름' 이다."""
    container = _container()
    draft = new_episode(container)

    report = _check(container, draft)

    assert report.issues == () and report.ok is None
    assert report.version_state == "DRAFT" and report.checked_requirements == 0
    assert report.to_dict()["ok"] is None
    assert _check(container, _ready(container)).ok is True


# ---- 돈이 드는 수정은 짚었을 때만 ----
def test_clip_is_not_remade_unless_the_issue_is_named():
    """고칠 것을 짚지 않고 부르면 문구만 고친다. 클립을 다시 만드는 수정(과금)은 어긋남을 짚었을 때만 한다."""
    container, generator = _spied(failing=("closeup:latte:color",))
    episode = _tamper(container, _ready(container), "closeup:price", "말차 라떼 6,000원")
    generator.requests.clear()

    result = _correct(container, episode)

    by_issue = {s.issue_id: s for s in result.steps}
    assert by_issue["overlay:closeup:price"].status is StepStatus.APPLIED  # 문구는 고친다 (무료)
    waiting = by_issue["requirement:closeup:latte:color"]
    assert waiting.status is StepStatus.SKIPPED and "issue_ids" in waiting.note
    assert result.pending_version_id is None
    assert result.episode.version_id == episode.version_id and result.episode.history == ()
    assert dispatcher_of(container).run_all() == 0 and generator.requests == []


def test_generation_is_only_accepted_when_asked():
    """짚어서 고치더라도 생성은 요청해야 접수한다. 기본은 수정 버전의 초안까지다."""
    assert CorrectScreenCommand("epi_1").generate is False
    container, generator = _spied(failing=("closeup:latte:color",))
    episode = _ready(container)
    generator.requests.clear()

    result = _correct(container, episode, issue_ids=("requirement:closeup:latte:color",))

    assert result.steps[0].status is StepStatus.DRAFTED
    assert version_of(container, result.pending_version_id).state is VersionState.DRAFT
    assert dispatcher_of(container).run_all() == 0 and generator.requests == []


def test_a_report_already_in_hand_is_not_checked_again():
    """검수 보고서를 넘기면 화면을 다시 읽지 않는다 (문구마다 유료 읽기 한 번이다)."""
    reader = FakeScreenTextReader()
    container = _container(reader=reader)
    episode = _tamper(container, _ready(container), "closeup:price", "말차 라떼 6,000원")
    report = _check(container, episode)
    read = len(reader.calls)

    result = _correct(container, episode, report=report)

    assert [s.status for s in result.steps] == [StepStatus.APPLIED]
    assert result.report is report and len(reader.calls) == read
    # 넘기지 않으면 다시 검수한다 (완성본을 다시 만든 뒤라 화면도 다시 읽는다)
    series_of(container).render_episode.execute(episode.episode_id)
    assert _correct(container, episode).steps == () and len(reader.calls) > read


def test_a_report_of_another_episode_or_version_is_refused():
    container = _container(failing=("closeup:latte:color",))
    episode = _ready(container)
    report = _check(container, episode)
    other = _ready(container)

    with pytest.raises(ValidationError, match="다른 편"):
        _correct(container, other, report=report)
    waiting = _correct(
        container,
        episode,
        report=report,
        issue_ids=("requirement:closeup:latte:color",),
        generate=True,
    )
    assert waiting.episode.version_id == report.version_id  # 아직 기준 버전은 그대로다
    dispatcher_of(container).run_all()
    with pytest.raises(ValidationError, match="버전"):  # 편이 다른 버전으로 넘어간 뒤의 낡은 보고서
        _correct(container, episode, report=report)


# ---- 국소 수정: 녹음 ----
def test_bad_narration_line_asks_for_a_new_recording_of_that_line_only():
    container = _container()
    episode = _ready(container)
    wrong = Transcript(
        "ko",
        (
            *GOOD_READING.segments[:2],
            SpeechSegment(5000, 6400, "가격은 육천 원"),
            GOOD_READING.segments[3],
        ),
    )
    series = series_of(container)
    series.attach_narration.execute(
        AttachNarrationCommand(episode.episode_id, (RecordingInput(fake_recording(wrong)),))
    )
    result = _correct(container, episode)

    assert [(s.issue_id, s.status, s.method) for s in result.steps] == [
        ("narration:L3", StepStatus.NEEDS_RERECORD, CorrectionMethod.RERECORD)
    ]
    assert "가격은 오천 원." in result.steps[0].note
    # 그 줄만 다시 녹음해 붙이면 어긋남이 없어진다
    line = fake_recording(Transcript("ko", (SpeechSegment(100, 1400, "가격은 오천 원"),)))
    series.attach_narration.execute(
        AttachNarrationCommand(episode.episode_id, (RecordingInput(line, line_key="L3"),))
    )
    series.render_episode.execute(episode.episode_id)
    assert _check(container, episode).ok


def test_patch_given_to_an_overlay_issue_is_refused_not_ignored():
    """얹은 문구의 어긋남에 준 가리기는 말없이 무시됐다 (문구 수정 분기가 먼저 걸렀다)."""
    container = _container()
    episode = _tamper(container, _ready(container), "closeup:price", "말차 라떼 6,000원")
    patch = PatchRegion("overlay:closeup:price", Region(0.1, 0.7, 0.8, 0.1))

    with pytest.raises(ValidationError, match="화면 속 글자에만"):
        _correct(container, episode, patches=(patch,))
    assert series_of(container).episodes.get(episode.episode_id).plan == episode.plan
