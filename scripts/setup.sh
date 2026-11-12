#!/usr/bin/env sh
# FRAME demo setup for macOS / Linux (needs Python 3.11+ and, for setup, Node.js 18+)
set -e
cd "$(dirname "$0")/.."
find_python() {
  for candidate in "${PYTHON:-}" python3.13 python3.12 python3.11 python3 python; do
    [ -n "$candidate" ] || continue
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}
PY="$(find_python)" || { echo "Python 3.11+ is required (macOS: brew install python@3.12, Ubuntu: sudo apt install python3.12 python3.12-venv)"; exit 1; }
exec "$PY" scripts/frame_demo.py setup "$@"
