"""설정값은 출처 하나 (ADR-0032): 설정에서 오는 값을 다른 곳에 다시 적지 않는다.

조립(frame/config)은 어댑터 · 유스케이스를 만들 때 설정값을 인자로 넘긴다. 그 인자에 생성자가 자기 기본값을
또 갖고 있으면 같은 값이 두 곳에 있는 것이다: 설정의 기본값을 바꿔도 생성자의 것은 그대로 남고, 생성자를 직접 쓰는
코드와 테스트는 운영과 다른 값으로 돈다.

    규칙 1  조립이 설정에서 넘기는 인자에는 생성자 기본값을 두지 않는다 ('없음' 을 뜻하는 None 은 된다).
    규칙 2  설정의 주소 · 모델 기본값(이름이 _url · _model 로 끝나는 항목)은 frame/config 밖의 코드에 다시 적지 않는다.

짝(어느 설정 항목이 어느 생성자의 어느 인자로 가는가)은 조립 코드를 읽어 스스로 찾는다. 표를 손으로 맞추지 않는다.
지금 규칙 1 에 걸리는 것은 baselines.SETTINGS_ARGUMENTS_WITH_DEFAULTS 에 있고 줄이기만 한다.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib
import inspect
from functools import cache
from typing import Any, NamedTuple

import pytest

from frame.config.settings import Settings
from frame.config.theme_settings import ThemeSettings
from tests.architecture.baselines import SETTINGS_ARGUMENTS_WITH_DEFAULTS
from tests.architecture.imports import ROOT, SOURCE, modules

CONFIG = "frame.config"
# 조립 코드에서 설정 객체를 가리키는 이름: ctx.settings · w.settings · s (= ctx.settings), w.ts · ts (테마 설정)
SETTINGS_NAMES = {"settings": Settings, "s": Settings, "ts": ThemeSettings}
ADDRESS_FIELD_ENDINGS = ("_url", "_model")


class Wire(NamedTuple):
    """조립이 설정 항목 하나를 생성자의 인자 하나로 넘기는 자리."""

    target: str  # 생성자의 이름 (클래스 이름)
    parameter: str
    owner: str  # Settings | ThemeSettings
    field: str
    default: Any  # 생성자가 가진 기본값 (없으면 inspect.Parameter.empty)
    where: str

    @property
    def key(self) -> str:
        return f"{self.target}.{self.parameter}"


def _settings_field(node: ast.AST) -> tuple[str, str] | None:
    """`ctx.settings.x` · `s.x` · `w.ts.x` 꼴이면 (설정 클래스 이름, 항목 이름)."""
    if not isinstance(node, ast.Attribute):
        return None
    base = ast.unparse(node.value).split(".")[-1]
    owner = SETTINGS_NAMES.get(base)
    if owner is None or node.attr not in {field.name for field in dataclasses.fields(owner)}:
        return None
    return owner.__name__, node.attr


def _imported(tree: ast.Module, module: str) -> dict[str, tuple[str, str]]:
    """이 모듈이 `from x import Name` 으로 가져온 이름 -> (모듈, 이름). 함수 안의 import 도 본다."""
    package = module.rsplit(".", 1)[0]
    found: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        source = node.module or ""
        if node.level:
            parts = package.split(".")
            source = ".".join(
                [*parts[: len(parts) - node.level + 1], *([source] if source else [])]
            )
        for alias in node.names:
            found[alias.asname or alias.name] = (source, alias.name)
    return found


@cache
def wires() -> tuple[Wire, ...]:
    found: dict[tuple[str, str, str, str], Wire] = {}
    for module, path in sorted(modules().items()):
        if not (module == CONFIG or module.startswith(CONFIG + ".")):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = _imported(tree, module if path.name != "__init__.py" else module + ".__init__")
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            origin = names.get(node.func.id)
            if origin is None:
                continue
            try:
                target = getattr(importlib.import_module(origin[0]), origin[1])
            except (ImportError, AttributeError):
                continue
            if not inspect.isclass(target):
                continue
            try:
                parameters = inspect.signature(target).parameters
            except (TypeError, ValueError):
                continue  # 서명을 읽을 수 없는 것 (예외 클래스 등)
            positional = [
                name for name, p in parameters.items() if p.kind is p.POSITIONAL_OR_KEYWORD
            ]
            given = [(name, arg) for name, arg in zip(positional, node.args, strict=False)]
            given += [(kw.arg, kw.value) for kw in node.keywords if kw.arg is not None]
            for name, value in given:
                field = _settings_field(value)
                if field is None or name not in parameters:
                    continue
                where = f"{path.relative_to(ROOT).as_posix()}:{node.lineno}"
                wire = Wire(target.__name__, name, *field, parameters[name].default, where)
                found.setdefault((wire.target, wire.parameter, wire.owner, wire.field), wire)
    return tuple(found.values())


def test_the_wiring_is_found():
    """조립 코드를 읽는 방법이 깨지면 아래 검사들이 아무것도 보지 않고 통과한다. 그렇게 되지 않게 한다."""
    targets = {wire.target for wire in wires()}

    assert len(wires()) >= 40
    assert {"OpenRouterVideoGenerator", "OpenAIJsonClient", "FfmpegShotDetector"} <= targets


def test_arguments_that_come_from_settings_have_no_default_of_their_own():
    with_default = {
        wire.key: wire
        for wire in wires()
        if wire.default is not inspect.Parameter.empty and wire.default is not None
    }
    new = sorted(set(with_default) - set(SETTINGS_ARGUMENTS_WITH_DEFAULTS))
    fixed = sorted(set(SETTINGS_ARGUMENTS_WITH_DEFAULTS) - set(with_default))

    if new:
        pytest.fail(
            "설정에서 오는 값인데 생성자가 기본값을 또 갖고 있다 (기본값을 지우고, 값은 조립이 넘긴다):\n  "
            + "\n  ".join(
                f"{key} = {with_default[key].default!r}  <- {with_default[key].owner}."
                f"{with_default[key].field}  ({with_default[key].where})"
                for key in new
            ),
            pytrace=False,
        )
    assert not fixed, f"이제 기본값이 없다. 기준선에서 지운다: {fixed}"


def test_address_and_model_defaults_are_written_only_in_the_config():
    values: dict[str, str] = {}
    for owner in (Settings, ThemeSettings):
        defaults = owner()
        for field in dataclasses.fields(owner):
            value = getattr(defaults, field.name)
            if isinstance(value, str) and value and field.name.endswith(ADDRESS_FIELD_ENDINGS):
                values[value] = f"{owner.__name__}.{field.name}"
    assert values  # 볼 것이 없으면 검사가 뜻이 없다

    copies = []
    for module, path in sorted(modules().items()):
        if module == CONFIG or module.startswith(CONFIG + "."):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        copies += [
            f"{path.relative_to(SOURCE.parent.parent).as_posix()}:{node.lineno}: "
            f"{node.value!r}  ({values[node.value]} 의 기본값)"
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in values
        ]

    if copies:
        pytest.fail(
            "설정의 주소 · 모델 기본값이 frame/config 밖에 다시 적혀 있다 (그 값은 설정에서 받아 쓴다):\n  "
            + "\n  ".join(copies),
            pytrace=False,
        )
