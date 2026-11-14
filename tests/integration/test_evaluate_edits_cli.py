"""지표 1 평가 CLI 를 환경 변수(FRAME_*)로 조립해, 서버가 SQLite 파일·산출물 폴더에 남긴 원본을 수정·평가한다."""

from __future__ import annotations

import json

from frame.application.dto import CreateProjectCommand
from frame.config import Settings, build_container
from frame.domain.enums import VersionState
from frame.tools import evaluate_edits
from tests.support.factories import CAFE_PROMPT


def _env(tmp_path):
    return {
        "FRAME_PERSISTENCE": "sqlalchemy",
        "FRAME_DB_URL": f"sqlite+pysqlite:///{(tmp_path / 'frame.db').as_posix()}",
        "FRAME_ARTIFACT_STORE": "local_fs",
        "FRAME_ARTIFACT_DIR": str(tmp_path / "artifacts"),
        "FRAME_JOBS": "thread",  # CLI 는 인라인으로 바꿔 돈다
    }


def test_cli_evaluates_cases_against_the_configured_database(tmp_path, capsys):
    env = _env(tmp_path)
    # 서버가 원본을 만들어 둔 상태 (같은 DB·산출물 폴더)
    server = build_container(Settings.from_env({**env, "FRAME_JOBS": "inline"}))
    created = server.create_project.execute(
        CreateProjectCommand(title="카페 신제품 숏폼", prompt=CAFE_PROMPT)
    )
    server.accept_generation.execute(created.version_id)
    parent = created.version_id

    cases = tmp_path / "cases.json"
    cases.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_id": "cup-teal",
                        "parent_version_id": parent,
                        "entity_key": "cup",
                        "predicate": "색상",
                        "new_value": "teal",
                    },
                    {
                        "case_id": "light-cool",
                        "parent_version_id": parent,
                        "entity_key": "cafe",
                        "predicate": "lighting",
                        "new_value": "cool",
                        "keep": [{"aspect": "APPEARANCE", "entity_key": "barista"}],
                    },
                ]
            },
            ensure_ascii=False,
        ),
        "utf-8",
    )
    out = tmp_path / "result.json"

    code = evaluate_edits.main(["--cases", str(cases), "--out", str(out)], env=env)

    assert code == 0
    report = json.loads(out.read_text("utf-8"))
    summary = report["summary"]
    assert (summary["cases"], summary["completed"], summary["errors"]) == (2, 2, 0)
    assert summary["meets_target"] is True
    # Fake 보존 검수기는 method 를 남기지 않아 두 기준에 똑같이 들어간다
    assert summary["by_basis"]["vlm_keep"] == summary["by_basis"]["ssim"]
    assert report["settings"]["persistence"] == "sqlalchemy"
    assert report["settings"]["artifact_store"] == "local_fs"
    # 결과 파일은 저장소에 기록한다: DB 주소·산출물 폴더·케이스 파일의 폴더(모두 tmp_path 아래)를 남기지 않는다
    assert "db_url" not in report["settings"] and "artifact_dir" not in report["settings"]
    assert report["cases_file"] == "cases.json"
    assert tmp_path.name not in out.read_text("utf-8")
    assert report["checkers"] == [
        "FakeRequirementChecker",
        "FakeConsistencyChecker",
        "FakePreservationChecker",
    ]
    # 수정 버전과 클립은 같은 DB·산출물 폴더에 남아 서버에서도 보인다
    for case in report["cases"]:
        version = server.queries.get_version(case["revision_version_id"]).version
        assert version.state is VersionState.COMPLETED
        assert version.parent_version_id == parent
        for clip in case["clips"]:
            assert server.artifact_store.exists(clip["uri"])
    assert report["cases"][0]["input"]["predicate"] == "color"
    assert "목표 달성" in capsys.readouterr().out
