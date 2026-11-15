"""Application 계층: 유스케이스, 포트(인터페이스), 파이프라인 조정.

규칙
- 포트(ports/)는 추상 클래스만 정의한다. 구현은 adapters/ 에만 둔다.
- 유스케이스는 포트와 도메인만 import 한다. FastAPI·SQLAlchemy·torch 를 import 하면 안 된다.

밖에서 쓰는 이름 - 이 패키지는 이름을 내보내지 않는다. 하위 패키지와 모듈에서 import 한다.

모듈 지도
    ports/        바깥에 요구하는 계약 (포트 하나가 모듈 하나)
    use_cases/    유스케이스 하나가 모듈 하나
    pipeline/     생성 -> 추적 -> 검수 단계와 실행기
    checks        검수 묶음 (CheckSuite): 켜진 검수기를 차례로 돌린다
    jobs          작업 이름과 처리기 (디스패처가 넘겨받는 것)
    dto           유스케이스의 명령과 결과
    errors        어댑터·유스케이스가 올리는 오류 계층
"""

__all__: list[str] = []
