"""LLM 기반 구조화 어댑터: 프롬프트 → LLMClient(JSON) → 보정 → IR 검증, 실패 시 오류를 되돌려 보내 재시도.

모델 호출은 LLMClient 포트 뒤에 있으므로 OpenAI / 로컬(vLLM) / 재생(테스트) 을 설정으로 바꾼다.
"""

from __future__ import annotations

import json

from frame.application.errors import StructuringError
from frame.application.ports.llm import JsonCompletion, LLMClient
from frame.application.ports.structurer import RequirementStructurer, StructuringRequest
from frame.domain.errors import DomainError
from frame.domain.ir import IntermediateRepresentation
from frame.domain.vocabulary import DEFAULT_VOCABULARY, PredicateVocabulary

from .llm_prompt import build_system_prompt, build_user_prompt, feedback_prompt, max_scenes_for
from .llm_schema import IR_SCHEMA_NAME, ir_json_schema
from .repair import light_repair_with_notes


class LLMStructurer(RequirementStructurer):
    def __init__(
        self,
        client: LLMClient,
        *,
        vocabulary: PredicateVocabulary = DEFAULT_VOCABULARY,
        max_attempts: int = 3,
        timeout_s: float | None = None,
    ) -> None:
        self._client = client
        self._vocabulary = vocabulary
        self._max_attempts = max(1, max_attempts)
        self._timeout_s = timeout_s
        self.attempts = 0
        self.last_completion: JsonCompletion | None = None  # 디버깅·재현용 원본 응답
        self.last_repair_notes: list[str] = []  # 마지막 시도에서 보정한 내용

    def structure(self, request: StructuringRequest) -> IntermediateRepresentation:
        if not request.prompt.strip():
            raise StructuringError("요구사항이 비어 있습니다")
        schema = ir_json_schema(self._vocabulary)
        system = build_system_prompt(self._vocabulary)
        user = build_user_prompt(request, max_scenes=max_scenes_for(request))

        self.attempts = 0
        last_error = "알 수 없는 오류"
        for attempt in range(1, self._max_attempts + 1):
            self.attempts = attempt
            completion = self._client.complete_json(
                system, user, schema, schema_name=IR_SCHEMA_NAME, timeout_s=self._timeout_s
            )
            self.last_completion = completion
            try:
                return self._parse(completion.text)
            except (ValueError, KeyError, TypeError, DomainError) as exc:
                last_error = _describe(exc)
                user = feedback_prompt(user, last_error, completion.text)
        raise StructuringError(f"LLM 구조화가 {self.attempts}회 시도 후 실패했습니다: {last_error}")

    def _parse(self, text: str) -> IntermediateRepresentation:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("응답이 JSON 객체가 아닙니다")
        repaired, notes = light_repair_with_notes(data)
        self.last_repair_notes = notes
        ir = IntermediateRepresentation.from_dict(repaired)
        ir.validate()
        return ir


def _describe(exc: Exception) -> str:
    if isinstance(exc, DomainError):
        return exc.message
    return f"{type(exc).__name__}: {exc}"
