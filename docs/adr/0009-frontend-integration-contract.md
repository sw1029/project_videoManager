# ADR-0009 프론트 연동 계약

- 상태: 채택 (2026-11-09, feature-frontend-mvp 연동 검토 반영)

## 맥락
프론트 MVP(`feature-frontend-mvp`)를 실제 백엔드와 붙여 보니 필드 계약은 일치했지만 연동을 막는 백엔드 쪽 공백이 있었다.
1. 오류 본문이 두 가지였다. 도메인 오류는 `{error_code, error_message}`, FastAPI 요청 검증 오류는 `{detail: [...]}`, 라우팅 오류는 `{detail: "Not Found"}`, 처리되지 않은 예외는 평문 500.
2. 결과 영상과 클립이 `mem://`, `local://` 같은 내부 URI 로만 나가고, 브라우저가 내려받을 경로가 없었다. 문서의 `GET /api/versions/{id}` "결과 영상" 과 AC-43 전후 비교의 전제가 빠져 있었다.
3. 화면이 백엔드 구조화 방식(DSL / 자연어)을 알 수 없어 기본 예시가 바로 실패했고, `FRAME_STRUCTURER=llm` 만 설정하면 LLM 공급자가 fake 로 남아 모든 요청이 조용히 실패했다.
4. 장소·조명·분위기 같은 장면 수준 조건은 수정 API 로 바꿀 수 없었다 (ADR-0008 이 장소를 장면 조건으로 옮긴 것과 충돌).
5. 다른 출처 배포용 CORS, 같은 출처 서빙, 공개 데모 보호(요청 제한·입력 제한)가 없었다.

## 결정
- **오류 형식 통일**: 모든 오류 응답은 `{error_code, error_message}`. 요청 검증 오류(422)는 `details[{loc, message}]` 를 더한다.
  라우팅 오류는 상태별 코드(`NOT_FOUND`, `METHOD_NOT_ALLOWED` …)와 원래 헤더를 유지하고, 처리되지 않은 예외는 `INTERNAL_ERROR` 로 내부 메시지를 숨긴다.
- **영상 조회 경로**: `GET /api/versions/{id}/video`, `GET /api/versions/{id}/scenes/{scene_key}/clip`.
  `VersionOut` 에 `video_url`·`video_media_type`, `SceneOut` 에 `clip_url`·`clip_media_type` 을 더한다. 내부 URI 필드는 디버깅용으로 남긴다.
  파일 저장소는 `FileResponse` 로 HTTP Range(영상 탐색)를 지원한다. 버전에 연결된 산출물만 내려주므로 임의 경로 접근은 없다.
  생성 전·실패한 버전은 `MEDIA_NOT_AVAILABLE`(404)로 `NOT_FOUND` 와 구분한다. Fake 생성기는 영상 대신 JSON 매니페스트를 내므로 화면은 미디어 형식을 보고 대체 표시를 고른다.
  저장소 포트에 `content_type()`(기본: 확장자 추정)과 `local_path()`(기본: None)를 더한다.
- **상태 확인 정보**: `/health` 가 `version`, `structuring_input`(dsl / natural_language / any), LLM 공급자·모델을 알려준다 (`HealthOut` 스키마).
- **LLM 기본 공급자**: `llm_provider` 기본값을 `openai` 로 바꾼다. 키나 패키지가 없으면 서버 기동이 실패한다(조기 실패). fake 는 테스트용으로만 남긴다.
- **장면 수준 조건 수정**: `POST /revisions` 의 `entity_key` 를 생략하면 장면 수준 조건을 모든 장면 또는 지정 장면에 적용한다. 관계 술어는 거부한다.
- **배포 옵션(설정으로만 켜짐)**: `FRAME_CORS_ORIGINS`(CORS), `FRAME_FRONTEND_DIST`(빌드된 화면을 "/" 에서 서빙, 없으면 기동 실패),
  `FRAME_RATE_LIMIT_PER_MINUTE`(IP 별 POST 요청 수 제한, 429 + Retry-After). 입력 상한: 프롬프트 4,000자, 참조 이미지 10개, 목표 길이 5분.
- **계약 고정**: `docs/openapi.json` 을 저장소에 두고, 테스트가 앱과의 필드 구조(경로·메서드·상태 코드·필드 이름)를 비교한다.

## 결과
- 화면은 오류를 한 형식으로 처리하고, `video_url` 로 영상을 재생하며, `/health` 로 입력 예시를 고를 수 있다.
- 시연·배포는 같은 출처 서빙(권장) 또는 CORS 중 하나를 설정으로 고른다. 기본값은 모두 꺼져 있어 기존 동작은 그대로다.
- 남은 과제: 화면 쪽 자동 폴링·추적 타임라인·전후 비교(이슈 #14), 참조 이미지 업로드(#12), 비교 집계 API(#13), 인증.
