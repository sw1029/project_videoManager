"""Domain 계층: Entity / Enum / 값 객체 / 상태·관계 규칙.

이 계층은 외부 세계(HTTP, DB, 파일, AI 모델)를 전혀 알지 못한다.
표준 라이브러리 외의 import 는 금지한다.

밖에서 쓰는 이름 - 이 패키지는 이름을 내보내지 않는다. 모듈에서 import 한다 (frame.domain.ir 처럼).

모듈 지도
    ir · vocabulary · identifiers          구조화 결과(중간표현)와 술어 어휘, 키 규칙
    models · state_machine · progress      프로젝트 · 버전 · 상태 · 진행
    revision · services/                   수정 의도와 그 규칙 (전파, 수정 요청 풀기), 정체성 기준, 버전 비교
    checks · metrics                       검수 결과와 성공 지표
    artifacts · tracking                   산출물(클립 · 영상)과 추적 결과
    hashing                                해시의 바탕 (canonical_hash). 해시·키의 지도는 docs/GLOSSARY.md 3절
    enums · errors                         열거형, 도메인 오류
"""

__all__: list[str] = []
