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
    generator_retries: int = 0
    generator_cache: bool = False
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
            generator_retries=int(text("GENERATOR_RETRIES", str(base.generator_retries))),
            generator_cache=flag("GENERATOR_CACHE", base.generator_cache),
            llm_provider=text("LLM_PROVIDER", base.llm_provider),
            llm_model=text("LLM_MODEL", base.llm_model),
            llm_key_file=text("LLM_KEY_FILE", "") or None,
            llm_base_url=text("LLM_BASE_URL", "") or None,
            llm_timeout_s=float(text("LLM_TIMEOUT_S", str(base.llm_timeout_s))),
            llm_temperature=_optional_float(text("LLM_TEMPERATURE", "0")),
            llm_strict_schema=flag("LLM_STRICT_SCHEMA", base.llm_strict_schema),
            llm_max_attempts=int(text("LLM_MAX_ATTEMPTS", str(base.llm_max_attempts))),
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
