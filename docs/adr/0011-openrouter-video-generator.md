# ADR-0011 OpenRouter 영상 생성 어댑터와 재과금 방지 규칙

- 상태: 채택 (2026-11-11)

## 맥락
- 영상 생성은 Fake 만 동작했고 `external_api` 는 뼈대였다. 2026-11 조사에서 OpenAI Sora Videos API(2026-09-24)와
  Gemini API 의 Veo 3.1 프리뷰(2026-10-22)가 종료되어, 아키텍처 문서의 Runway/Veo/Kling 후보는 낡았다.
- OpenRouter 는 키 하나로 영상 모델 30여 개를 같은 API(제출 → 폴링 → 다운로드)로 부른다. 모델별 허용 길이·화면비·해상도·seed 지원·가격은
  키 없이 받는 메타데이터(`GET /api/v1/videos/models`)로 알 수 있다.
- 외부 API 는 작업을 받은(202) 뒤 과금된다. 기존 `RetryingVideoGenerator` 는 모든 `GenerationError` 를 재시도해서, 접수된 작업이 실패하거나
  시간을 넘기면 같은 장면을 다시 제출(재과금)할 수 있었다. 접수 뒤 `failed` 된 작업의 과금 여부는 문서에 없다.
- 모델마다 fps·해상도·길이(프레임 격자)·오디오 유무가 달라, 모델 출력을 그대로 쓰면 포트 계약("`duration_ms` 는 장면 길이와 같다")을 지킬 수 없다.

## 결정
- **어댑터 하나, 모델은 설정**: `FRAME_GENERATOR=external_api` 는 `OpenRouterVideoGenerator` 다. 모델은 `FRAME_VIDEO_MODEL`
  (기본 `alibaba/wan-3.0`), 해상도는 `FRAME_VIDEO_RESOLUTION`(기본 720p, 시험은 480p), 화면비는 `FRAME_VIDEO_ASPECT_RATIO`(9:16).
  모델 확정은 팀이 한다 (대안: `google/gemini-omni-1.2-flash`, `bytedance/seedance-2.0-mini`, `heygen/heygen-video-1`).
- **요청**: `duration` = 장면 길이 이상인 가장 짧은 허용 길이(정수 초). `seed` 는 모델이 지원할 때만 보내고 0 ≤ seed < 2^31 로 접는다.
  `generate_audio=false`. 참조 이미지는 `input_references` 로 보낸다 (저장소 이미지는 data URI, http(s)·data URI 는 그대로).
  `frame_images` 는 쓰지 않는다. 함께 보내면 참조가 무시되고 image-to-video 로 처리된다. 임의의 로컬 경로는 읽지 않고 `ArtifactStore` 의 이미지만 보낸다.
- **프롬프트**: 영어 틀(`Scene` / `Subjects` / `Scene conditions`)에 사용자 설명을 번역하지 않고 넣는다. 번역은 추가 호출·비용이 들고 결과가
  흔들리며 화면 문구(TEXT) 철자를 바꿀 수 있어 하지 않는다. 술어·값·객체 종류는 이미 영어 어휘다. 기본 `compile_prompt` 가 빠뜨리던
  장면 수준 조건(분위기·조명 등)도 넣고, 객체별 참조 이미지 번호를 적는다.
- **폴링·다운로드**: 20초 간격(`FRAME_VIDEO_POLL_INTERVAL_S`), 접수부터 600초(`FRAME_VIDEO_TIMEOUT_S`)를 넘기면 `GenerationTimeout`.
  상태가 `failed`·`cancelled`·`expired` 면 `GenerationError`. 폴링·다운로드(GET)의 일시 오류(408·409·425·429·5xx·연결 오류)는 과금과
  무관해 계속 시도한다. 결과는 Authorization 헤더를 붙여 `/videos/{id}/content` 에서 받는다.
- **재과금 방지**: `GenerationError` 에 `retryable`(기본 True)을 두고, `RetryingVideoGenerator` 는 `retryable=False` 를 재시도하지 않는다.
  - 어댑터 안에서 다시 보내는 것은 제출 단계의 429·502 뿐이다 (최대 3번, `Retry-After` 존중). 접수 전 거절이라 과금되지 않는다.
  - 그 밖의 4xx·5xx, 제출 응답을 못 받은 경우(접수됐을 수 있음), 접수 뒤 실패, 시간 초과, 다운로드·정규화 실패는 `retryable=False` 다.
  - 오류 메시지에 job id 를 넣어 결과를 손으로 회수할 수 있게 하고, 정규화가 실패하면 과금된 원본을 `clips/<버전>/<장면>.source.mp4` 로 남긴다.
