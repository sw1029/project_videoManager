"""정규화(canonical) JSON 해시. 조건 고정·클립 재사용·캐시 키에 공통으로 쓴다."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_hash(payload: Any) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
