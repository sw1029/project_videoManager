"""환경 변수(FRAME_*) 기반 설정. 어댑터 선택은 전부 '키' 하나로 이루어진다."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Settings:
    structurer: str = "rule_based"
    generator: str = "fake"
    assembler: str = "fake"
    tracker: str = "fake"
    checkers: tuple[str, ...] = ("fake_requirement", "fake_consistency", "fake_preservation")
    artifact_store: str = "memory"
    artifact_dir: str = "./artifacts"
    persistence: str = "memory"  # memory | sqlalchemy
    db_url: str = "sqlite+pysqlite:///:memory:"
    db_create_schema: bool = False  # 개발용. 운영 스키마는 Alembic 마이그레이션으로 관리
    jobs: str = "inline"  # inline | recording | thread
    # 장면 하나 생성의 범용 시간 제한(초). 0·빈 값이면 끔. external_api 는 FRAME_VIDEO_TIMEOUT_S 보다 길게 (ADR-0012)
    generator_timeout_s: float = 0.0
    generator_retries: int = 0
    generator_cache: bool = True  # 같은 조건·생성기 설정의 장면은 한 번만 생성 (ADR-0013)
    # --- 장면 사이 객체 일관성 (ADR-0015) ---
    # off | fake | frame(클립 가운데 프레임 전체) | vlm_crop(VLM 으로 객체 위치를 찾아 잘라냄, FRAME_VLM_* 사용)
    # 켜면 객체가 처음 나온 클립에서 정체성 기준 이미지를 뽑아 그 객체가 나오는 다른 장면 생성에 참조로 넘긴다.
    identity: str = "off"
    identity_kinds: tuple[str, ...] = ("PERSON", "ANIMAL", "PROP", "OTHER")  # 우선순위 순
    identity_max_references: int = 3  # 장면 요청 하나에 넘기는 기준 이미지 상한
    # 이미지를 읽는 모델(VLM): 정체성 기준 추출(vlm_crop)과 일관성 검수(vlm_consistency)가 쓴다.
    # 공급자·키·주소는 FRAME_LLM_* 를 그대로 쓰고 모델만 따로 정할 수 있다 (비우면 FRAME_LLM_MODEL).
    vlm_model: str | None = None
    # 실제 검수기 (FRAME_CHECKERS 에 vlm_consistency · ssim_preservation 을 넣으면 쓴다)
    consistency_frames: int = 3  # 일관성 검수가 장면마다 뽑는 표본 프레임 수
    consistency_threshold: float = 0.9  # 고정 속성 유지율 목표 (지표 3)
    preservation_threshold: float = 0.9  # 부모 클립 대비 SSIM 하한 (비대상 변경률 10% 이하)
    # --- 재시작 복구 (ADR-0013) ---
    # 기동 때 QUEUED·STRUCTURING 은 다시 디스패치하고 PROCESSING 은 워커 유실로 본다 (서버 프로세스 하나 전제).
    # 실행 중에는 recovery_interval_s 마다 worker_lease_s 를 넘긴 PROCESSING 을 워커 유실로 본다.
    # 리스는 버전 하나의 최대 처리 시간(장면 수 x 생성 시간 + 여유)보다 길게 둔다.
    # 워커 유실 뒤 재시도를 포함한 실행이 worker_max_attempts 번에 닿으면 FAILED(WORKER_LOST).
    worker_lease_s: float = 1800.0
    worker_max_attempts: int = 3
    recovery_interval_s: float = 60.0  # 0 이면 기동 때만 복구
    # --- 영상 생성 API (FRAME_GENERATOR=external_api 일 때, OpenRouter /api/v1/videos) ---
    # 키나 ffmpeg 가 없으면 서버 기동 시점에 실패한다. 허용 길이·화면비·해상도는 모델 메타데이터를 따른다.
    video_model: str = "alibaba/wan-3.0"  # OpenRouter 영상 모델 id
    video_resolution: str = "720p"  # 시험은 480p (Wan 3.0: 480p $0.05/s, 720p $0.10/s)
    video_aspect_ratio: str = "9:16"
    video_fps: int = 30  # 정규화한 클립 fps. Wan 3.0 원본이 30fps (조립 시 모든 장면이 같은 규격)
    video_key_file: str | None = None  # 키 파일 경로. 없으면 OPENROUTER_API_KEY 환경 변수
    video_base_url: str = "https://openrouter.ai/api/v1"
    video_timeout_s: float = 600.0  # 제출부터 완료까지 기다리는 시간 (넘으면 GENERATION_TIMEOUT)
    video_poll_interval_s: float = 20.0
    video_ffmpeg: str | None = None  # ffmpeg 실행 파일. 없으면 PATH -> imageio-ffmpeg(video extra)
    # --- LLM (FRAME_STRUCTURER=llm 일 때) ---
    # openai(기본, OpenAI 호환 로컬 서버도 base_url 로) | fake(테스트용 재생 클라이언트).
    # 키나 패키지가 없으면 서버 기동 시점에 실패한다 (요청마다 조용히 실패하지 않도록).
    llm_provider: str = "openai"
    llm_model: str = "gpt-4.1-mini"
    llm_key_file: str | None = None  # 키 파일 경로. 없으면 OPENAI_API_KEY 환경 변수
    llm_base_url: str | None = None  # 로컬 3090: http://localhost:8001/v1
    llm_timeout_s: float = 60.0
    llm_temperature: float | None = 0.0  # None 이면 보내지 않음 (추론 모델은 자동 생략)
    llm_strict_schema: bool = True
    llm_max_attempts: int = 3  # 검증 실패 시 자기수정 재시도 횟수
    # 화면에서 API 키 입력 허용. 켜면 키 없이 기동하고, 키는 메모리에만 둔다 (재시작 시 사라짐)
    llm_runtime_key: bool = False
    llm_runtime_key_allow_remote: bool = False  # 기본은 서버와 같은 기기(loopback) 요청만 허용
    api_prefix: str = "/api"
    # --- 프론트 연동·배포 ---
    cors_origins: tuple[
        str, ...
    ] = ()  # 비어 있으면 CORS 꺼짐 (개발은 Vite 프록시, 배포는 같은 출처 권장)
    frontend_dist: str | None = None  # 빌드된 화면 폴더. 지정하면 "/" 에서 서빙
    rate_limit_per_minute: int = 0  # 0 이면 꺼짐. IP 별 변경 요청(POST) 수 제한

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        src = os.environ if env is None else env
        base = cls()

        def text(name: str, default: str) -> str:
            return src.get(f"FRAME_{name}", default)

        def flag(name: str, default: bool) -> bool:
            raw = src.get(f"FRAME_{name}")
            return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}

        checkers_raw = text("CHECKERS", ",".join(base.checkers))
        return replace(
            base,
            structurer=text("STRUCTURER", base.structurer),
            generator=text("GENERATOR", base.generator),
            assembler=text("ASSEMBLER", base.assembler),
            tracker=text("TRACKER", base.tracker),
            checkers=tuple(c.strip() for c in checkers_raw.split(",") if c.strip()),
            artifact_store=text("ARTIFACT_STORE", base.artifact_store),
            artifact_dir=text("ARTIFACT_DIR", base.artifact_dir),
            persistence=text("PERSISTENCE", base.persistence),
            db_url=text("DB_URL", base.db_url),
            db_create_schema=flag("DB_CREATE_SCHEMA", base.db_create_schema),
            jobs=text("JOBS", base.jobs),
            generator_timeout_s=_optional_float(text("GENERATOR_TIMEOUT_S", "")) or 0.0,
            generator_retries=int(text("GENERATOR_RETRIES", str(base.generator_retries))),
            generator_cache=flag("GENERATOR_CACHE", base.generator_cache),
            identity=text("IDENTITY", "") or base.identity,
            identity_kinds=tuple(
                k.strip().upper() for k in text("IDENTITY_KINDS", "").split(",") if k.strip()
            )
            or base.identity_kinds,
            identity_max_references=int(
                text("IDENTITY_MAX_REFERENCES", "") or base.identity_max_references
            ),
            vlm_model=text("VLM_MODEL", "") or None,
            consistency_frames=int(text("CONSISTENCY_FRAMES", "") or base.consistency_frames),
            consistency_threshold=float(
                text("CONSISTENCY_THRESHOLD", "") or base.consistency_threshold
            ),
            preservation_threshold=float(
                text("PRESERVATION_THRESHOLD", "") or base.preservation_threshold
            ),
            worker_lease_s=float(text("WORKER_LEASE_S", "") or base.worker_lease_s),
            worker_max_attempts=int(text("WORKER_MAX_ATTEMPTS", "") or base.worker_max_attempts),
            recovery_interval_s=float(text("RECOVERY_INTERVAL_S", "") or base.recovery_interval_s),
            # .env 에 빈 값으로 남겨 둔 항목은 기본값을 쓴다
            video_model=text("VIDEO_MODEL", "") or base.video_model,
            video_resolution=text("VIDEO_RESOLUTION", "") or base.video_resolution,
            video_aspect_ratio=text("VIDEO_ASPECT_RATIO", "") or base.video_aspect_ratio,
            video_fps=int(text("VIDEO_FPS", "") or base.video_fps),
            video_key_file=text("VIDEO_KEY_FILE", "") or None,
            video_base_url=text("VIDEO_BASE_URL", "") or base.video_base_url,
            video_timeout_s=float(text("VIDEO_TIMEOUT_S", "") or base.video_timeout_s),
            video_poll_interval_s=float(
                text("VIDEO_POLL_INTERVAL_S", "") or base.video_poll_interval_s
            ),
            video_ffmpeg=text("VIDEO_FFMPEG", "") or None,
            llm_provider=text("LLM_PROVIDER", base.llm_provider),
            llm_model=text("LLM_MODEL", base.llm_model),
            llm_key_file=text("LLM_KEY_FILE", "") or None,
            llm_base_url=text("LLM_BASE_URL", "") or None,
            llm_timeout_s=float(text("LLM_TIMEOUT_S", str(base.llm_timeout_s))),
            llm_temperature=_optional_float(text("LLM_TEMPERATURE", "0")),
            llm_strict_schema=flag("LLM_STRICT_SCHEMA", base.llm_strict_schema),
            llm_max_attempts=int(text("LLM_MAX_ATTEMPTS", str(base.llm_max_attempts))),
            llm_runtime_key=flag("LLM_RUNTIME_KEY", base.llm_runtime_key),
            llm_runtime_key_allow_remote=flag(
                "LLM_RUNTIME_KEY_ALLOW_REMOTE", base.llm_runtime_key_allow_remote
            ),
            api_prefix=text("API_PREFIX", base.api_prefix),
            cors_origins=tuple(o.strip() for o in text("CORS_ORIGINS", "").split(",") if o.strip()),
            frontend_dist=text("FRONTEND_DIST", "") or None,
            rate_limit_per_minute=int(text("RATE_LIMIT_PER_MINUTE", "0")),
        )


def _optional_float(raw: str) -> float | None:
    value = raw.strip().lower()
    if value in {"", "none", "null", "off"}:
        return None
    return float(value)