- **정규화(ffmpeg)**: `frames = round(duration_ms × fps / 1000)` (fps 기본 30, Wan 3.0 원본과 같음). 원본이 길면 자르고 짧으면 마지막 프레임을 늘린다.
  비율을 지킨 채 목표 크기(9:16 480p → 480×854, 720p → 720×1280)를 덮도록 늘리고 가운데를 자른다. H.264 yuv420p mp4, faststart,
  오디오·메타데이터 제거. 결과를 다시 읽어 프레임 수·크기를 검증한다. 클립의 `duration_ms` 는 요청 장면 길이다.
  ffmpeg 는 `FRAME_VIDEO_FFMPEG` → PATH → `imageio-ffmpeg`(`video` extra, 0.6.0 고정) 순서로 찾는다. ffprobe 없이 ffmpeg 하나로 검사한다.
- **조립(ffmpeg, `FRAME_ASSEMBLER=ffmpeg`)**: 장면 클립을 순서대로 이어 `videos/<버전>.mp4` 로 저장한다. 모든 클립이 목표 규격
  (H.264·크기·fps)과 같으면 concat demuxer 로 스트림을 복사하고(재인코딩 없음, 재사용 장면도 바이트 그대로 들어간다),
  다르면(설정을 바꾸기 전에 만든 부모 클립 등) concat 필터로 목표 규격에 맞춰 다시 인코딩한다. 길이는 클립 길이의 합이다.
- **키**: `OPENROUTER_API_KEY` 또는 `FRAME_VIDEO_KEY_FILE`. 키 파일에서는 `sk-or-` 로 시작하는 값만 읽는다 (OpenAI 키 파일을 잘못 지정해도
  다른 회사 키를 보내지 않도록). Authorization 헤더에만 싣고 리다이렉트 요청으로는 넘기지 않는다 (urllib 은 기본적으로 다른 호스트에도 넘긴다).
  서버 메시지에 섞여 와도 지우고, 클립 메타데이터·로그에는 키와 서명 URL(`urls`)을 남기지 않는다. https 가 아닌 원격 주소는 기동 시점에 거부한다.
- **HTTP**: 표준 라이브러리 urllib 를 `HttpTransport` 뒤에 둔다 (운영 설치에 새 의존성 없음). 테스트는 가짜 전송을 주입한다.
- **기동 시점 실패**: 키나 ffmpeg 가 없으면 `ConfigurationError`. 모델 메타데이터는 첫 생성 때 한 번 받는다 (기동에 네트워크가 필요 없다).
- **클립 메타데이터**: 모델, job id, `generation_id`, `usage.cost`, 예상 비용(초 단가 × 과금 초), 요청 초·과금 초, seed, 폴링 횟수,
  생성 시간, 원본·출력 규격(크기·fps·프레임 수·오디오), 프롬프트.

## 결과
- `FRAME_GENERATOR=external_api` 로 장면 클립이 실제 mp4 로 만들어지고, 모델 교체는 설정 한 줄이다.
- 같은 장면이 실패 재시도로 두 번 과금되지 않는다. 대신 접수 뒤 일시 장애로 실패한 장면은 사람이 다시 요청해야 한다.
  같은 조건 장면의 재과금은 `FRAME_GENERATOR_CACHE=true` 로 막는 것을 권한다 (프로세스 메모리 캐시).
  - 보완 (2026-11-14, 감사 D-07): 생성 캐시는 ADR-0013 에서 산출물 저장소에 남는 캐시가 됐다 (프로세스를 다시 시작해도 남는다).
    캐시를 꺼도 제출한 작업의 원장이 재과금을 막는다 (ADR-0013).
- fps 30 에서 100 ms 단위가 아닌 장면 길이는 반 프레임(약 17 ms) 이내로 반올림된다.
- 남은 일: 부모 클립을 넘기는 포트 확장(편집형 모델·`previous_job_id`), 클립 메타데이터의 DB 저장
  (SQL 저장소는 URI·fingerprint·길이만 저장), 장면 병렬 제출, 화면 키 입력(ADR-0010)의 영상 키 확장.

