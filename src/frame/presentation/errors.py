"""도메인 오류 -> HTTP 상태 코드 매핑. 본문은 항상 {error_code, error_message} 이다."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from frame.domain.errors import (
    DomainError,
    IntegrityViolation,
    InvalidStateTransition,
    NotFound,
    ValidationError,
)

STATUS_BY_ERROR: tuple[tuple[type[DomainError], int], ...] = (
    (NotFound, 404),
    (InvalidStateTransition, 409),  # VERSION_NOT_GENERATABLE / VERSION_NOT_REVISABLE 포함
    (IntegrityViolation, 409),
    (ValidationError, 422),
    (DomainError, 400),
)


def register_error_handlers(app: FastAPI) -> None:
    for error_type, status in STATUS_BY_ERROR:

        def _handler(_: Request, exc: DomainError, status: int = status) -> JSONResponse:
            return JSONResponse(
                status_code=status,
                content={"error_code": exc.code, "error_message": exc.message},
            )

        app.add_exception_handler(error_type, _handler)  # type: ignore[arg-type]
