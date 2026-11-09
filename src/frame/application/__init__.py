"""Application 계층: 유스케이스(Service), 포트(인터페이스), 파이프라인 조정.

규칙
- 포트(ports/)는 추상 클래스만 정의한다. 구현은 adapters/ 에만 둔다.
- 유스케이스는 포트와 도메인만 import 한다. FastAPI·SQLAlchemy·torch 를 import 하면 안 된다.
"""
