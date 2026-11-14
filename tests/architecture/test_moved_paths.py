"""옮긴 모듈의 옛 경로 (ADR-0031 3절): 걷어낸 경로는 다시 생기지 않고, 아무도 그 경로로 import 하지 않는다.

모듈을 옮길 때 작업 중인 브랜치가 그 경로를 쓰고 있으면 옛 경로에 이름만 다시 내보내는 호환 모듈을 남겼다.
그 브랜치들이 dev 에 들어간 뒤 호환 모듈을 걷어냈다 (2026-11-15).
쓰는 브랜치가 없는 모듈은 옛 경로를 남기지 않고 옮긴다.
옛 브랜치를 가져오다 아래 경로가 다시 생기거나 쓰이면 이 테스트가 실패한다 — 표의 새 자리로 고친다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.architecture.imports import ROOT, imported_names, modules

# 걷어낸 옛 경로 -> 지금 쓰는 자리. 모듈이면 그 아래 이름까지, 이름 하나면 그 이름만 막는다
MOVED = {
    # 어댑터 공용 모듈 (L-03)
    "frame.adapters.common": "frame.adapters.shared.runtime",
    "frame.adapters.http_transport": "frame.adapters.shared.http",
    "frame.adapters.video_tools": "frame.adapters.shared.media 의 frames · probe · similarity · ffmpeg",
    "frame.adapters.vlm_answers": "frame.adapters.shared.vlm.answers",
    "frame.adapters.generator.ffmpeg": (
        "클립 정규화 frame.adapters.generator.normalizer, "
        "ffmpeg 찾기 frame.adapters.shared.media.ffmpeg, 클립 검사 frame.adapters.shared.media.probe"
    ),
    # 조립기 포트는 생성기 포트와 다른 모듈이다 (K-04)
    "frame.application.ports.generator.VideoAssembler": "frame.application.ports.assembler.VideoAssembler",
    # 키를 실어 보낼 주소의 판정은 어댑터들이 함께 쓴다 (빌려 쓰던 안쪽 이름)
    "frame.adapters.generator.openrouter._checked_base_url": "frame.adapters.shared.http.secure_base_url",
    # 수정 의도와 그 규칙 (M-07)
    "frame.domain.services.propagation": "frame.domain.revision",
    "frame.domain.services.revision_planning": "frame.domain.revision.resolution",
    # 형식(도메인 서비스)으로 묶은 패키지를 없앴다 (K-05)
    "frame.domain.services.identity": "frame.domain.identity",
    "frame.domain.services.comparison": "frame.domain.comparison",
    "frame.domain.services": "frame.domain.identity 와 frame.domain.comparison",
}

# src 밖에서 frame 을 import 하는 곳
OTHER_FOLDERS = ("tests", "scripts")


def _moved_from(name: str) -> str | None:
    return next((old for old in MOVED if name == old or name.startswith(old + ".")), None)


def _files() -> list[tuple[Path, str | None]]:
    """(파일, 모듈 이름). src 밖의 파일은 모듈 이름이 없다 (상대 import 를 풀지 않는다)."""
    inside: list[tuple[Path, str | None]] = [(path, name) for name, path in modules().items()]
    outside: list[tuple[Path, str | None]] = [
        (path, None) for folder in OTHER_FOLDERS for path in sorted((ROOT / folder).rglob("*.py"))
    ]
    return inside + outside


def test_removed_paths_do_not_come_back():
    back = [f"{old}  ->  {MOVED[old]}" for old in MOVED if old in modules()]

    if back:
        pytest.fail(
            "걷어낸 옛 경로에 모듈이 다시 생겼다 (새 자리의 것을 쓴다):\n  " + "\n  ".join(back),
            pytrace=False,
        )


def test_nothing_imports_a_removed_path():
    wrong: dict[tuple[str, int, str], None] = {}
    for path, importer in _files():
        for line, name in imported_names(path, importer):
            old = _moved_from(name)
            if old is not None:
                wrong[(path.relative_to(ROOT).as_posix(), line, old)] = None

    if wrong:
        lines = [f"{path}:{line}  {old}  ->  {MOVED[old]}" for path, line, old in wrong]
        pytest.fail(
            "걷어낸 옛 경로로 import 한다 (새 자리로 고친다):\n  " + "\n  ".join(lines),
            pytrace=False,
        )


def test_the_table_points_at_places_that_exist():
    """새 자리로 적은 모듈이 실제로 있다 (표가 낡지 않게)."""
    missing = []
    for old, new in MOVED.items():
        mentioned = [word.strip(",") for word in new.split() if word.startswith("frame.")]
        for name in mentioned:
            module = name if name in modules() else name.rpartition(".")[0]
            if module not in modules():
                missing.append(f"{old}  ->  {name}")

    assert missing == [], "표의 새 자리가 없다"
