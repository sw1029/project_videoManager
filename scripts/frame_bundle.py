"""OS별 시연 번들(휴대용 Python + 화면 동봉) 만들기·확인 진입점. 사용법: python scripts/frame_bundle.py --help"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from frame.tools.bundle import main  # noqa: E402 - src 경로를 먼저 등록해야 한다

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:], root=ROOT))
