"""LLM 기반 구조화 어댑터의 뼈대.

프롬프트 템플릿과 JSON -> IR 변환은 완성되어 있고, 실제 모델 호출(_complete)만 비어 있다.
사용하려는 LLM(OpenAI/Anthropic/로컬 vLLM 등)별로 _complete 를 구현한 서브클래스를 만들고
config/registry.py 에 키를 등록하면 된다. 결정성을 위해 temperature=0 을 권장한다.
"""

from __future__ import annotations

import json

from frame.application.errors import StructuringError
from frame.application.ports.structurer import RequirementStructurer, StructuringRequest
from frame.domain.errors import DomainError
from frame.domain.ir import IntermediateRepresentation

SYSTEM_PROMPT = """You convert a short-form video brief into a JSON intermediate representation.
Return ONLY JSON with this shape:
{"entities":[{"entity_key":str,"kind":"PERSON|PROP|BACKGROUND|TEXT|OTHER","description":str}],
 "scenes":[{"scene_key":str,"planned_start_ms":int,"planned_end_ms":int,"description":str,
            "entity_keys":[str],
            "requirements":[{"scene_key":str,"entity_key":str|null,"related_entity_key":str|null,
                             "predicate":str,"expected_value":str}]}]}
Rules: entity_key/scene_key are unique snake_case; every requirement entity must be listed in the
scene's entity_keys; scenes must not overlap and must cover the whole duration."""


class LLMStructurer(RequirementStructurer):
    def __init__(self, model: str = "unconfigured") -> None:
        self.model = model

    def structure(self, request: StructuringRequest) -> IntermediateRepresentation:
        user_prompt = self.build_user_prompt(request)
        raw = self._complete(SYSTEM_PROMPT, user_prompt)
        try:
            ir = IntermediateRepresentation.from_dict(json.loads(raw))
            ir.validate()
        except (ValueError, KeyError, TypeError) as exc:
            raise StructuringError(f"LLM 응답을 IR 로 해석할 수 없습니다: {exc}") from exc
        except DomainError as exc:
            raise StructuringError(f"LLM 결과가 무결성 규칙을 위반합니다: {exc.message}") from exc
        return ir

    @staticmethod
    def build_user_prompt(request: StructuringRequest) -> str:
        refs = "\n".join(
            f"- {r.image_uri} (entity: {r.entity_key or 'global style'})"
            for r in request.reference_images
        )
        duration = request.target_duration_ms or 8000
        return f"Target duration: {duration} ms\nReference images:\n{refs or '- none'}\n\nBrief:\n{request.prompt}"

    def _complete(self, system_prompt: str, user_prompt: str) -> str:
        raise StructuringError(
            f"LLMStructurer(model={self.model}) 에 모델 호출이 구현되어 있지 않습니다. "
            "_complete 를 구현한 서브클래스를 등록하세요."
        )
