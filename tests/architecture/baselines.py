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
        "frame.adapters.change_request",
        "frame.adapters.checker",
        "frame.adapters.generator",
        "frame.adapters.identity",
        "frame.adapters.jobs",
        "frame.adapters.persistence.sqlalchemy",
        "frame.adapters.storage",
        "frame.adapters.structurer",
        "frame.adapters.tracker",
        "frame.presentation.routers",
    }
)

# 밖에서 쓰는 이름(__all__)을 적지 않은 패키지 — K-03
PACKAGES_WITHOUT_ALL = frozenset(
    {
        "frame",
        "frame.adapters",
        "frame.adapters.persistence",
        "frame.application",
        "frame.domain",
        "frame.domain.services",
        "frame.presentation",
        "frame.tools",
    }
)

# 500줄을 넘는 모듈 — M-01 (openrouter), M-05 (demo·bundle), M-03 (schemas)
LARGE_MODULES = frozenset(
    {
        "frame.adapters.generator.openrouter",
        "frame.tools.demo",
        "frame.tools.bundle",
        "frame.presentation.schemas",
    }
)

# 어댑터 패키지들이 함께 쓰는 모듈인데 아직 adapters/shared/ 밖에 있는 것 — L-03. 옮기면 지운다
LEGACY_SHARED_ADAPTER_MODULES = frozenset(
    {
        "frame.adapters.common",
        "frame.adapters.http_transport",
        "frame.adapters.video_tools",
        "frame.adapters.vlm_answers",
    }
)
