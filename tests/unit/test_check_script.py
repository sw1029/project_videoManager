"""품질 검사 입구 scripts/check.py (ADR-0030): 커버리지 하한의 판정과, 출력 때문에 검사가 죽지 않는 것.

검사 도구(ruff·mypy·pytest·coverage)는 실제로 돌리지 않고, 그 결과를 받아 판정하는 부분만 본다.
"""

from __future__ import annotations

import importlib.util
import io
import os
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


def _no_encoding_chosen(monkeypatch) -> None:
    """부르는 쪽이 인코딩을 정하지 않았다 (한국어 Windows 의 보통 상태)."""
    monkeypatch.delenv("PYTHONUTF8", raising=False)
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)


def test_output_sent_to_a_file_or_pipe_is_written_in_utf8(check, monkeypatch):
    """콘솔이 아닌 출력은 UTF-8 로 쓴다: 로그를 읽는 쪽이 인코딩을 짐작하지 않아도 되고, 어떤 글자도 잃지 않는다.

    한국어 Windows 에서 출력을 파일·파이프로 돌리면 표준 출력이 cp949 다. 예전에는 cp949 가 못 담는 글자('—')에서
    검사가 죽었고, 그다음에는 그 글자를 ? 로 바꿔 찍었다.
    """
    _no_encoding_chosen(monkeypatch)
    raw = _cp949_stdout(monkeypatch)
    _tools(check, monkeypatch, totals=("측정값 없음 — ✗", "96.00", "98.00"))

    assert check.main(["tests"]) == 1

    sys.stdout.flush()
    printed = raw.getvalue().decode("utf-8")
    assert "측정값 없음 — ✗" in printed and "실패: tests" in printed


@pytest.mark.parametrize("variable", ["PYTHONIOENCODING", "PYTHONUTF8"])
def test_an_encoding_the_caller_chose_is_kept_and_cannot_stop_the_checks(
    check, monkeypatch, variable
):
    """부르는 쪽이 인코딩을 정했으면 바꾸지 않는다. 그 인코딩이 못 담는 글자는 \\uXXXX 로 찍고 판정까지 간다."""
    _no_encoding_chosen(monkeypatch)
    monkeypatch.setenv(variable, "cp949" if variable == "PYTHONIOENCODING" else "0")
    raw = _cp949_stdout(monkeypatch)
    _tools(check, monkeypatch, totals=("측정값 없음 — ✗", "96.00", "98.00"))

    assert check.main(["tests"]) == 1

    sys.stdout.flush()
    printed = raw.getvalue().decode("cp949")
    assert "측정값 없음 \\u2014 \\u2717" in printed and "실패: tests" in printed


def test_the_tools_run_in_utf8_mode_unless_the_caller_chose_an_encoding(check, monkeypatch):
    """도구(ruff · mypy · pytest · coverage)는 UTF-8 모드로 돈다: 로컬과 CI 가 같은 조건이고, 로그가 한 인코딩이다."""
    _no_encoding_chosen(monkeypatch)
    assert check._environment()["PYTHONUTF8"] == "1"

    monkeypatch.setenv("PYTHONUTF8", "0")
    assert check._environment()["PYTHONUTF8"] == "0"

    monkeypatch.delenv("PYTHONUTF8")
    monkeypatch.setenv("PYTHONIOENCODING", "cp949")
    assert "PYTHONUTF8" not in check._environment()


def test_the_script_writes_utf8_when_its_output_is_piped():
    """실제로 돌려 본다. 한국어 Windows 에서는 고치기 전에 이 출력이 cp949 였다 (UTF-8 로 읽으면 깨진다)."""
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}

    done = subprocess.run(
        [sys.executable, str(SCRIPT), "--list"], capture_output=True, env=env, check=False
    )

    assert done.returncode == 0, done.stderr
    assert "포맷" in done.stdout.decode("utf-8")
