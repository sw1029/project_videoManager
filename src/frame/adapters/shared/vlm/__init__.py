"""이미지를 읽는 모델(VLM)에 묻고 답을 읽는 공용 조각.

맡는 개념
    검수기·정체성 추출기·객체 추적기·변경 요청 해석기가 모델의 구조화 응답을 읽을 때 함께 쓰는 규칙이다.
    모델을 부르는 일 자체는 LLMClient 포트(application/ports/llm.py)와 adapters/llm/ 이 맡는다.

밖에서 쓰는 이름 — 모듈 경로로 import 한다 (frame.adapters.shared.vlm.ask 처럼).

모듈 지도
    ask        ask_json (한 번 묻고 응답 본문을 JSON 으로 읽는다. 읽지 못하면 LLMBadResponse), VlmAnswer
    answers    match_answers (응답 항목을 물어본 키에 맞춘다), requested_key (응답의 키가 가리키는 물어본 키)
    boxes      BOX_SCHEMA (박스 응답 형식), valid_box (보인다고 답한 쓸 만한 박스만 Box 로) — 정체성 추출·객체 추적
    wording    질의 글의 작은 조각: predicate_words (술어를 낱말로), described (이름 뒤의 설명)
"""

__all__: list[str] = []
