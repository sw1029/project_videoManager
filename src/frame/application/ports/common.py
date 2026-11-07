"""테스트 결정성을 위해 분리한 작은 포트: ID 생성기, 시계."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime


class IdGenerator(ABC):
    @abstractmethod
    def new_id(self, prefix: str = "") -> str: ...


class Clock(ABC):
    @abstractmethod
    def now(self) -> datetime: ...
