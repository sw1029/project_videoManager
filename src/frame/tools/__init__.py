"""명령줄 도구: 시연 준비·실행, 번들 만들기, 개발·검증용 호출.

밖에서 쓰는 이름 - 이 패키지는 이름을 내보내지 않는다.
    `python -m frame.tools.<도구>` 나 scripts/ 의 진입 스크립트로 돌린다.

모듈 지도
    demo              시연 런처: doctor · setup · run · keys · smoke (표준 라이브러리만 쓴다 - 의존성 설치 전에도 돈다)
    demo_keys         런처의 API 키: 키 파일 · 상태 · 온라인 확인 (OpenAI · OpenRouter, ADR-0037)
    demo_key_guide    런처의 키 도우미: 대화형 메뉴와 keys 명령 (demo_keys 를 묻고 알리는 순서)
    demo_video        런처의 실제 영상 모드 (run --video): 서버 설정 · 준비 점검 · 안내 (ADR-0037)
    bundle            OS별 시연 번들 만들기와 확인
    export_openapi    OpenAPI 명세 내보내기와 계약 비교
    structure_cli     요구사항 -> IR 을 어댑터로 직접 불러 보는 CLI
"""

__all__: list[str] = []