## 검증 (2026-11-11)
- 네트워크 없이: 가짜 HTTP 전송·가짜 정규화기를 꽂은 단위·계약 테스트(요청 형식, 길이 선택, seed, 참조 이미지, 폴링·시간 초과,
  상태별 오류, 재과금 방지, 키 미노출), 실제 ffmpeg 정규화 테스트, 루프백 서버로 urllib 전송(리다이렉트 때 키를 넘기지 않음).
- 실호출 1회 (`tests/live/test_openrouter_live.py`): Wan 3.0, 9:16, 480p, 4초, seed 20261111, 카페 intro 장면, 참조 이미지 없음.
  - 제출부터 완료까지 82.4초(폴링 4회), 다운로드·정규화까지 86.7초. 제공자(Alibaba) 쪽 생성 시간은 63.9초.
  - 비용 `usage.cost` = $0.2125. 목록가로 계산한 예상($0.05 × 4초 = $0.20)보다 6.25% 높고, 그 원인은 문서와 과금 기록(`/generation`)에 나오지 않는다.
  - 원본: 480×854, 30fps, 120프레임, 오디오 트랙 있음 (`generate_audio=false` 를 보냈는데도).
  - 정규화 결과: 480×854, 24fps, 96프레임, 4.000초, 무음 H.264. 실측 뒤 원본에 맞춰 기본 fps 를 30 으로 바꾸었다
    (24fps 면 다섯 프레임에 하나를 버린다). 30fps 정규화는 같은 규격의 합성 원본으로 검증했다.
  - 한국어 장면 설명 + 영어 틀 프롬프트로 "흰 셔츠의 바리스타가 따뜻한 조명의 카페 카운터에서 빨간 머그컵을 들어 보이는" 영상이 나왔다 (표본 1개).
- 파이프라인 실호출 (같은 날, 실제 서버·SQLite·local_fs·thread 디스패처, `FRAME_ASSEMBLER=ffmpeg`):
  한국어 브리프 → LLM 구조화(gpt-4.1-mini, 16초, 장면 3개 × 4초) → 3장면 생성(장면당 103~145초, 순차 366초) → 최종 mp4 조립(스트림 복사, 360프레임)
  → `cup.color=teal` 수정(전파 대상 2장면) → 2장면만 재생성(197초), 컵이 없는 장면은 부모 클립을 바이트 그대로 재사용. 비용 5 × $0.2125 = $1.0625.
  - 수정한 속성(머그컵 색)은 두 장면에 반영됐고, 화면 문구 "FRAME"·흰 셔츠·따뜻한 조명도 유지됐다.
  - 재생성한 장면 안의 비대상 요소(구도·배경 소품)는 seed 가 같아도 바뀐다. 장면 단위 재생성의 한계이며, 부모 클립 편집(포트 확장)이 필요한 근거다.
  - 장면마다 따로 만들어 같은 객체(바리스타)가 장면 사이에 다른 인물로 나왔다. Fake 일관성 검수기는 이를 잡지 못한다(1.0).
    참조 이미지(`input_references`)를 객체별로 넘기거나 실제 일관성 검수기가 필요하다.
- 가짜 위쪽 서버로 확인한 실패 경로: 시간 초과 → `FAILED(GENERATION_TIMEOUT)`·재제출 없음·검수 미실행·부모 보존(이슈 #1),
  둘째 장면 실패 → 버전 FAILED, 과금된 첫 장면 클립은 저장소에만 남음(#3), 같은 프로세스 안 같은 조건은 재제출 0회지만 재시작 뒤 다시 제출(#7),
  생성 중 재시작 → 버전이 PROCESSING 으로 남고 재접수 409(#2).

## 대안
- 공급자 직접 연동(DashScope, BytePlus 등): 모델마다 키·형식이 달라 어댑터가 늘어난다. 가격은 같고 OpenRouter 는 크레딧 구매 때 수수료가 붙는다.
- 레지스트리에서 `external_api` 일 때만 재시도 끄기: 모델 목록 장애처럼 과금과 무관한 실패의 재시도까지 막아 기각.
- LLM 번역 뒤 영어 프롬프트: 비결정성·추가 비용·문구 훼손 위험으로 보류. 필요하면 구조화 단계에서 영어 설명을 함께 내는 편이 낫다.
- httpx: dev extra 에만 있어 운영 설치에 새 의존성이 생기므로 기각.
