"""OpenAI Chat Completions 기반 JSON 클라이언트 (구조화 출력 json_schema, strict).

- 키 해석 순서: 명시 인자 → OPENAI_API_KEY 환경 변수 → 키 파일(FRAME_LLM_KEY_FILE). 키는 로그·예외에 남기지 않는다.
- base_url 을 주면 OpenAI 호환 서버(vLLM, Ollama 등 로컬 3090 환경)를 같은 코드로 호출한다.
  로컬 서버가 strict json_schema 를 지원하지 않으면 strict=False 로 두고 호출자(LLMStructurer)의 검증·재시도에 맡긴다.
- 추론 모델(o*, gpt-5*)은 temperature 를 받지 않으므로 보내지 않는다.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from frame.application.errors import LLMBadResponse, LLMRateLimited, LLMTimeout, LLMUnavailable
from frame.application.ports.llm import JsonCompletion, LLMClient

_KEY_TOKEN = re.compile(r"sk-[A-Za-z0-9_\-]+")
_REASONING_PREFIXES = ("o1", "o3", "o4", "gpt-5")


def resolve_api_key(
    explicit: str | None = None,
    env: Mapping[str, str] | None = None,
    key_file: str | Path | None = None,
) -> str:
    """키를 찾아 돌려준다. 못 찾으면 LLMUnavailable (메시지에 키 내용은 절대 포함하지 않음)."""
    if explicit and explicit.strip():
        return explicit.strip()
    source = os.environ if env is None else env
    from_env = (source.get("OPENAI_API_KEY") or "").strip()
    if from_env:
        return from_env
    if key_file:
        path = Path(key_file)
        if not path.is_file():
            raise LLMUnavailable(f"키 파일이 없습니다: {path}")
        text = path.read_text(encoding="utf-8", errors="ignore")
        match = _KEY_TOKEN.search(text)
        if match:
            return match.group(0)
        for line in text.splitlines():
            if "=" in line:
                value = line.split("=", 1)[1].strip().strip("\"'")
                if value:
                    return value
        raise LLMUnavailable(f"키 파일에서 API 키를 찾지 못했습니다: {path}")
    raise LLMUnavailable(
        "OpenAI API 키가 없습니다. OPENAI_API_KEY 환경 변수 또는 FRAME_LLM_KEY_FILE 을 설정하세요"
    )


def mask_key(key: str) -> str:
    return f"{key[:3]}...{key[-4:]}" if len(key) > 8 else "***"


def is_reasoning_model(model: str) -> bool:
    return model.lower().startswith(_REASONING_PREFIXES)


class OpenAIJsonClient(LLMClient):
    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        key_file: str | Path | None = None,
        base_url: str | None = None,
        timeout_s: float = 60.0,
        temperature: float | None = 0.0,
        strict: bool = True,
        sdk_client: Any | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url
        self.timeout_s = timeout_s
        self.temperature = temperature
        self.strict = strict
        if sdk_client is None:
            import openai  # 지연 import: openai 패키지는 llm extra

            key = resolve_api_key(api_key, env, key_file)
            sdk_client = openai.OpenAI(api_key=key, base_url=base_url, timeout=timeout_s)
            self.key_hint = mask_key(key)  # 진단 출력용 (키 자체는 보관하지 않는다)
        else:
            self.key_hint = "(injected)"
        self._sdk = sdk_client

    def complete_json(
        self,
        system: str,
        user: str,
        json_schema: Mapping[str, Any],
        *,
        schema_name: str = "response",
        timeout_s: float | None = None,
    ) -> JsonCompletion:
        import openai

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "schema": dict(json_schema),
                    "strict": self.strict,
                },
            },
        }
        if self.temperature is not None and not is_reasoning_model(self.model):
            kwargs["temperature"] = self.temperature
        if timeout_s is not None:
            kwargs["timeout"] = timeout_s

        try:
            response = self._sdk.chat.completions.create(**kwargs)
        except openai.APITimeoutError as exc:
            raise LLMTimeout(f"OpenAI 응답 시간 초과 ({timeout_s or self.timeout_s}s)") from exc
        except openai.APIConnectionError as exc:
            raise LLMUnavailable(f"OpenAI 연결 실패: {type(exc).__name__}") from exc
        except openai.RateLimitError as exc:
            raise LLMRateLimited("OpenAI 요청 한도 초과 (429)") from exc
        except openai.AuthenticationError as exc:
            raise LLMUnavailable("OpenAI 인증 실패 (401): API 키를 확인하세요") from exc
        except openai.APIStatusError as exc:
            raise LLMUnavailable(f"OpenAI 오류 {exc.status_code}: {_safe_message(exc)}") from exc

        message = response.choices[0].message
        refusal = getattr(message, "refusal", None)
        if refusal:
            raise LLMBadResponse(f"모델이 응답을 거부했습니다: {refusal}")
        content = message.content
        if not content:
            raise LLMBadResponse("빈 응답을 받았습니다")
        usage = getattr(response, "usage", None)
        return JsonCompletion(
            text=content,
            model=getattr(response, "model", None) or self.model,
            input_tokens=getattr(usage, "prompt_tokens", None),
            output_tokens=getattr(usage, "completion_tokens", None),
        )


def _safe_message(exc: Exception) -> str:
    return str(exc)[:300].replace("\n", " ")
