"""FRAME 시연 준비·실행 진입점 (Windows / macOS / Linux). 사용법: python scripts/frame_demo.py --help"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from frame.tools.demo import main  # noqa: E402 - src 경로를 먼저 등록해야 한다

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:], root=ROOT))
