"""시연 런처가 안내하는 길이 맞는지 본다 (감사 O-05).

런처는 서버를 FRAME_JOBS=inline 으로 띄운다. 분석 도중 끝난 실행은 STRUCTURING 버전을 남기고, 다음 실행의
기동 복구(ADR-0013)가 그 분석을 요청을 받기 전에 다시 실행한다. 기동이 오래 걸리면 런처는
"--offline 으로 한 번 실행하면 남은 분석이 정리된다" 고 안내한다.
"""

from __future__ import annotations

from frame.adapters.common import UuidIdGenerator
from frame.application.dto import CreateProjectCommand
from frame.config import Settings
from frame.domain.enums import VersionState
from frame.tools import demo
from tests.support.factories import CAFE_PROMPT, make_container, version_of

NATURAL_LANGUAGE = "카페에서 바리스타가 빨간 컵에 라떼를 따르는 20초 영상"


def _launcher_settings(root, *, offline: bool, **changed: str) -> Settings:
    """런처가 서버에 주는 설정 그대로 (demo_env). changed 는 지난 실행을 흉내 낼 때만 바꾼다."""
    env = demo.demo_env(root, offline=offline, base_env={}, key_file=None)
    env.update(changed)
    return Settings.from_env(env)


def _process(root, *, offline: bool, **changed: str):
    """같은 시연 폴더로 새로 조립한 서버 (메모리에 남은 것이 없는 새 프로세스)."""
    return make_container(
        _launcher_settings(root, offline=offline, **changed), ids=UuidIdGenerator()
    )


def _left_structuring(root, prompt: str) -> str:
    """지난 실행: 분석 작업이 돌기 전에 프로세스가 끝났다 (작업은 메모리에만 있었다)."""
    (root / demo.DEMO_DIR).mkdir(exist_ok=True)
    before = _process(root, offline=True, FRAME_JOBS="recording")
    created = before.create_project.execute(CreateProjectCommand("카페", prompt))
    assert version_of(before, created.version_id).state is VersionState.STRUCTURING
    return created.version_id


def test_an_unfinished_analysis_ends_when_the_demo_starts_offline_once(tmp_path):
    version_id = _left_structuring(tmp_path, NATURAL_LANGUAGE)

    offline = _process(tmp_path, offline=True)  # run --offline
    report = offline.recover_pending_versions.execute(startup=True)

    assert report.restructured == (version_id,)
    version = version_of(offline, version_id)
    # 자연어는 DSL 이 아니다: 원인과 함께 끝난다 (다시 분석을 요청하면 된다). STRUCTURING 으로 남지 않는다
    assert version.state is VersionState.FAILED and version.error_code
    # 다음 실행의 기동에는 다시 실행할 분석이 없다
    again = _process(tmp_path, offline=False)
    assert again.recover_pending_versions.execute(startup=True).total == 0


def test_an_unfinished_dsl_analysis_becomes_a_draft_when_the_demo_starts_offline(tmp_path):
    version_id = _left_structuring(tmp_path, CAFE_PROMPT)

    offline = _process(tmp_path, offline=True)
    offline.recover_pending_versions.execute(startup=True)

    assert version_of(offline, version_id).state is VersionState.DRAFT
