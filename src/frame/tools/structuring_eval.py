"""구조화 골드셋 기록을 견주고 다시 채점하는 도구 (US-06 AC-11, docs/STRUCTURING_DESIGN.md §10).

    python -m frame.tools.structuring_eval compare <기준 기록.json> <비교 기록.json>
    python -m frame.tools.structuring_eval rescore <기록.json> [--gold tests/evaluation/structuring_gold] [--write]

- compare: 대표 지표와 사례별 변화를 표로 낸다. 지표가 하나라도 나빠지면 종료 코드 1.
- rescore: 기록에 남은 원본 응답을 지금 골드로 다시 채점한다 (모델을 부르지 않는다).
  --write 를 주면 그 기록 파일을 다시 채점한 내용으로 고쳐 쓴다 (실행 정보는 그대로).
기록을 새로 만드는 실제 모델 실행은 `pytest -m slow tests/evaluation` 이다.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from frame.adapters.llm import ReplayLLMClient
from frame.adapters.structurer import LLMStructurer
from frame.evaluation.structuring_gold import (
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


def main(argv: list[str] | None = None) -> int:
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
        comparison = compare_records(read_record(args.baseline), read_record(args.candidate))
        print(format_comparison(comparison))
        return 1 if comparison.regressed else 0

    path = Path(args.record)
    record = read_record(path)
    rescored = rescore_record(
        record,
        load_gold_cases(args.gold),
        _replay,
        rescored_at=datetime.now().astimezone().isoformat(timespec="seconds"),
    )
    print(format_summary(rescored))
    print(format_comparison(compare_records(record, rescored)))
    if args.write:
        print(f"[rescore] 고쳐 씀: {save_record(rescored, path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
