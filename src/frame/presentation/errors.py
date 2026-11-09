"""오류 응답 형식 통일: 모든 오류 본문은 {error_code, error_message[, details]} 이다.

- 도메인 오류: 코드별 상태 (404 / 409 / 422 / 400). code 는 도메인 오류의 code 그대로.
- 요청 스키마 검증 오류(FastAPI): 422 VALIDATION_ERROR + details[{loc, message}]
- 라우팅 오류(없는 경로 404, 허용되지 않은 메서드 405 등): 상태별 코드, 원래 헤더(Allow 등) 유지
- 요청 제한: 429 RATE_LIMITED + Retry-After
- 처리되지 않은 예외: 500 INTERNAL_ERROR. 내부 메시지는 로그에만 남기고 응답에는 넣지 않는다.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from frame.domain.errors import (
    DomainError,
    IntegrityViolation,
    InvalidStateTransition,
    NotFound,
    PermissionDenied,
    ValidationError,
)

log = logging.getLogger(__name__)

STATUS_BY_ERROR: tuple[tuple[type[DomainError], int], ...] = (
    (NotFound, 404),  # MEDIA_NOT_AVAILABLE 포함
    (PermissionDenied, 403),
    (InvalidStateTransition, 409),  # VERSION_NOT_GENERATABLE / VERSION_NOT_REVISABLE 포함
    (IntegrityViolation, 409),
    (ValidationError, 422),
    (DomainError, 400),
)

CODE_BY_HTTP_STATUS: dict[int, str] = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    413: "PAYLOAD_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    429: "RATE_LIMITED",
}


class RateLimitExceeded(Exception):
    def __init__(self, retry_after_s: int) -> None:
        super().__init__(f"요청이 너무 많습니다. {retry_after_s}초 뒤에 다시 시도하세요")
        self.retry_after_s = retry_after_s


def error_body(
    code: str, message: str, details: list[dict[str, str]] | None = None
) -> dict[str, Any]:
    body: dict[str, Any] = {"error_code": code, "error_message": message}
    if details:
        body["details"] = details
    return body


def _validation_details(exc: RequestValidationError) -> list[dict[str, str]]:
    return [
        {
            "loc": ".".join(str(part) for part in err.get("loc", ())),
            "message": str(err.get("msg", "")),
        }
        for err in exc.errors()
    ]


def register_error_handlers(app: FastAPI) -> None:
    for error_type, status in STATUS_BY_ERROR:

        def _domain(_: Request, exc: DomainError, status: int = status) -> JSONResponse:
            return JSONResponse(status_code=status, content=error_body(exc.code, exc.message))

        app.add_exception_handler(error_type, _domain)  # type: ignore[arg-type]

    async def _request_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = _validation_details(exc)
        summary = "; ".join(
            f"{d['loc'].removeprefix('body.')}: {d['message']}" for d in details[:3]
        )
        return JSONResponse(
            status_code=422,
            content=error_body(
                "VALIDATION_ERROR", f"요청 형식이 올바르지 않습니다. {summary}", details
            ),
        )

    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = CODE_BY_HTTP_STATUS.get(exc.status_code, f"HTTP_{exc.status_code}")
        message = exc.detail if isinstance(exc.detail, str) else code
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(code, message),
            headers=getattr(exc, "headers", None),
        )

    async def _rate_limited(_: Request, exc: RateLimitExceeded) -> JSONResponse:
        return JSONResponse(
            status_code=429,
            content=error_body("RATE_LIMITED", str(exc)),
            headers={"Retry-After": str(exc.retry_after_s)},
        )

    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        log.error("처리되지 않은 예외", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content=error_body(
                "INTERNAL_ERROR", "서버 내부 오류가 발생했습니다. 서버 로그를 확인하세요"
            ),
        )

    app.add_exception_handler(RequestValidationError, _request_validation)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, _http)  # type: ignore[arg-type]
    app.add_exception_handler(RateLimitExceeded, _rate_limited)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _unexpected)
