"""지표 1 평가 CLI (python -m frame.tools.evaluate_edits): Fake 어댑터로 케이스 파일 → 결과 JSON 과 목표 판정."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from frame.adapters.checker import FakeConsistencyChecker, FakeRequirementChecker
from frame.application.dto import CreateProjectCommand
from frame.config import Settings
from frame.domain.enums import RevisionKind, VersionState
from frame.evaluation.edit_cases import parse_cases
from frame.evaluation.edit_runner import CaseInterrupted, run_case
from frame.tools import evaluate_edits
from tests.support.checkers import MethodPreservationChecker
from tests.support.factories import (
    CAFE_PROMPT,
    create_draft,
    dispatcher_of,
    generate,
    make_container,
)


def _container(*, ssim=0.97, keep=1.0, failing=(), jobs="inline"):
    """vlm_preservation·ssim_preservation 을 함께 켠 것처럼 method 를 남기는 보존 검수기 둘."""
    return make_container(
        Settings(jobs=jobs, persistence="memory"),
        checkers=(
            FakeRequirementChecker(failing_keys=failing),
            FakeConsistencyChecker(),
            MethodPreservationChecker("vlm_keep", keep),
            MethodPreservationChecker("ssim", ssim),
        ),
    )


def _parent(container) -> str:
    """원본 영상 (COMPLETED). 인라인 디스패처라 접수하면 끝까지 돈다."""
    created = container.create_project.execute(
        CreateProjectCommand(title="카페 신제품 숏폼", prompt=CAFE_PROMPT)
    )
    container.accept_generation.execute(created.version_id)
    assert container.queries.get_version(created.version_id).version.state is VersionState.COMPLETED
    return created.version_id


def _cup(parent, **fields):
    case = {
        "case_id": "cup",
        "parent_version_id": parent,
        "entity_key": "cup",
        "predicate": "color",
        "new_value": "teal",
    }
    case.update(fields)
    return case


def _run(tmp_path, container, cases, *options):
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(json.dumps({"cases": cases}, ensure_ascii=False), "utf-8")
    out = tmp_path / "out" / "result.json"
    # 기다리는 시간을 짧게: 수정 버전이 끝나지 않는 회귀가 생기면 한 시간 기다리지 않고 바로 드러난다
    wait = ["--timeout-s", "2", "--poll-s", "0.05"]
    code = evaluate_edits.main(
        ["--cases", str(cases_path), "--out", str(out), *wait, *options], container=container
    )
    report = json.loads(out.read_text("utf-8")) if out.exists() else None
    return code, report


def _run_expecting_ctrl_c_handled(tmp_path, container, cases, *options):
    """main 이 Ctrl+C 를 잡지 못하는 회귀는 pytest 세션 중단이 아니라 이 테스트의 실패로 드러나게 한다."""
    try:
        return _run(tmp_path, container, cases, *options)
    except KeyboardInterrupt:
        pytest.fail("main 이 Ctrl+C(KeyboardInterrupt)를 잡지 않았다")


def test_runs_each_case_and_judges_the_target(tmp_path):
    container = _container()
    parent = _parent(container)
    code, report = _run(
        tmp_path,
        container,
        [
            _cup(parent),
            _cup(
                parent,
                case_id="shirt",
                entity_key="barista",
                predicate="shirt_color",
                new_value="black",
            ),
            _cup(
                parent, case_id="light", entity_key="cafe", predicate="lighting", new_value="cool"
            ),
        ],
    )

    assert code == 0
    assert report["complete"] is True and report["interrupted"] is None
    summary = report["summary"]
    assert (summary["cases"], summary["completed"], summary["errors"]) == (3, 3, 0)
    assert summary["basis"] == report["basis"] == "vlm_keep"
    assert summary["success_rate"] == 1.0
    assert summary["mean_non_target_change_rate"] == 0.0
    assert summary["meets_target"] is True
    assert report["targets"] == {"success_rate": 0.8, "non_target_change_rate": 0.1}

    cup = report["cases"][0]
    assert cup["state"] == "COMPLETED" and cup["error"] is None
    assert cup["revision_version_id"] not in (None, parent)
    assert cup["input"]["predicate"] == "color" and cup["input"]["new_value"] == "teal"
    assert cup["plan"]["kind"] == "CHANGE"
    assert sorted(cup["plan"]["target_scene_keys"]) == ["closeup", "intro"]
    assert sorted(cup["changed_scene_keys"]) == ["closeup", "intro"]
    assert sorted(cup["target_requirement_keys"]) == ["closeup:cup:color", "intro:cup:color"]
    assert cup["outcome"] == {"success": True, "target_met": True, "non_target_change_rate": 0.0}


def test_report_keeps_clip_uris_and_check_details_as_evidence(tmp_path):
    container = _container()
    parent = _parent(container)
    _, report = _run(tmp_path, container, [_cup(parent)])
    cup = report["cases"][0]

    clips = {c["scene_key"]: c for c in cup["clips"]}
    assert clips["outro"]["reused"] is True and clips["outro"]["target"] is False
    assert clips["outro"]["uri"] == clips["outro"]["parent_uri"]
    assert clips["intro"]["reused"] is False and clips["intro"]["target"] is True
    assert clips["intro"]["uri"] != clips["intro"]["parent_uri"]
    assert cup["video_uri"]

    preservation = [c for c in cup["checks"] if c["check_type"] == "PRESERVATION"]
    assert {c["detail"]["method"] for c in preservation} == {"vlm_keep", "ssim", "identical"}
    target = [c for c in cup["checks"] if c["requirement_key"] == "intro:cup:color"]
    assert [c["verdict"] for c in target] == ["PASS"]
    assert target[0]["detail"]["predicate"] == "color"


def test_report_leaves_no_local_folder_paths(tmp_path):
    """결과 파일은 저장소에 기록한다 (TDD_WORKFLOW §7): 이 컴퓨터의 폴더 경로를 남기지 않는다."""
    container = _container()
    parent = _parent(container)
    _, report = _run(tmp_path, container, [_cup(parent)])

    assert report["cases_file"] == "cases.json"  # 케이스 파일은 이름만
    assert "artifact_dir" not in report["settings"]  # 산출물 폴더는 종류(artifact_store)만
    assert report["settings"]["artifact_store"] == "memory"
    assert tmp_path.name not in (tmp_path / "out" / "result.json").read_text("utf-8")


def test_both_bases_are_recorded_and_the_option_picks_the_headline(tmp_path):
    container = _container(
        ssim=0.5
    )  # 다시 만든 장면의 SSIM 이 낮다 (새로 생성돼 구도가 달라진 것처럼)
    parent = _parent(container)
    _, report = _run(tmp_path, container, [_cup(parent)], "--preservation", "ssim")

    summary = report["summary"]
    assert summary["basis"] == report["basis"] == "ssim"
    assert summary["meets_target"] is False
    assert summary["by_basis"]["vlm_keep"]["meets_target"] is True
    assert summary["by_basis"]["ssim"]["meets_target"] is False
    assert summary["by_basis"]["ssim"]["mean_non_target_change_rate"] == pytest.approx(
        1 - 2.0 / 3, abs=1e-6
    )
    cup = report["cases"][0]
    assert cup["by_basis"]["vlm_keep"]["success"] is True
    assert cup["by_basis"]["ssim"]["success"] is False
    assert cup["outcome"] == cup["by_basis"]["ssim"]


def test_basis_whose_checker_is_off_is_undetermined_and_flagged(tmp_path, capsys):
    """ssim_preservation 을 켜지 않았으면 재사용 장면의 1.0 만으로 '변경 없음' 이 되지 않는다."""
    container = make_container(
        Settings(jobs="inline", persistence="memory"),
        checkers=(FakeRequirementChecker(), MethodPreservationChecker("vlm_keep", 1.0)),
    )
    parent = _parent(container)
    _, report = _run(tmp_path, container, [_cup(parent)], "--preservation", "ssim")

    ssim = report["summary"]["by_basis"]["ssim"]
    assert ssim["measured_cases"] == 0 and ssim["mean_non_target_change_rate"] is None
    assert report["summary"]["meets_target"] is False
    assert report["summary"]["by_basis"]["vlm_keep"]["meets_target"] is True
    assert "ssim_preservation" in capsys.readouterr().out


def test_kind_is_left_to_the_plan_and_keep_becomes_the_revision_intent(tmp_path):
    container = _container()
    parent = _parent(container)
    keep = [{"aspect": "APPEARANCE", "entity_key": "barista"}, {"aspect": "BACKGROUND"}]
    _, report = _run(tmp_path, container, [_cup(parent, new_value="red", keep=keep)])

    case = report["cases"][0]
    assert case["plan"]["kind"] == "REPAIR"  # 지금 값(red)과 같으면 계획이 다시 맞추기로 정한다
    revision = container.queries.get_version(case["revision_version_id"]).version
    assert revision.intent.kind is RevisionKind.REPAIR
    assert [item.to_dict() for item in revision.intent.keep] == [
        {"aspect": "APPEARANCE", "entity_key": "barista"},
        {"aspect": "BACKGROUND", "entity_key": None},
    ]
    assert case["outcome"]["success"] is True


def test_case_whose_target_requirement_fails_is_not_a_success(tmp_path):
    container = _container(failing=("intro:cup:color",))
    parent = _parent(container)
    _, report = _run(tmp_path, container, [_cup(parent)])

    outcome = report["cases"][0]["outcome"]
    assert outcome["target_met"] is False and outcome["success"] is False
    assert report["summary"]["success_rate"] == 0.0
    assert report["summary"]["meets_target"] is False


def test_failing_case_is_recorded_and_the_run_continues(tmp_path):
    container = _container()
    parent = _parent(container)
    code, report = _run(tmp_path, container, [_cup("ver_missing", case_id="missing"), _cup(parent)])

    assert code == 0
    missing, cup = report["cases"]
    assert missing["error"]["code"] == "NOT_FOUND"
    assert missing["state"] is None and missing["revision_version_id"] is None
    assert missing["outcome"]["success"] is False
    assert cup["outcome"]["success"] is True
    summary = report["summary"]
    assert (summary["cases"], summary["errors"], summary["success_rate"]) == (2, 1, 0.5)


def test_unexpected_error_in_one_case_is_recorded_and_the_rest_still_run(tmp_path, caplog):
    """DomainError·AdapterError 가 아닌 오류(DB 잠금 등)도 그 케이스에만 남고, 남은 유료 케이스는 계속 돈다."""

    class BrokenPlan:
        def __init__(self, inner):
            self.inner = inner

        def execute(self, command):
            if command.change.new_value == "black":
                raise RuntimeError("database is locked")
            return self.inner.execute(command)

    container = _container()
    parent = _parent(container)
    broken = dataclasses.replace(container, plan_revision=BrokenPlan(container.plan_revision))
    code, report = _run(
        tmp_path, broken, [_cup(parent, case_id="first", new_value="black"), _cup(parent)]
    )

    assert code == 0 and report["complete"] is True
    first, cup = report["cases"]
    assert first["error"] == {
        "code": "UNEXPECTED_ERROR",
        "message": "RuntimeError: database is locked",
    }
    assert first["plan"] is None and first["revision_version_id"] is None
    assert first["outcome"]["success"] is False
    assert cup["state"] == "COMPLETED" and cup["outcome"]["success"] is True
    assert (report["summary"]["errors"], report["summary"]["success_rate"]) == (1, 0.5)
    # 원인을 찾을 수 있게 트레이스백은 로그로 남긴다
    assert any(r.exc_info and "first" in r.getMessage() for r in caplog.records)


def test_plan_only_reports_the_plan_without_creating_revisions(tmp_path):
    container = _container()
    parent = _parent(container)
    project_id = container.queries.get_version(parent).version.project_id
    before = len(container.queries.get_project(project_id).versions)

    code, report = _run(tmp_path, container, [_cup(parent)], "--plan-only")

    assert code == 0
    assert len(container.queries.get_project(project_id).versions) == before
    assert report["plan_only"] is True
    case = report["cases"][0]
    assert case["revision_version_id"] is None
    assert sorted(case["plan"]["target_scene_keys"]) == ["closeup", "intro"]
    summary = report["summary"]
    assert summary["meets_target"] is None
    assert summary["regenerate_scenes"] == 2


def test_plan_only_case_that_ends_without_a_plan_fails_loudly(tmp_path):
    """오류 없이 끝났는데 계획이 없으면(유스케이스 계약 위반) 진행 줄을 지어내지 않고 멈춘다.

    python -O 에서도 사라지지 않는 명시적 확인이다. 계획만 보는 실행이라 과금은 없다.
    """

    class NoPlan:
        def execute(self, command):
            return None

    container = _container()
    parent = _parent(container)
    broken = dataclasses.replace(container, plan_revision=NoPlan())

    with pytest.raises(RuntimeError, match="변경 계획이 없습니다"):
        _run(tmp_path, broken, [_cup(parent)], "--plan-only")


def test_report_is_rewritten_after_each_case(tmp_path):
    """창을 닫거나 프로세스가 죽어도(Ctrl+C 가 아닌 끊김) 끝난 케이스는 남는다.

    다음 케이스를 시작할 때 앞 케이스가 이미 결과 파일에 쓰여 있다.
    """
    out = tmp_path / "out" / "result.json"
    seen = []

    class PeekingPlan:
        def __init__(self, inner):
            self.inner = inner

        def execute(self, command):
            seen.append(json.loads(out.read_text("utf-8")) if out.exists() else None)
            return self.inner.execute(command)

    container = _container()
    parent = _parent(container)
    peeking = dataclasses.replace(container, plan_revision=PeekingPlan(container.plan_revision))
    code, _ = _run(
        tmp_path, peeking, [_cup(parent), _cup(parent, case_id="second", new_value="black")]
    )

    assert code == 0
    before_first, before_second = seen
    assert before_first is None
    assert before_second["complete"] is False
    assert [c["case_id"] for c in before_second["cases"]] == ["cup"]


class _InterruptingAccept:
    """n 번째 생성 접수에서 Ctrl+C. 그 케이스의 수정 버전은 이미 만들어져 있다."""

    def __init__(self, inner, *, on_call):
        self.inner, self.on_call, self.calls = inner, on_call, 0

    def execute(self, version_id):
        self.calls += 1
        if self.calls == self.on_call:
            raise KeyboardInterrupt
        return self.inner.execute(version_id)


def test_ctrl_c_saves_finished_cases_and_the_interrupted_revision_then_exits_130(tmp_path, capsys):
    """비용이 드는 실행을 중간에 멈춰도 끝난 케이스와, 하던 케이스에서 만든 수정 버전이 결과에 남는다."""
    container = _container()
    parent = _parent(container)
    broken = dataclasses.replace(
        container, accept_generation=_InterruptingAccept(container.accept_generation, on_call=2)
    )
    code, report = _run_expecting_ctrl_c_handled(
        tmp_path,
        broken,
        [
            _cup(parent),
            _cup(parent, case_id="second", new_value="black"),
            _cup(parent, case_id="third", new_value="white"),
        ],
    )

    assert code == 130
    assert report["complete"] is False
    assert [c["case_id"] for c in report["cases"]] == ["cup"]  # third 는 시작하지 않았다
    assert report["summary"]["cases"] == 1
    interrupted = report["interrupted"]
    assert interrupted["case_id"] == "second"
    revision = interrupted["revision_version_id"]
    assert container.queries.get_version(revision).version.state is VersionState.DRAFT
    assert interrupted["state"] == "DRAFT"  # 접수 전에 멈췄다 → 서버가 다시 실행하지 않는다
    err = capsys.readouterr().err
    assert "중단" in err and revision in err and "접수 전" in err


def test_ctrl_c_inside_a_case_carries_what_was_done():
    container = _container()
    parent = _parent(container)
    (spec,) = parse_cases([_cup(parent)])
    broken = dataclasses.replace(
        container, accept_generation=_InterruptingAccept(container.accept_generation, on_call=1)
    )

    # 예상 밖 오류와 달리 케이스에 묻지 않고, 그때까지의 기록을 담아 올라간다
    with pytest.raises(KeyboardInterrupt) as info:
        run_case(broken, spec)

    assert isinstance(info.value, CaseInterrupted)
    run = info.value.run
    assert run.spec is spec and run.plan is not None
    assert run.revision_version_id is not None and run.error_code is None


def test_example_case_file_runs_on_the_cafe_example(tmp_path):
    """README 의 예시 케이스 파일이 낡지 않았는지: 부모 ID 만 바꾸면 카페 예시에서 모두 끝까지 돈다."""
    example = Path(__file__).parents[2] / "docs" / "evaluation" / "edit_cases.example.json"
    data = json.loads(example.read_text("utf-8"))
    container = _container()
    parent = _parent(container)
    for case in data["cases"]:
        case["parent_version_id"] = parent

    _, report = _run(tmp_path, container, data["cases"])

    assert [c["error"] for c in report["cases"]] == [None] * len(data["cases"])
    assert report["summary"]["completed"] == len(data["cases"]) == 6
    kinds = {c["case_id"]: c["plan"]["kind"] for c in report["cases"]}
    assert kinds["cup-repair-closeup"] == "REPAIR" and kinds["cup-color-teal"] == "CHANGE"
    navy = next(c for c in report["cases"] if c["case_id"] == "shirt-color-navy-outro")
    assert navy["changed_scene_keys"] == ["outro"]
    assert report["summary"]["meets_target"] is True


def test_invalid_case_file_exits_with_2_and_writes_nothing(tmp_path, capsys):
    code, report = _run(tmp_path, _container(), [{"parent_version_id": "ver_1"}])
    assert code == 2 and report is None
    assert "predicate" in capsys.readouterr().err


def test_memory_persistence_is_rejected_because_parent_versions_live_elsewhere(tmp_path, capsys):
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps([_cup("ver_1")]), "utf-8")
    code = evaluate_edits.main(["--cases", str(cases), "--out", str(tmp_path / "r.json")], env={})
    assert code == 2
    assert "FRAME_PERSISTENCE" in capsys.readouterr().err
    assert not (tmp_path / "r.json").exists()


# ---- 완료 대기 ----


class _Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_wait_polls_until_the_version_finishes():
    container = _container(jobs="recording")
    parent = generate(container, create_draft(container).version_id).version_id
    (spec,) = parse_cases([_cup(parent)])
    dispatcher = dispatcher_of(container)
    clock = _Clock()

    def sleep(seconds):  # 기다리는 동안 다른 워커가 작업을 끝낸다
        clock.sleep(seconds)
        dispatcher.run_all()

    run = run_case(container, spec, timeout_s=60, poll_s=5, sleep=sleep, monotonic=clock.monotonic)

    assert run.error_code is None
    assert run.version.state is VersionState.COMPLETED
    assert clock.now == 5  # 한 번 기다렸다
    assert run.results


def test_wait_gives_up_after_the_timeout_and_records_the_case():
    container = _container(jobs="recording")
    parent = generate(container, create_draft(container).version_id).version_id
    (spec,) = parse_cases([_cup(parent)])
    clock = _Clock()

    run = run_case(
        container, spec, timeout_s=30, poll_s=10, sleep=clock.sleep, monotonic=clock.monotonic
    )

    assert run.error_code == "EVALUATION_WAIT_TIMEOUT"
    assert run.revision_version_id is not None
    assert run.version.state is VersionState.QUEUED
    assert run.results == ()


def test_unexpected_error_after_the_revision_was_made_keeps_its_version_id():
    """생성까지 끝난(과금된) 뒤 검수 결과를 읽다 예상 밖 오류가 나도 수정 버전을 찾을 수 있다."""

    class BrokenChecks:
        def __init__(self, inner):
            self.inner = inner

        def get_version(self, version_id):
            return self.inner.get_version(version_id)

        def get_checks(self, version_id):
            raise LookupError("검수 결과를 읽지 못함")

    container = _container()
    parent = _parent(container)
    (spec,) = parse_cases([_cup(parent)])
    broken = dataclasses.replace(container, queries=BrokenChecks(container.queries))

    run = run_case(broken, spec)

    assert run.error_code == "UNEXPECTED_ERROR"
    assert run.error_message == "LookupError: 검수 결과를 읽지 못함"
    assert run.revision_version_id is not None
    assert run.version.state is VersionState.COMPLETED
    assert run.results == ()


class _FailingAfterAccept:
    """생성 접수(DRAFT → QUEUED)까지 한 뒤 예외. 그 뒤의 일(결과 저장 등)에서 난 오류를 흉내 낸다."""

    def __init__(self, inner):
        self.inner = inner

    def execute(self, version_id):
        self.inner.execute(version_id)
        raise RuntimeError("database is locked")


def test_error_after_accepting_records_the_state_the_revision_was_left_in():
    """접수 뒤 오류로 끝난 케이스도 수정 버전의 마지막 상태를 남긴다 (서버를 켜면 다시 실행될 버전을 찾게)."""
    container = _container(jobs="recording")  # 접수만 하고 작업은 돌지 않는다 → QUEUED 로 남는다
    parent = generate(container, create_draft(container).version_id).version_id
    (spec,) = parse_cases([_cup(parent)])
    broken = dataclasses.replace(
        container, accept_generation=_FailingAfterAccept(container.accept_generation)
    )

    run = run_case(broken, spec)

    assert run.error_message == "RuntimeError: database is locked"
    assert run.version is not None
    assert run.version.version_id == run.revision_version_id
    assert run.version.state is VersionState.QUEUED


def test_revision_that_cannot_be_read_again_keeps_the_original_error():
    class ParentOnly:
        """부모는 읽히고 수정 버전은 읽히지 않는다."""

        def __init__(self, inner, parent):
            self.inner, self.parent = inner, parent

        def get_version(self, version_id):
            if version_id != self.parent:
                raise RuntimeError("다시 읽지 못함")
            return self.inner.get_version(version_id)

        def get_checks(self, version_id):
            return self.inner.get_checks(version_id)

    container = _container(jobs="recording")
    parent = generate(container, create_draft(container).version_id).version_id
    (spec,) = parse_cases([_cup(parent)])
    broken = dataclasses.replace(
        container,
        accept_generation=_FailingAfterAccept(container.accept_generation),
        queries=ParentOnly(container.queries, parent),
    )

    run = run_case(broken, spec)

    assert run.error_message == "RuntimeError: database is locked"
    assert run.revision_version_id is not None and run.version is None


def test_revision_left_unfinished_by_an_error_is_flagged(tmp_path, capsys):
    """오류로 끝났는데 수정 버전이 접수된 채 남으면, 서버를 켜면 다시 실행된다(과금)는 것을 알린다."""
    container = _container(jobs="recording")
    parent = generate(container, create_draft(container).version_id).version_id
    broken = dataclasses.replace(
        container, accept_generation=_FailingAfterAccept(container.accept_generation)
    )

    code, report = _run(tmp_path, broken, [_cup(parent)])

    assert code == 0
    case = report["cases"][0]
    assert case["error"]["code"] == "UNEXPECTED_ERROR"
    assert case["state"] == "QUEUED"
    assert report["summary"]["unfinished_versions"] == [
        {"case_id": "cup", "revision_version_id": case["revision_version_id"], "state": "QUEUED"}
    ]
    captured = capsys.readouterr()
    assert "서버를 켜면" in captured.err  # 진행 줄
    assert "서버를 켜면" in captured.out and case["revision_version_id"] in captured.out  # 요약
