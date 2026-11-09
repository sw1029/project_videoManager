"""LLMKeyStore: 화면 입력 키(메모리) > 설정 키 > OPENAI_API_KEY 환경 변수 > 키 파일 순으로 키를 찾는다.

화면 입력 키는 프로세스 메모리에만 있고 DB·파일·로그에 쓰지 않는다. 서버를 다시 시작하면 사라진다.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Mapping
from pathlib import Path

from frame.application.ports.llm import LLMKeySource, LLMKeyStatus

from .keys import mask_key, read_key_file


class LLMKeyStore(LLMKeySource):
    def __init__(
        self,
        key_file: str | Path | None = None,
        env: Mapping[str, str] | None = None,
        explicit: str | None = None,
    ) -> None:
        self._key_file = Path(key_file) if key_file else None
        self._env = env
        self._explicit = (explicit or "").strip() or None
        self._runtime: str | None = None
        self._lock = threading.Lock()

    def __repr__(self) -> str:  # 키가 디버그 출력에 섞이지 않도록
        return f"LLMKeyStore(status={self.status()!r})"

    # ---- LLMKeySource ------------------------------------------------------
    def resolve(self) -> str | None:
        found = self._lookup()
        return found[0] if found else None

    def status(self) -> LLMKeyStatus:
        found = self._lookup()
        if found is None:
            return LLMKeyStatus(configured=False)
        key, source = found
        return LLMKeyStatus(configured=True, source=source, hint=mask_key(key))

    def set_runtime_key(self, key: str) -> None:
        value = (key or "").strip()
        if not value:
            raise ValueError("빈 키는 설정할 수 없습니다")
        with self._lock:
            self._runtime = value

    def clear_runtime_key(self) -> None:
        with self._lock:
            self._runtime = None

    def describe_missing(self) -> str:
        if self._key_file is not None and not self._key_file.is_file():
            return (
                f"키 파일이 없습니다: {self._key_file}. "
                "화면의 키 입력란이나 OPENAI_API_KEY 환경 변수로도 설정할 수 있습니다"
            )
        return (
            "OpenAI API 키가 설정되지 않았습니다. 화면의 키 입력란(FRAME_LLM_RUNTIME_KEY=true), "
            "OPENAI_API_KEY 환경 변수, FRAME_LLM_KEY_FILE 중 하나로 설정하세요"
        )

    # ---- 내부 --------------------------------------------------------------
    def _lookup(self) -> tuple[str, str] | None:
        with self._lock:
            runtime = self._runtime
        if runtime:
            return runtime, "runtime"
        if self._explicit:
            return self._explicit, "config"
        env = os.environ if self._env is None else self._env
        from_env = (env.get("OPENAI_API_KEY") or "").strip()
        if from_env:
            return from_env, "env"
        if self._key_file is not None:
            from_file = read_key_file(self._key_file)
            if from_file:
                return from_file, "file"
        return None
