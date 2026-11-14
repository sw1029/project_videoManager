"""품질 검사 입구 scripts/check.py (ADR-0030): 커버리지 하한의 판정과, 출력 때문에 검사가 죽지 않는 것.

검사 도구(ruff·mypy·pytest·coverage)는 실제로 돌리지 않고, 그 결과를 받아 판정하는 부분만 본다.
"""

from __future__ import annotations

import importlib.util
import io
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check.py"


@pytest.fixture
def check():
    spec = importlib.util.spec_from_file_location("frame_check_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tools(check, monkeypatch, *, pytest_code=0, totals=("90.00", "96.00", "98.00")):
    """검사 도구 자리에 정해 둔 결과를 넣는다. 부른 명령을 돌려준다."""
    calls: list[tuple[str, ...]] = []
    remaining = list(totals)

    def run(*args: str, capture: bool = False) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[0] == "pytest":
            return subprocess.CompletedProcess(args, pytest_code, "", "")
        if args[:2] == ("coverage", "report"):
            return subprocess.CompletedProcess(args, 0, remaining.pop(0) + "\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(check, "_python", run)
    return calls


def test_the_tests_step_passes_when_every_coverage_floor_is_met(check, monkeypatch, capsys):
    calls = _tools(check, monkeypatch)

    assert check.check_tests() is True

    printed = capsys.readouterr().out
    assert printed.count("통과") == len(check.COVERAGE_FLOORS) and "하한 미달" not in printed
    assert [call[:2] for call in calls].count(("coverage", "report")) == len(check.COVERAGE_FLOORS)


def test_the_tests_step_fails_when_coverage_drops_below_a_floor(check, monkeypatch, capsys):
    _tools(check, monkeypatch, totals=("90.00", "94.99", "98.00"))  # 제품 코드의 하한은 95

    assert check.check_tests() is False

    printed = capsys.readouterr().out
    assert printed.count("하한 미달") == 1 and "94.99%" in printed


def test_the_tests_step_fails_when_coverage_cannot_be_read(check, monkeypatch, capsys):
    _tools(check, monkeypatch, totals=("No data to report.", "96.00", "98.00"))

    assert check.check_tests() is False
    assert "No data to report." in capsys.readouterr().out


def test_failing_tests_fail_the_step_without_reading_coverage(check, monkeypatch):
    calls = _tools(check, monkeypatch, pytest_code=1)

    assert check.check_tests() is False
    assert [call[0] for call in calls] == ["pytest"]


def test_main_reports_which_checks_failed(check, monkeypatch, capsys):
    _tools(check, monkeypatch, pytest_code=1)

    assert check.main(["lint", "tests"]) == 1
    assert "실패: tests" in capsys.readouterr().out


def _cp949_stdout(monkeypatch) -> io.BytesIO:
    """한국어 Windows 에서 출력을 파일이나 파이프로 돌렸을 때의 표준 출력 (cp949, 못 담는 글자는 오류)."""
    raw = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="cp949", errors="strict"))
    return raw


def test_the_coverage_lines_can_be_written_to_a_cp949_output(check, monkeypatch):
    raw = _cp949_stdout(monkeypatch)
    _tools(check, monkeypatch)

    assert check.check_tests() is True

    sys.stdout.flush()
    assert raw.getvalue().decode("cp949").count("통과") == len(check.COVERAGE_FLOORS)


def test_a_character_the_output_cannot_show_does_not_stop_the_checks(check, monkeypatch):
    """검사 도구가 낸 글에 출력 인코딩이 못 담는 글자가 있어도 판정까지 간다."""
    raw = _cp949_stdout(monkeypatch)
    _tools(check, monkeypatch, totals=("측정값 없음 — ✗", "96.00", "98.00"))

    assert check.main(["tests"]) == 1

    sys.stdout.flush()
    printed = raw.getvalue().decode("cp949")
    assert "측정값 없음" in printed and "실패: tests" in printed
