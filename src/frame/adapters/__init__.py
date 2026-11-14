"""Adapters 계층: 포트의 구체 구현 (교체 가능한 요소들).

맡는 개념
    하위 패키지 하나가 포트 하나(개념 하나)의 구현을 맡는다. 각 패키지의 fake.py 는 테스트·로컬 개발용 결정적 구현이다.
    실제 모델·외부 API 어댑터는 같은 포트를 구현해 config/registrations/<포트>.py 에 등록하면 설정 키만으로 바뀐다.
    어댑터 패키지끼리는 서로 import 하지 않는다. 함께 쓰는 것은 shared/ 에 둔다 (ADR-0030, ADR-0031).

밖에서 쓰는 이름 - 이 패키지는 이름을 내보내지 않는다. 하위 패키지에서 import 한다 (frame.adapters.storage 처럼).

모듈 지도
    structurer/ · change_request/ · llm/                   구조화, 변경 요청 해석, LLM·VLM 클라이언트와 키
    generator/ · identity/ · checker/ · tracker/           장면 클립 생성(과 조립), 정체성 기준 이미지, 검수, 추적
    storage/ · persistence/ · jobs/                        산출물 저장소, DB 저장소, 작업 디스패처
    shared/                                                어댑터들이 함께 쓰는 것
    common · http_transport · video_tools · vlm_answers    옛 경로의 호환 모듈 (구현은 shared/ 에 있다)
"""

__all__: list[str] = []
