"""어댑터 패키지들이 함께 쓰는 것.

맡는 개념
    어느 한 포트의 구현에도 속하지 않는 공용 도구다. 어댑터 패키지끼리는 서로 import 하지 않고,
    함께 쓰는 것은 여기에 둔다 (ADR-0030 의 어댑터 경계).

밖에서 쓰는 이름 — 모듈 경로로 import 한다 (frame.adapters.shared.api_keys 처럼).

모듈 지도
    api_keys    API 키 다루기: 키 파일에서 키 찾기, 화면·로그에 보일 표시
    http        외부 HTTP API 를 부르는 전송 계층 (HttpTransport, 표준 라이브러리 구현 UrllibTransport),
                키를 실어 보낼 주소의 판정 (secure_base_url)
    runtime     ID 생성기와 시계 (운영 구현과, 테스트·재현 실행용 결정적 구현)
    locking     키별 잠금 (KeyedLocks): 같은 키의 일을 한 번에 하나만
    media/      영상 처리 도구: ffmpeg 찾기·실행, 프레임 뽑기, 영상·소리 정보, 유사도(SSIM), 가짜 미디어 형식
    vlm/        이미지를 읽는 모델(VLM)의 답 읽기
"""

__all__: list[str] = []
