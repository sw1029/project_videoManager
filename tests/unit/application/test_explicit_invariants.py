"""유스케이스가 실행 중에 기대는 것을 assert 가 아니라 명시적 오류로 알린다 (감사 C-04, R-05).

assert 는 `python -O` 에서 사라진다. 사라지면 방금 상태를 바꾼 버전이 다시 읽히지 않을 때 엉뚱한 곳에서
AttributeError 가 난다. 이런 일은 저장소가 어긋났을 때만 생기지만, 생겼을 때 무엇이 없었는지 말해야 한다.
"""

from __future__ import annotations

import pytest

from frame.application.pipeline.context import PipelineContext
from frame.domain.enums import VersionState
from frame.domain.errors import IntegrityViolation, NotFound, VersionNotRevisable
from frame.domain.models import VideoVersion
from tests.support.clock import ManualClock
from tests.support.factories import cafe_ir, version_of

NOW = ManualClock().now()


class _RowsVanish:
    """저장 단위를 감싼다: 버전을 allowed 번 읽은 뒤에는 그 버전이 없다고 답한다."""

    def __init__(self, factory, allowed: int) -> None:
        self._factory = factory
        self.left = allowed

    def __call__(self):
        return _Unit(self._factory(), self)


class _Unit:
    def __init__(self, inner, owner: _RowsVanish) -> None:
        self._inner, self._owner = inner, owner

    def __enter__(self):
        self._unit = self._inner.__enter__()
        return self

    def __exit__(self, *exc):
        return self._inner.__exit__(*exc)

    @property
    def versions(self):
        return _Versions(self._unit.versions, self._owner)

    def __getattr__(self, name):
        return getattr(self._unit, name)


class _Versions:
    def __init__(self, inner, owner: _RowsVanish) -> None:
        self._inner, self._owner = inner, owner

    def get(self, version_id):
        if self._owner.left <= 0:
            return None
        self._owner.left -= 1
        return self._inner.get(version_id)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def test_accepting_says_which_version_vanished_after_its_state_changed(container, draft):
    use_case = container.accept_generation
    use_case._uow_factory = _RowsVanish(container.uow_factory, allowed=1)

    with pytest.raises(NotFound, match=draft.version_id):
        use_case.execute(draft.version_id)


def test_running_says_which_version_vanished_after_its_state_changed(container, draft):
    container.accept_generation.execute(draft.version_id)
    use_case = container.run_generation_pipeline
    use_case._uow_factory = _RowsVanish(container.uow_factory, allowed=1)

    with pytest.raises(NotFound, match=draft.version_id):
        use_case.execute(draft.version_id)


def test_saving_a_result_says_which_version_is_missing(container, draft):
    use_case = container.run_generation_pipeline
    ctx = PipelineContext(version=version_of(container, draft.version_id))

    with pytest.raises(NotFound, match="ver_missing"):
        use_case._save_failure("ver_missing", "GENERATION_ERROR", "실패", None)
    with pytest.raises(NotFound, match="ver_missing"):
        use_case._record_success("ver_missing", ctx)


def test_a_finished_run_without_a_video_is_an_integrity_violation(container, draft):
    """조립 단계가 영상을 남기지 않았는데 끝났다고 기록하지 않는다."""
    ctx = PipelineContext(version=version_of(container, draft.version_id))

    with pytest.raises(IntegrityViolation, match="영상"):
        container.run_generation_pipeline._record_success(draft.version_id, ctx)


@pytest.mark.parametrize(
    "state", [VersionState.DRAFT, VersionState.COMPLETED, VersionState.FAILED], ids=str
)
def test_a_settled_version_with_an_ir_can_be_revised(state):
    ir = cafe_ir()
    version = VideoVersion("ver_1", "prj_1", state, NOW, ir=ir)

    assert version.ensure_revisable() is ir


@pytest.mark.parametrize(
    ("state", "has_ir"),
    [
        (VersionState.STRUCTURING, False),
        (VersionState.QUEUED, True),
        (VersionState.PROCESSING, True),
        (VersionState.FAILED, False),  # 구조화에 실패한 버전: 고칠 중간표현이 없다
    ],
    ids=["structuring", "queued", "processing", "failed-without-ir"],
)
def test_a_version_in_progress_or_without_an_ir_cannot_be_revised(state, has_ir):
    version = VideoVersion("ver_1", "prj_1", state, NOW, ir=cafe_ir() if has_ir else None)

    with pytest.raises(VersionNotRevisable) as caught:
        version.ensure_revisable()

    assert caught.value.code == "VERSION_NOT_REVISABLE"
