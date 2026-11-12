"""VIDEO_VERSION 실행 기록 (#2, ERD 확장): 접수·시작 시각과 실행 시도 횟수."""

from datetime import UTC, datetime, timedelta

from frame.domain.enums import VersionState as S
from frame.domain.models import VideoVersion

T0 = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)


def test_new_version_has_no_execution_record_and_keeps_its_positional_signature():
    version = VideoVersion("ver_1", "prj_1", S.DRAFT, T0, "ver_0")
    assert version.parent_version_id == "ver_0"  # 새 필드가 위치 인자 순서를 바꾸지 않는다
    assert (version.queued_at, version.started_at, version.attempt) == (None, None, 0)


def test_queue_and_start_are_recorded_and_every_start_counts_an_attempt():
    version = VideoVersion("ver_1", "prj_1", S.QUEUED, T0)
    version.record_queued(T0 + timedelta(seconds=1))
    version.record_started(T0 + timedelta(seconds=2))
    assert version.queued_at == T0 + timedelta(seconds=1)
    assert (version.started_at, version.attempt) == (T0 + timedelta(seconds=2), 1)

    version.record_started(T0 + timedelta(minutes=5))  # 워커 유실 뒤 다시 시작
    assert (version.started_at, version.attempt) == (T0 + timedelta(minutes=5), 2)
    assert version.queued_at == T0 + timedelta(seconds=1)
