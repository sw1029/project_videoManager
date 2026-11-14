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
from frame.evaluation.edit_runner import run_case
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
    code = evaluate_edits.main(
        ["--cases", str(cases_path), "--out", str(out), *options], container=container
    )
    report = json.loads(out.read_text("utf-8")) if out.exists() else None
    return code, report


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
    assert report["complete"] is True
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


def test_report_is_saved_after_each_case(tmp_path):
    """비용이 드는 실행이 중간에 끊겨도 끝난 케이스의 결과는 남는다."""

    class Exploding:
        def __init__(self, inner):
            self.inner = inner

        def execute(self, command):
            if command.change.new_value == "black":
                raise RuntimeError("예상하지 못한 오류")
            return self.inner.execute(command)

    container = _container()
    parent = _parent(container)
    broken = dataclasses.replace(container, create_revision=Exploding(container.create_revision))
    with pytest.raises(RuntimeError):
        _run(tmp_path, broken, [_cup(parent), _cup(parent, case_id="second", new_value="black")])

    report = json.loads((tmp_path / "out" / "result.json").read_text("utf-8"))
    assert report["complete"] is False
    assert [c["case_id"] for c in report["cases"]] == ["cup"]


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
