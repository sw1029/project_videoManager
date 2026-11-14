"""구조화 골드셋 기록을 견주고 다시 채점하는 도구 (US-06 AC-11, docs/STRUCTURING_DESIGN.md §10).

    python -m frame.tools.evaluate_structuring compare <기준 기록.json> <비교 기록.json>
    python -m frame.tools.evaluate_structuring rescore <기록.json> [--gold tests/evaluation/structuring_gold] [--write]

- compare: 대표 지표와 사례별 변화를 표로 낸다. 지표가 하나라도 나빠지면 종료 코드 1, 기록을 읽지 못하면 2.
  한쪽 기록에만 있는 사례가 있으면 그 사례를 밝힌다 (그때 대표 지표는 서로 다른 사례 집합의 값이다).
- rescore: 기록에 남은 원본 응답을 지금 골드로 다시 채점한다 (모델을 부르지 않는다).
  실행 때 실패한 사례는 재생에서도 실패하면 실행 때의 오류를 그대로 둔다.
  --write 를 주면 그 기록 파일을 다시 채점한 내용으로 고쳐 쓴다 (실행 정보는 그대로).
  골드 폴더가 없거나 비었거나, 기록의 사례가 골드에 없으면 아무것도 쓰지 않고 종료 코드 2 로 멈춘다.
  골드 폴더의 기본값은 지금 폴더 기준이라 저장소 루트에서 실행하거나 --gold 를 준다.
기록을 새로 만드는 실제 모델 실행은 tests/live/test_structuring_gold_live.py (FRAME_EVAL_STRUCTURING_LIVE=1) 다.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import sys
from datetime import datetime
from pathlib import Path

from frame.adapters.llm import ReplayLLMClient
from frame.adapters.structurer import LLMStructurer
from frame.evaluation.structuring import (
    compare_records,
    format_comparison,
    format_summary,
    load_gold_cases,
    read_record,
    rescore_record,
    save_record,
)

DEFAULT_GOLD_DIR = Path("tests") / "evaluation" / "structuring_gold"


def _replay(text: str) -> LLMStructurer:
    return LLMStructurer(ReplayLLMClient([text], model="replay"), max_attempts=1)


def _utf8_output() -> None:
    """출력이 파이프·파일로 갈 때 Windows(cp949)에서 표의 글자 때문에 실패하지 않게 UTF-8 로 맞춘다.

    tools/demo.py 와 같은 방식이다. 바꿀 수 없는 스트림(테스트의 가로채기 등)은 그대로 둔다.
    """
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            with contextlib.suppress(ValueError, OSError):
                stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    """종료 코드: 0 문제없음 · 1 회귀(compare) · 2 오류 (기록을 읽지 못함, 골드 폴더 문제 등)."""
    _utf8_output()
    parser = argparse.ArgumentParser(description="구조화 골드셋 기록을 견주고 다시 채점한다")
    commands = parser.add_subparsers(dest="command", required=True)
    compare = commands.add_parser("compare", help="두 기록을 견준다 (회귀가 있으면 종료 코드 1)")
    compare.add_argument("baseline", help="기준 기록 JSON")
    compare.add_argument("candidate", help="비교할 기록 JSON")
    rescore = commands.add_parser("rescore", help="기록의 원본 응답을 지금 골드로 다시 채점한다")
    rescore.add_argument("record", help="기록 JSON")
    rescore.add_argument("--gold", default=str(DEFAULT_GOLD_DIR), help="골드셋 폴더")
    rescore.add_argument("--write", action="store_true", help="기록 파일을 고쳐 쓴다")
    args = parser.parse_args(argv)

    if args.command == "compare":
        try:
            comparison = compare_records(read_record(args.baseline), read_record(args.candidate))
        except (OSError, ValueError, KeyError) as exc:
            print(f"[compare] 기록을 읽지 못했다: {exc}", file=sys.stderr)
            return 2
        print(format_comparison(comparison))
        return 1 if comparison.regressed else 0

    path = Path(args.record)
    try:
        record = read_record(path)
        rescored = rescore_record(
            record,
            load_gold_cases(args.gold),
            _replay,
            rescored_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        )
    except (OSError, ValueError, KeyError) as exc:
        print(
            f"[rescore] 멈춤 (아무것도 쓰지 않았다): {exc}\n"
            "  저장소 루트에서 실행하거나 --gold 로 골드 폴더를 주세요.",
            file=sys.stderr,
        )
        return 2
    print(format_summary(rescored))
    comparison = compare_records(record, rescored)
    gold = "골드 그대로" if comparison.same_gold else "골드가 바뀌었다"
    print(f"[rescore] 다시 채점 전 → 후 ({gold})")
    print(format_comparison(comparison, rescore_hint=False))
    if args.write:
        print(f"[rescore] 고쳐 씀: {save_record(rescored, path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
