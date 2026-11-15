"""이미지를 읽는 모델에 묻고 답을 JSON 으로 읽는다 (검수기 셋과 정체성 추출기가 함께 쓴다).

모델은 응답 형식(json_schema)을 받아도 어긋난 답을 낼 수 있다. 읽지 못한 답은 LLMBadResponse 로 올린다:
검수기는 그것을 잡아 미판정으로 남기고, 사유에 오류 코드(LLM_BAD_RESPONSE)와 이 문구를 적는다.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from frame.application.errors import LLMBadResponse
from frame.application.ports.llm import ImageInput, LLMClient

__all__ = ["VlmAnswer", "ask_json"]

T = TypeVar("T")


@dataclass(frozen=True)
class VlmAnswer(Generic[T]):
    value: T  # 응답 본문에서 읽어 낸 것
    model: str  # 답한 모델
    usage: dict[str, int | None]  # 이 호출의 토큰 수 {input_tokens, output_tokens}


def ask_json(
    llm: LLMClient,
    system: str,
    user: str,
    json_schema: Mapping[str, Any],
    *,
    schema_name: str,
    timeout_s: float | None,
    images: Sequence[ImageInput],
    read: Callable[[Any], T],
    what: str,
) -> VlmAnswer[T]:
    """모델에 한 번 묻는다. read 는 JSON 으로 읽은 응답 본문에서 쓸 것을 꺼낸다.

    본문이 JSON 이 아니거나 read 가 기대한 모양이 아니면(KeyError · TypeError · ValueError)
    LLMBadResponse("<what> 응답을 해석하지 못했습니다: ...") 다. 모델을 부르다 난 오류(LLMError)는 그대로 올라간다.
    """
    completion = llm.complete_json(
        system, user, json_schema, schema_name=schema_name, timeout_s=timeout_s, images=images
    )
    try:
        value = read(json.loads(completion.text))
    except (ValueError, KeyError, TypeError) as exc:
        raise LLMBadResponse(f"{what} 응답을 해석하지 못했습니다: {exc}") from exc
    usage = {"input_tokens": completion.input_tokens, "output_tokens": completion.output_tokens}
    return VlmAnswer(value, completion.model, usage)
