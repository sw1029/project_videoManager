"""로컬과 CI 가 같은 검사를 돌리는 한 가지 입구 (ADR-0030, docs/CI.md).

    python scripts/check.py              # 전부: format -> lint -> types -> tests
    python scripts/check.py lint types   # 고른 것만
    python scripts/check.py --list       # 검사 목록

- 이 저장소 폴더의 src 를 쓰도록 PYTHONPATH 를 맞춘다. 가상환경의 편집 설치가 다른 체크아웃(다른 worktree)을
  가리켜도 지금 폴더의 코드를 검사한다.
- 유료 API 를 부르지 않는다. pytest 는 기본으로 slow·gpu 테스트를 뺀다 (pyproject.toml 의 addopts).
- 커버리지 하한은 아래 COVERAGE_FLOORS 한 곳에 있다. 내려가면 실패하고, 올라가면 하한을 올린다.
- 출력을 파일이나 파이프로 돌려도 된다. 그 인코딩(한국어 Windows 는 cp949)이 못 담는 글자는 ? 로 찍는다.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATHS = ["src", "tests", "scripts"]

# (이름, coverage report 의 범위 인자, 하한 %). 2026-11-14 측정: 전체 90.3 · 제품 코드 95.6 · 핵심 97.6
COVERAGE_FLOORS: tuple[tuple[str, tuple[str, ...], float], ...] = (
    ("전체", (), 89.0),
    ("제품 코드 (tools 제외)", ("--omit=src/frame/tools/*",), 95.0),
    ("핵심 (domain·application)", ("--include=src/frame/domain/*,src/frame/application/*",), 97.0),
)


def _environment() -> dict[str, str]:
    env = dict(os.environ)
    inherited = env.get("PYTHONPATH")
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(ROOT / "src"), inherited]))
    return env


def _tolerant_output() -> None:
    """출력 인코딩이 못 담는 글자를 만나도 검사가 죽지 않게 한다 (그 글자만 ? 로 바뀐다)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="replace")


def _python(*args: str, capture: bool = False) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, "-m", *args]
    print("$ python -m " + " ".join(args), flush=True)
    return subprocess.run(
        command, cwd=ROOT, env=_environment(), text=True, capture_output=capture, check=False
    )


def check_format() -> bool:
    return _python("ruff", "format", "--check", *PATHS).returncode == 0


def check_lint() -> bool:
    return _python("ruff", "check", *PATHS).returncode == 0


def check_types() -> bool:
    return _python("mypy").returncode == 0


def check_tests(*, xml: bool = False) -> bool:
    if _python("pytest", "--cov=frame", "--cov-report=").returncode != 0:
        return False
    if xml:
        _python("coverage", "xml", "-o", "coverage.xml")
    passed = True
    for name, scope, floor in COVERAGE_FLOORS:
        report = _python(
            "coverage", "report", "--format=total", "--precision=2", *scope, capture=True
        )
        try:
            measured = float(report.stdout.strip())
        except ValueError:
            print(
                f"  커버리지를 읽지 못했습니다 ({name}): {report.stdout.strip()} {report.stderr.strip()}"
            )
            passed = False
            continue
        verdict = "통과" if measured >= floor else "하한 미달"
        print(f"  커버리지 {name}: {measured:.2f}% (하한 {floor:g}%) - {verdict}")
        passed = passed and measured >= floor
    return passed


STEPS: dict[str, tuple[str, Callable[[], bool]]] = {
    "format": ("포맷 (ruff format --check)", check_format),
    "lint": ("정적 검사 (ruff check)", check_lint),
    "types": ("타입 검사 (mypy)", check_types),
    "tests": ("테스트와 커버리지 하한 (pytest)", check_tests),
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FRAME 백엔드 품질 검사 (로컬·CI 공통)")
    parser.add_argument("steps", nargs="*", choices=[*STEPS, []], help="돌릴 검사 (없으면 전부)")
    parser.add_argument("--list", action="store_true", help="검사 목록만 보여 준다")
    parser.add_argument(
        "--xml", action="store_true", help="tests 뒤에 coverage.xml 을 쓴다 (CI 산출물)"
    )
    args = parser.parse_args(argv)
    _tolerant_output()
    if args.list:
        for name, (title, _) in STEPS.items():
            print(f"{name:8} {title}")
        return 0

    failed: list[str] = []
    for name in args.steps or list(STEPS):
        title, run = STEPS[name]
        print(f"\n== {title} ==", flush=True)
        ok = check_tests(xml=args.xml) if name == "tests" else run()
        if not ok:
            failed.append(name)
    print()
    if failed:
        print("실패: " + ", ".join(failed))
        return 1
    print("모든 검사 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
