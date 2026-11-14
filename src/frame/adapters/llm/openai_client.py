"""OpenAI Chat Completions 기반 JSON 클라이언트 (구조화 출력 json_schema, strict).

- 키는 LLMKeySource 에서 호출 시점에 얻는다: 화면 입력 키 > 설정 키 > OPENAI_API_KEY > 키 파일.
  키가 바뀌면 SDK 클라이언트를 새로 만든다. 키는 로그·예외에 남기지 않는다.
- require_key_at_start=True(기본)면 생성 시점에 키가 없을 때 바로 실패한다 (서버 기동 단계 조기 실패).
  화면 키 입력(FRAME_LLM_RUNTIME_KEY)을 쓰면 False 로 두어 키 없이 기동하고, 키가 없을 때의 호출만 실패시킨다.
- base_url 을 주면 OpenAI 호환 서버(vLLM, Ollama 등 로컬 3090 환경)를 같은 코드로 호출한다.
  로컬 서버가 strict json_schema 를 지원하지 않으면 strict=False 로 두고 호출자(LLMStructurer)의 검증·재시도에 맡긴다.
- 추론 모델(o*, gpt-5*)은 temperature 를 받지 않으므로 보내지 않는다.
- images 를 주면 user 메시지를 글 + 이미지(data URI) 조각으로 보낸다 (이미지를 읽는 모델이어야 한다).
- 실패는 모두 LLMError 로 알린다: SDK 가 내는 오류 전부와, 응답에 choices·message 가 없거나 본문이 글이 아닌 경우까지.
  다른 예외가 새면 부르는 쪽(검수기·구조화기)이 '판정 불가·구조화 실패' 로 다루지 못한다.
"""

from __future__ import annotations

import base64
import hashlib
import os
import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from frame.application.errors import LLMBadResponse, LLMRateLimited, LLMTimeout, LLMUnavailable
from frame.application.ports.llm import ImageInput, JsonCompletion, LLMClient, LLMKeySource

from .key_store import LLMKeyStore
from .keys import mask_key, read_key_file

__all__ = ["OpenAIJsonClient", "is_reasoning_model", "mask_key", "resolve_api_key"]

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
        found = read_key_file(path)
        if found:
            return found
        raise LLMUnavailable(f"키 파일에서 API 키를 찾지 못했습니다: {path}")
    raise LLMUnavailable(
        "OpenAI API 키가 없습니다. OPENAI_API_KEY 환경 변수 또는 FRAME_LLM_KEY_FILE 을 설정하세요"
    )


def is_reasoning_model(model: str) -> bool:
    return model.lower().startswith(_REASONING_PREFIXES)


def _fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


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
        key_source: LLMKeySource | None = None,
        sdk_factory: Callable[[str], Any] | None = None,
        require_key_at_start: bool = True,
        image_detail: str = "high",
    ) -> None:
        self.model = model
        self.image_detail = image_detail  # 이미지 해상도 수준 (high: 얼굴·문구 같은 세부를 본다)
        self.base_url = base_url
        self.timeout_s = timeout_s
        self.temperature = temperature
        self.strict = strict
        self._fixed_sdk = sdk_client  # 테스트용 주입: 키 해석을 건너뛴다
        self._cached: tuple[str, Any] | None = None  # (키 지문, SDK 클라이언트)
        self._lock = threading.Lock()
        self.key_source: LLMKeySource | None = None
        if sdk_client is not None:
            return
        import openai  # 지연 import: openai 패키지는 llm extra (없으면 ImportError)

        self.key_source = key_source or LLMKeyStore(key_file=key_file, env=env, explicit=api_key)
        self._sdk_factory = sdk_factory or (
            lambda key: openai.OpenAI(api_key=key, base_url=base_url, timeout=timeout_s)
        )
        if require_key_at_start:
            self._sdk()

    @property
    def key_hint(self) -> str:
        if self._fixed_sdk is not None:
            return "(injected)"
        status = self.key_source.status() if self.key_source else None
        return status.hint if status and status.hint else "(미설정)"

    def _sdk(self) -> Any:
        if self._fixed_sdk is not None:
            return self._fixed_sdk
        assert self.key_source is not None
        key = self.key_source.resolve()
        if not key:
            raise LLMUnavailable(self.key_source.describe_missing())
        fingerprint = _fingerprint(key)
        with self._lock:
            if self._cached is None or self._cached[0] != fingerprint:
                self._cached = (fingerprint, self._sdk_factory(key))
            return self._cached[1]

    def _user_content(self, user: str, images: Sequence[ImageInput]) -> Any:
        """이미지가 없으면 예전처럼 문자열, 있으면 글 + 이미지 조각."""
        if not images:
            return user
        return [{"type": "text", "text": user}, *_image_parts(images, self.image_detail)]

    def complete_json(
        self,
        system: str,
        user: str,
        json_schema: Mapping[str, Any],
        *,
        schema_name: str = "response",
        timeout_s: float | None = None,
        images: Sequence[ImageInput] = (),
    ) -> JsonCompletion:
        import openai

        sdk = self._sdk()
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": self._user_content(user, images)},
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
            response = sdk.chat.completions.create(**kwargs)
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
        except openai.APIResponseValidationError as exc:
            raise LLMBadResponse("OpenAI 응답의 형식이 예상과 다릅니다") from exc
        except (
            openai.OpenAIError
        ) as exc:  # SDK 가 내는 그 밖의 오류도 이 클라이언트 밖으로는 LLMError 다
            raise LLMUnavailable(f"OpenAI 호출 실패: {type(exc).__name__}") from exc

        try:
            message = response.choices[0].message
            refusal = getattr(message, "refusal", None)
            content = message.content
        except (AttributeError, IndexError, TypeError) as exc:
            # OpenAI 호환 서버는 choices 를 비우거나 빼고 답할 때가 있다
            raise LLMBadResponse("응답에 choices·message 가 없습니다") from exc
        if refusal:
            raise LLMBadResponse(f"모델이 응답을 거부했습니다: {refusal}")
        if not content:
            raise LLMBadResponse("빈 응답을 받았습니다")
        if not isinstance(content, str):
            raise LLMBadResponse("응답 본문이 글이 아닙니다")
        usage = getattr(response, "usage", None)
        return JsonCompletion(
            text=content,
            model=getattr(response, "model", None) or self.model,
            input_tokens=getattr(usage, "prompt_tokens", None),
            output_tokens=getattr(usage, "completion_tokens", None),
        )


def _image_parts(images: Sequence[ImageInput], detail: str) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    for image in images:
        if image.label:
            parts.append({"type": "text", "text": image.label})
        encoded = base64.b64encode(image.data).decode("ascii")
        parts.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{image.media_type};base64,{encoded}", "detail": detail},
            }
        )
    return parts


def _safe_message(exc: Exception) -> str:
    return str(exc)[:300].replace("\n", " ")
