"""AdapterRegistry (Abstract Factory + Registry): 포트별로 '키 -> 팩토리' 를 등록한다.

새 모델/저장소를 붙이는 절차
1. 포트를 구현한 어댑터 클래스를 adapters/ 에 추가한다.
2. 그 포트의 등록 모듈(config/registrations/<포트>.py)의 register() 에 registry.register(Port, "키", factory)
   한 줄을 추가한다. (또는 플러그인에서 자기 레지스트리에 등록)
3. 설정(FRAME_GENERATOR=키 등)만 바꾸면 교체된다. 코드의 다른 곳은 바뀌지 않는다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from frame.application.errors import ConfigurationError
from frame.application.ports import ArtifactStore, Clock, IdGenerator, JobHandlerRegistry
from frame.application.ports.llm import LLMKeySource

from .settings import Settings

__all__ = ["AdapterRegistry", "BuildContext", "Factory", "default_registry"]


@dataclass
class BuildContext:
    """팩토리가 참조할 수 있는 공용 객체. (이미 만들어진 것만 담는다)"""

    settings: Settings
    ids: IdGenerator
    clock: Clock
    artifact_store: ArtifactStore | None = None
    handlers: JobHandlerRegistry | None = None
    llm_keys: LLMKeySource | None = None  # 화면 입력 키를 OpenAI 클라이언트와 설정 API 가 공유


Factory = Callable[[BuildContext], Any]


class AdapterRegistry:
    def __init__(self) -> None:
        self._factories: dict[tuple[str, str], Factory] = {}

    def register(self, port: type, key: str, factory: Factory) -> None:
        self._factories[(port.__name__, key)] = factory

    def build(self, port: type, key: str, ctx: BuildContext) -> Any:
        try:
            factory = self._factories[(port.__name__, key)]
        except KeyError as exc:
            raise ConfigurationError(
                f"{port.__name__} 에 '{key}' 어댑터가 등록되어 있지 않습니다. "
                f"사용 가능: {self.keys(port)}"
            ) from exc
        return factory(ctx)

    def keys(self, port: type) -> list[str]:
        return sorted(k for (p, k) in self._factories if p == port.__name__)


def default_registry() -> AdapterRegistry:
    """기본 어댑터를 모두 올린 레지스트리. 포트별 등록은 frame.config.registrations 에 있다."""
    # 등록 모듈은 어댑터를 import 한다. 이 모듈을 import 하는 것만으로 어댑터가 올라오지 않게 여기서 가져온다
    # (등록 모듈이 이 모듈의 AdapterRegistry·BuildContext 를 쓰므로 순환 import 도 피한다)
    from .registrations import register_defaults

    registry = AdapterRegistry()
    register_defaults(registry)
    return registry
