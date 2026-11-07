"""FastAPI 앱 팩토리. 컨테이너를 주입받으므로 테스트는 Fake 로 조립한 컨테이너를 그대로 넘긴다."""

from __future__ import annotations

from fastapi import FastAPI

from frame import __version__
from frame.config.container import Container

from .errors import register_error_handlers
from .routers import projects, versions, vocabulary


def create_app(container: Container) -> FastAPI:
    app = FastAPI(
        title="FRAME API",
        version=__version__,
        description="구조화된 중간 표현 기반 숏폼 비디오 선택적 편집·일관성 제어 프레임워크",
    )
    app.state.container = container
    prefix = container.settings.api_prefix
    app.include_router(projects.router, prefix=prefix)
    app.include_router(versions.router, prefix=prefix)
    app.include_router(vocabulary.router, prefix=prefix)
    register_error_handlers(app)

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, object]:
        s = container.settings
        return {
            "status": "ok",
            "adapters": {
                "structurer": s.structurer,
                "generator": s.generator,
                "tracker": s.tracker,
                "checkers": list(s.checkers),
                "persistence": s.persistence,
                "jobs": s.jobs,
            },
        }

    return app
