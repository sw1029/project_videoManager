"""구조화 (RequirementStructurer · IrDocumentStructurer 포트): 요구사항을 중간표현(IR)으로 옮긴다.

맡는 개념
    글로 쓴 요구사항이나 화면이 조립한 IR 문서를 검증된 IR 로 만든다. 모델의 출력은 결정적 보정을 거친 뒤
    도메인의 검증(IntermediateRepresentation.validate)을 통과해야 한다 - 입력 방식이 달라도 같은 IR 규칙으로 수렴한다.

밖에서 쓰는 이름
    LLMStructurer            LLM 으로 구조화 (FRAME_STRUCTURER=llm): 프롬프트 -> JSON -> 보정 -> 검증, 실패하면 되묻는다
    RuleBasedStructurer      규칙 기반 (FRAME_STRUCTURER=rule_based): 한 줄에 한 문장인 DSL 을 읽는다. 결정적
    PassthroughStructurer    IR 직접 입력: 화면이 조립한 IR 문서를 LLM 없이 보정 · 검증한다
    FakeStructurer           테스트용: 정해 둔 IR 을 돌려주거나 오류를 낸다

모듈 지도
    llm_structurer    LLM 구조화의 흐름 (호출 · 보정 · 검증 · 재시도)
    llm_prompt        구조화 프롬프트와 그 판(PROMPT_VERSION), 되묻는 문구
    llm_schema        구조화 출력의 JSON Schema (IR 문서와 1:1)
    repair            모델 출력의 결정적 보정 (LLM 구조화와 IR 직접 입력이 함께 쓴다)
    rule_based        규칙 기반 구조화 (DSL)
    passthrough       IR 직접 입력
    fake              테스트용 구현
"""

from .fake import FakeStructurer
from .llm_structurer import LLMStructurer
from .passthrough import PassthroughStructurer
from .rule_based import RuleBasedStructurer

__all__ = ["FakeStructurer", "LLMStructurer", "PassthroughStructurer", "RuleBasedStructurer"]
