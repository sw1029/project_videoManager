"""구조 규칙의 기준선 (ADR-0030): 지금 규칙에 걸리지만 당장 고칠 수 없는 것.

- 줄이기만 한다. 고쳐서 더는 걸리지 않으면 여기서 지운다 (지우지 않으면 테스트가 알려 준다).
- 새 코드는 처음부터 규칙을 지킨다. 게이트가 생기기 전에 갈라진 브랜치가 dev 에 들어올 때만,
  그 브랜치의 것을 이유와 함께 더할 수 있다.
- 발견 ID 는 docs/audit/2026-11-backend-audit.md 의 것이다.
"""

from __future__ import annotations

# 설명(docstring)이 없는 패키지 — K-03. 모두 채웠다
PACKAGES_WITHOUT_DOCSTRING: frozenset[str] = frozenset()

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

# 설정에서 오는 인자인데 생성자가 기본값을 또 가진 것 ("생성자.인자") — P-19, ADR-0032.
# 모두 고쳤다. 새로 더하지 않는다 (기본값을 지우고 값은 조립이 넘긴다)
SETTINGS_ARGUMENTS_WITH_DEFAULTS: frozenset[str] = frozenset()
