"""명령줄 도구: 시연 준비·실행, 번들 만들기, 개발·검증용 호출.

밖에서 쓰는 이름 - 이 패키지는 이름을 내보내지 않는다.
    `python -m frame.tools.<도구>` 나 scripts/ 의 진입 스크립트로 돌린다.

모듈 지도
    demo              시연 런처: doctor · setup · run · smoke (표준 라이브러리만 쓴다 - 의존성 설치 전에도 돈다)
    bundle            OS별 시연 번들 만들기와 확인
    export_openapi    OpenAPI 명세 내보내기와 계약 비교
    structure_cli     요구사항 -> IR 을 어댑터로 직접 불러 보는 CLI
"""

__all__: list[str] = []
