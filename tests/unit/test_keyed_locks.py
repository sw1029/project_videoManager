"""키별 잠금 (ADR-0013): 같은 키의 작업은 한 번에 하나만. 기다리지 않고 얻어 보는 방법도 있다 (감사 A-09).

구현은 하나다 (감사 P-20). 같은 스레드가 다시 들어가야 하는 쪽(편 · 테마 문서의 잠금 - 유스케이스가 다른 유스케이스를
부른다)은 reentrant=True 로 만든다.
"""

from __future__ import annotations

import threading

import pytest

from frame.adapters.shared.locking import KeyedLocks

WAIT = 5.0  # 테스트가 멈춰 버리지 않게 하는 안전 상한


def test_the_same_key_is_held_by_one_holder_at_a_time():
    locks = KeyedLocks()
    inside, leave = threading.Event(), threading.Event()
    order: list[str] = []

    def first() -> None:
        with locks.hold("scene"):
            order.append("first in")
            inside.set()
            leave.wait(WAIT)
            order.append("first out")

    def second() -> None:
        with locks.hold("scene"):
            order.append("second in")

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    threads[0].start()
    assert inside.wait(WAIT)
    threads[1].start()
    leave.set()
    for thread in threads:
        thread.join(WAIT)

    assert order == ["first in", "first out", "second in"]


def test_a_free_key_is_taken_at_once_and_given_back():
    locks = KeyedLocks()

    with locks.try_hold("scene") as mine:
        assert mine is True
        with locks.try_hold("scene") as again:
            assert again is False  # 쥐고 있는 동안에는 같은 스레드도 다시 얻지 못한다
        with locks.try_hold("other") as other:
            assert other is True  # 다른 키는 상관없다

    with locks.try_hold("scene") as after:
        assert after is True


def test_a_key_someone_else_holds_is_not_waited_for():
    locks = KeyedLocks()
    inside, leave = threading.Event(), threading.Event()

    def holder() -> None:
        with locks.hold("scene"):
            inside.set()
            leave.wait(WAIT)

    thread = threading.Thread(target=holder)
    thread.start()
    try:
        assert inside.wait(WAIT)
        with locks.try_hold("scene") as mine:
            assert mine is False  # 기다리지 않고 바로 돌아온다
    finally:
        leave.set()
        thread.join(WAIT)

    with locks.try_hold("scene") as mine:
        assert mine is True  # 상대가 놓은 뒤에는 얻는다


def test_a_reentrant_lock_lets_the_thread_that_holds_it_in_again():
    """편 문서의 잠금: 화면 수정이 그 편의 키를 쥔 채 화면 검수(같은 키)를 부른다."""
    locks = KeyedLocks(reentrant=True)
    entered: list[str] = []

    with locks.hold("ep_1"):
        entered.append("outer")
        with locks.hold("ep_1"):
            entered.append("inner")
        with locks.try_hold("ep_1") as mine:
            assert mine is True  # 쥐고 있는 스레드는 기다리지 않는 쪽으로도 다시 얻는다

    assert entered == ["outer", "inner"]


def test_a_reentrant_lock_still_keeps_other_threads_out():
    locks = KeyedLocks(reentrant=True)
    inside, leave = threading.Event(), threading.Event()

    def holder() -> None:
        with locks.hold("ep_1"), locks.hold("ep_1"):
            inside.set()
            leave.wait(WAIT)

    thread = threading.Thread(target=holder)
    thread.start()
    try:
        assert inside.wait(WAIT)
        with locks.try_hold("ep_1") as mine:
            assert mine is False
    finally:
        leave.set()
        thread.join(WAIT)

    with locks.try_hold("ep_1") as mine:
        assert mine is True  # 두 번 들어간 스레드가 두 번 다 나온 뒤에는 얻는다


@pytest.mark.parametrize("reentrant", [False, True])
def test_a_lock_nobody_uses_is_forgotten(reentrant):
    """키는 장면 · 클립 · 편마다 생긴다. 쓰는 쪽이 없어진 잠금을 쌓아 두지 않는다."""
    locks = KeyedLocks(reentrant=reentrant)

    with locks.hold("a"), locks.hold("b"):
        assert locks.in_use == 2
        with locks.try_hold("a"):
            assert locks.in_use == 2

    assert locks.in_use == 0


def test_a_failed_try_leaves_the_holder_untouched():
    locks = KeyedLocks()

    with locks.hold("scene"):
        with locks.try_hold("scene") as mine:
            assert mine is False
        # 얻지 못한 쪽이 나가면서 남의 잠금을 풀지 않았다
        with locks.try_hold("scene") as still_held:
            assert still_held is False
