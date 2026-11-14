"""글로 쓴 변경 요청의 해석 (ChangeRequestInterpreter 포트, ADR-0019).

맡는 개념
    사용자가 글로 쓴 변경 요청("컵을 청록색으로, 배경은 그대로")을 수정 의도로 읽는다. 읽은 것은 이 영상(중간표현)에
    있는 객체·장면·유지할 요소에 맞춰야 한다 - 그 경계는 두 구현이 같은 규칙(_reading)을 쓴다.

밖에서 쓰는 이름
    LLMChangeRequestInterpreter          LLM 으로 읽는다 (FRAME_CHANGE_INTERPRETER=llm)
    RuleBasedChangeRequestInterpreter    LLM 없이 도는 DSL (FRAME_CHANGE_INTERPRETER=rule_based)

모듈 지도
    llm           LLM 해석기: 질의 글, 응답 형식, 응답 읽기
    rule_based    규칙 기반 해석기: DSL 문법
    _reading      함께 쓰는 규칙: 읽어 낸 객체·장면·유지할 요소를 이 영상의 것에 맞춘다
"""

from .llm import LLMChangeRequestInterpreter
from .rule_based import RuleBasedChangeRequestInterpreter

__all__ = ["LLMChangeRequestInterpreter", "RuleBasedChangeRequestInterpreter"]
