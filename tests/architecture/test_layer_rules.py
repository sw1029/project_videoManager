"""계층 규칙 (ADR-0030, docs/ARCHITECTURE.md §3.1): 의존은 안쪽으로만 향한다.

    presentation -> application -> domain
    adapters     -> application(ports·errors) / domain
    config       -> 조립 지점 (모든 계층)
    evaluation   -> domain / application        tools -> 모든 계층

예외 목록은 없다. 규칙을 바꾸려면 ADR 을 고친다.
"""

from __future__ import annotations

import sys

from tests.architecture.imports import (
    TOP,
    external_imports,
    internal_imports,
    layer_of,
    listing,
    modules,
)

# 계층 -> 그 계층이 import 해도 되는 계층
ALLOWED_LAYERS: dict[str, frozenset[str]] = {
    TOP: frozenset(),
    "domain": frozenset({"domain"}),
    "application": frozenset({"domain", "application"}),
    "adapters": frozenset({"domain", "application", "adapters"}),
    "presentation": frozenset({"domain", "application", "config", "presentation", TOP}),
    "config": frozenset({"domain", "application", "adapters", "config"}),
    "evaluation": frozenset({"domain", "application", "evaluation"}),
    "tools": frozenset(
        {"domain", "application", "adapters", "presentation", "config", "evaluation", "tools", TOP}
    ),
}

# 어댑터가 application 에서 가져와도 되는 것: 포트와 어댑터 오류 계층
ADAPTER_APPLICATION_SURFACE = ("frame.application.ports", "frame.application.errors")

# 외부 패키지 -> 그것을 import 해도 되는 곳. 새 외부 패키지를 쓰려면 여기에 자리를 적는다
THIRD_PARTY_HOMES: dict[str, tuple[str, ...]] = {
    "fastapi": ("frame.presentation",),
    "starlette": ("frame.presentation",),
    "pydantic": ("frame.presentation",),
    "sqlalchemy": ("frame.adapters.persistence",),
    "openai": ("frame.adapters.llm",),
    "imageio_ffmpeg": ("frame.adapters",),
    "faster_whisper": ("frame.adapters.speech",),  # 선택 설치 (speech-local): 로컬 음성 인식
}


def _within(module: str, prefixes: tuple[str, ...]) -> bool:
    return any(module == prefix or module.startswith(prefix + ".") for prefix in prefixes)


def test_dependencies_point_inward():
    wrong = [
        item
        for item in internal_imports()
        if layer_of(item.imported) not in ALLOWED_LAYERS.get(layer_of(item.importer), frozenset())
    ]

    assert not wrong, "허용되지 않은 계층 방향의 import:" + listing(wrong)


def test_every_layer_has_a_rule():
    """새 최상위 패키지를 만들면 계층 규칙을 함께 정해야 한다."""
    layers = {layer_of(name) for name in modules()}

    assert layers - set(ALLOWED_LAYERS) == set(), "계층 규칙이 없는 최상위 패키지"


def test_adapters_use_only_ports_and_errors_of_the_application():
    wrong = [
        item
        for item in internal_imports()
        if layer_of(item.importer) == "adapters"
        and layer_of(item.imported) == "application"
        and not _within(item.imported, ADAPTER_APPLICATION_SURFACE)
    ]

    assert not wrong, "어댑터는 application.ports 와 application.errors 만 쓴다:" + listing(wrong)


def test_the_domain_uses_only_the_standard_library():
    standard = sys.stdlib_module_names
    wrong = [
        item
        for item in external_imports()
        if layer_of(item.importer) == "domain" and item.imported.split(".")[0] not in standard
    ]

    assert not wrong, "도메인은 표준 라이브러리만 쓴다:" + listing(wrong)


def test_third_party_packages_stay_in_their_layer():
    standard = sys.stdlib_module_names
    wrong = []
    for item in external_imports():
        package = item.imported.split(".")[0]
        if package in standard:
            continue
        homes = THIRD_PARTY_HOMES.get(package)
        if homes is None or not _within(item.importer, homes):
            wrong.append(item)

    assert not wrong, "외부 패키지가 정해진 자리 밖에서 쓰였다 (THIRD_PARTY_HOMES):" + listing(
        wrong
    )
