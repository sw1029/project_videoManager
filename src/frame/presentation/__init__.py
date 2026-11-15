"""Presentation 계층 (FastAPI): 입력 검증, 유스케이스 호출, 응답 변환만 한다.

밖에서 쓰는 이름 - 이 패키지는 이름을 내보내지 않는다. 앱은 frame.presentation.app.create_app 으로 만든다.

모듈 지도
    app · main        앱 팩토리, 서버 진입점
    routers/          자원별 라우터
    schemas           요청·응답 스키마 (pydantic)
    mappers           도메인·DTO -> 응답 스키마
    errors            오류 응답 형식 ({error_code, error_message})
    dependencies      라우터가 컨테이너에서 유스케이스를 꺼내는 길
    media             저장소 주소를 HTTP 응답으로
    rate_limit        공개 시연용 요청 제한
    recovery          주기 복구 스레드
"""

__all__: list[str] = []
