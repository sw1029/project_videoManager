"""구조 규칙의 기준선 (ADR-0030): 지금 규칙에 걸리지만 당장 고칠 수 없는 것.

- 줄이기만 한다. 고쳐서 더는 걸리지 않으면 여기서 지운다 (지우지 않으면 테스트가 알려 준다).
- 새 코드는 처음부터 규칙을 지킨다. 게이트가 생기기 전에 갈라진 브랜치가 dev 에 들어올 때만,
  그 브랜치의 것을 이유와 함께 더할 수 있다.
- 발견 ID 는 docs/audit/2026-11-backend-audit.md 의 것이다.
"""

from __future__ import annotations

# 설명(docstring)이 없는 패키지 — K-03. 패키지를 옮기거나 고칠 때 채운다
PACKAGES_WITHOUT_DOCSTRING = frozenset(
    {
        "frame.adapters.structurer",  # #10 이 이 파일을 고친다. 머지 뒤에 채운다
    }
)

# 밖에서 쓰는 이름(__all__)을 적지 않은 패키지 — K-03. 모두 채웠다
PACKAGES_WITHOUT_ALL: frozenset[str] = frozenset()

# 500줄을 넘는 모듈 — M-01 (openrouter), M-05 (demo·bundle), M-03 (schemas)
LARGE_MODULES = frozenset(
    {
        "frame.adapters.generator.openrouter.generator",  # 순수한 부분은 뗐다. 클래스를 나누는 일이 남았다
        "frame.tools.demo",
        "frame.tools.bundle",
        "frame.presentation.schemas",
    }
)

# 옛 경로에 남긴 호환 모듈 (옛 경로 -> 구현이 있는 새 자리) — L-03, ADR-0031.
# 구현은 adapters/shared/ 로 옮겼고, 작업 중인 브랜치가 옛 경로를 import 하고 있어 이름만 다시 내보낸다.
# 그 브랜치들이 dev 에 들어가 import 를 새 경로로 고친 뒤 지운다
COMPAT_ADAPTER_MODULES: dict[str, tuple[str, ...]] = {
    "frame.adapters.common": ("frame.adapters.shared.runtime",),
    "frame.adapters.http_transport": ("frame.adapters.shared.http",),
    "frame.adapters.video_tools": ("frame.adapters.shared.media",),
    "frame.adapters.vlm_answers": ("frame.adapters.shared.vlm.answers",),
    # 클립 정규화는 같은 패키지의 normalizer 로, ffmpeg 찾기와 클립 검사는 공용 모듈로 갔다
    "frame.adapters.generator.ffmpeg": (
        "frame.adapters.generator.normalizer",
        "frame.adapters.shared.media",
    ),
}

# 호환 경로를 아직 import 하는 모듈 — 다른 브랜치가 고치고 있어 지금 고치지 않는 파일. 머지 뒤에 고치고 지운다
COMPAT_PATH_IMPORTERS = frozenset(
    {
        "frame.config.container",  # #10 · theme-series 가 고친다 (P-02)
    }
)
