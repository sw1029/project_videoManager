"""FastAPI 앱 팩토리. 컨테이너를 주입받으므로 테스트는 Fake 로 조립한 컨테이너를 그대로 넘긴다.

프론트 연동 옵션 (설정으로만 켜진다)
- FRAME_CORS_ORIGINS   : 다른 출처에서 띄운 화면이 API 를 부를 때 허용할 오리진 목록
- FRAME_FRONTEND_DIST  : 빌드된 화면(frontend/dist)을 같은 출처에서 서빙 (CORS 불필요, 단일 컨테이너 배포)
- FRAME_RATE_LIMIT_PER_MINUTE : 공개 데모에서 IP 별 변경 요청 수 제한
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from frame import __version__
from frame.application.errors import ConfigurationError
from frame.config.container import Container
from frame.config.settings import Settings

from .errors import register_error_handlers
from .rate_limit import SlidingWindowRateLimiter
from .recovery import RecoveryLoop
from .routers import projects, versions, vocabulary
from .schemas import HealthAdaptersOut, HealthOut

log = logging.getLogger(__name__)

STRUCTURING_INPUT = {"rule_based": "dsl", "llm": "natural_language"}


def create_app(container: Container) -> FastAPI:
    settings = container.settings
    app = FastAPI(
        title="FRAME API",
        version=__version__,
        description="구조화된 중간 표현 기반 숏폼 비디오 선택적 편집·일관성 제어 프레임워크",
        lifespan=_recovery_lifespan(container),
    )
    app.state.container = container
    app.state.rate_limiter = (
        SlidingWindowRateLimiter(settings.rate_limit_per_minute)
        if settings.rate_limit_per_minute > 0
        else None
    )
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_methods=["GET", "POST", "PUT", "OPTIONS"],
            allow_headers=["*"],
            expose_headers=["Content-Range", "Accept-Ranges", "Retry-After"],
        )

    prefix = settings.api_prefix
    app.include_router(projects.router, prefix=prefix)
    app.include_router(versions.router, prefix=prefix)
    app.include_router(vocabulary.router, prefix=prefix)
    register_error_handlers(app)

    @app.get("/health", tags=["ops"], response_model=HealthOut)
    def health() -> HealthOut:
        return _health(settings)

    if settings.frontend_dist:  # 라우트 뒤에 붙여야 /api·/health 를 가리지 않는다
        dist = Path(settings.frontend_dist)
        if not (dist / "index.html").is_file():
            raise ConfigurationError(f"FRAME_FRONTEND_DIST 에 index.html 이 없습니다: {dist}")
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    return app


def _recovery_lifespan(
    container: Container,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """재시작 복구 (#2, ADR-0013): 요청을 받기 전에 지난 프로세스가 남긴 버전을 복구하고, 주기 검사를 띄운다.

    기동 복구를 요청 처리보다 먼저 끝내므로, 새로 접수된 버전을 '사라진 작업' 으로 오인하지 않는다.
    FRAME_JOBS=inline 이면 복구한 작업이 기동 중에 실행돼 기동이 늦어진다 (thread 권장).
    """

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        recover = container.recover_pending_versions
        try:
            recover.execute(startup=True)
        except Exception:  # noqa: BLE001 - 복구 실패가 서버 기동을 막지 않게 한다 (로그로 알린다)
            log.exception(
                "기동 복구에 실패했습니다. 남은 QUEUED·PROCESSING 버전은 다음 기동 때 다시 봅니다"
            )
        loop = RecoveryLoop(recover.execute, container.settings.recovery_interval_s)
        loop.start()
        try:
            yield
        finally:
            loop.stop()

    return lifespan


def _health(s: Settings) -> HealthOut:
    uses_llm = s.structurer == "llm"
    return HealthOut(
        status="ok",
        version=__version__,
        structuring_input=STRUCTURING_INPUT.get(s.structurer, "any"),  # type: ignore[arg-type]
        adapters=HealthAdaptersOut(
            structurer=s.structurer,
            generator=s.generator,
            tracker=s.tracker,
            checkers=list(s.checkers),
            persistence=s.persistence,
            jobs=s.jobs,
            llm_provider=s.llm_provider if uses_llm else None,
            llm_model=s.llm_model if uses_llm else None,
        ),
    )
