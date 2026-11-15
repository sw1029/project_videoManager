"""로컬 파일 저장소의 쓰기 (감사 A-09): 임시 파일에 다 쓴 뒤 바꿔 끼운다.

작업 원장·생성 캐시의 기록이 이 저장소에 있다. 쓰는 도중 끊겨 잘린 기록이 남으면 다음 요청이 그것을 '없음' 으로 읽고
같은 장면을 다시 제출한다 (재과금). 그래서 읽는 쪽은 예전 내용이나 새 내용만 봐야 한다.
"""

from __future__ import annotations

import os
import threading

import pytest

from frame.adapters.storage import LocalFileArtifactStore, local_fs
from frame.application.errors import StorageError

KEY = "ledger/openrouter/abc.json"
SUBMITTED = b'{"state": "submitted"}'
COMPLETED = b'{"state": "completed"}'


def _files(folder):
    return sorted(path.name for path in folder.iterdir())


def test_put_leaves_only_the_artifact_itself(tmp_path):
    store = LocalFileArtifactStore(tmp_path)

    store.put(KEY, SUBMITTED)
    uri = store.put(KEY, COMPLETED)

    assert store.get(uri) == COMPLETED
    assert _files(tmp_path / "ledger" / "openrouter") == ["abc.json"]


def test_a_failed_write_keeps_the_previous_content(tmp_path, monkeypatch):
    """디스크가 차거나 쓰는 도중 끊겨도 앞서 남긴 접수 기록은 온전하다."""
    store = LocalFileArtifactStore(tmp_path)
    uri = store.put(KEY, SUBMITTED)

    def no_space(source, target):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(local_fs.os, "replace", no_space)

    with pytest.raises(StorageError) as raised:
        store.put(KEY, COMPLETED)

    assert store.get(uri) == SUBMITTED
    assert _files(tmp_path / "ledger" / "openrouter") == ["abc.json"]  # 임시 파일도 남기지 않는다
    assert str(tmp_path) not in str(raised.value)  # 서버의 경로를 오류 문구에 싣지 않는다


def test_put_waits_out_a_reader_that_briefly_holds_the_file(tmp_path, monkeypatch):
    """Windows 는 누군가 그 파일을 읽는 동안 바꿔 끼우기를 거절한다. 잠깐 기다렸다 다시 한다."""
    store = LocalFileArtifactStore(tmp_path)
    store.put(KEY, SUBMITTED)
    replace, attempts = os.replace, []

    def held_twice(source, target):
        attempts.append(target)
        if len(attempts) <= 2:
            raise PermissionError(13, "다른 프로세스가 파일을 사용 중입니다")
        replace(source, target)

    monkeypatch.setattr(local_fs.os, "replace", held_twice)

    uri = store.put(KEY, COMPLETED)

    assert store.get(uri) == COMPLETED and len(attempts) == 3


def test_put_gives_up_when_the_file_stays_locked(tmp_path, monkeypatch):
    store = LocalFileArtifactStore(tmp_path)
    uri = store.put(KEY, SUBMITTED)

    def always_held(source, target):
        raise PermissionError(13, "다른 프로세스가 파일을 사용 중입니다")

    monkeypatch.setattr(local_fs.os, "replace", always_held)
    monkeypatch.setattr(local_fs, "_BUSY_WAIT_S", 0.05)  # 기다리는 시간을 줄여 본다

    with pytest.raises(StorageError):
        store.put(KEY, COMPLETED)

    assert store.get(uri) == SUBMITTED
    assert _files(tmp_path / "ledger" / "openrouter") == ["abc.json"]


def test_readers_never_see_a_partly_written_artifact(tmp_path):
    """한 스레드가 계속 덮어쓰는 동안 다른 스레드가 읽어도, 읽은 내용은 늘 온전한 한 벌이다."""
    store = LocalFileArtifactStore(tmp_path)
    first, second = b"a" * 200_000, b"b" * 300_000
    uri = store.put("clips/v1/intro.mp4", first)
    stop = threading.Event()
    seen: list[bytes] = []
    failures: list[BaseException] = []

    def read() -> None:
        while not stop.is_set():
            try:
                seen.append(store.get(uri))
            except BaseException as exc:  # noqa: BLE001 - 스레드의 실패를 단언으로 옮긴다
                failures.append(exc)
                return

    reader = threading.Thread(target=read)
    reader.start()
    try:
        for index in range(60):
            store.put("clips/v1/intro.mp4", second if index % 2 == 0 else first)
    finally:
        stop.set()
        reader.join(timeout=10)

    assert failures == []
    assert seen and all(content in (first, second) for content in seen)
