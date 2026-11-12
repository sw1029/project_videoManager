"""/api/settings/llm-key: 화면에서 OpenAI API 키를 넣고 빼는 시연·로컬 실행용 API.

안전 규칙
- FRAME_LLM_RUNTIME_KEY=true 일 때만 입력·삭제할 수 있다 (기본 꺼짐). 상태 조회는 항상 가능하다.
- 기본은 서버와 같은 기기(loopback)에서 온 요청만 받는다. 원격 허용은 FRAME_LLM_RUNTIME_KEY_ALLOW_REMOTE.
- 키는 메모리에만 두고 응답·로그·DB 에 남기지 않는다. 응답에는 끝 4자리만 보인다.
"""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from frame.application.ports.llm import LLMKeySource
from frame.config.container import Container
from frame.config.settings import Settings
from frame.domain.errors import NotFound, PermissionDenied, ValidationError

from .. import mappers
from ..dependencies import get_container
from ..schemas import ErrorOut, LLMKeyIn, LLMKeyStatusOut

router = APIRouter(prefix="/settings", tags=["settings"])
ContainerDep = Annotated[Container, Depends(get_container)]

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_KEY_FORMAT = re.compile(r"^sk-[A-Za-z0-9_\-]{17,400}$")
ERRORS: dict[int | str, dict[str, object]] = {
    403: {"model": ErrorOut, "description": "키 입력이 꺼져 있거나 다른 기기에서 온 요청"},
    404: {"model": ErrorOut, "description": "LLM 구조화를 쓰지 않는 설정"},
}


def _keys(container: Container) -> LLMKeySource:
    if container.llm_keys is None:
        raise NotFound("LLM 구조화(FRAME_STRUCTURER=llm, 공급자 openai)를 쓰지 않는 설정입니다")
    return container.llm_keys


def entry_allowed(request: Request, settings: Settings) -> bool:
    """이 요청을 보낸 기기에서 키를 입력할 수 있는지 (입력이 켜져 있고, 같은 기기이거나 원격 허용)."""
    if not settings.llm_runtime_key:
        return False
    host = request.client.host if request.client else ""
    return host in LOOPBACK_HOSTS or settings.llm_runtime_key_allow_remote


def _require_entry(request: Request, container: Container) -> None:
    if not container.settings.llm_runtime_key:
        raise PermissionDenied("화면 키 입력이 꺼져 있습니다. FRAME_LLM_RUNTIME_KEY=true 로 켜세요")
    if not entry_allowed(request, container.settings):
        raise PermissionDenied("API 키는 서버가 실행 중인 기기에서만 입력할 수 있습니다")


def _status_out(keys: LLMKeySource, request: Request, container: Container) -> LLMKeyStatusOut:
    settings = container.settings
    return mappers.llm_key_out(
        keys.status(), settings, entry_allowed=entry_allowed(request, settings)
    )


@router.get(
    "/llm-key",
    response_model=LLMKeyStatusOut,
    responses={404: ERRORS[404]},
    summary="OpenAI API 키 설정 상태(출처·끝 4자리)를 조회한다",
)
def get_llm_key(request: Request, container: ContainerDep) -> LLMKeyStatusOut:
    return _status_out(_keys(container), request, container)


@router.put(
    "/llm-key",
    response_model=LLMKeyStatusOut,
    responses={**ERRORS, 422: {"model": ErrorOut}},
    summary="화면에서 입력한 OpenAI API 키를 서버 메모리에 설정한다 (재시작 시 사라짐)",
)
def set_llm_key(body: LLMKeyIn, request: Request, container: ContainerDep) -> LLMKeyStatusOut:
    keys = _keys(container)
    _require_entry(request, container)
    value = body.api_key.get_secret_value().strip()
    if not _KEY_FORMAT.fullmatch(value):
        raise ValidationError("API 키 형식이 올바르지 않습니다. sk- 로 시작하는 키를 입력하세요")
    keys.set_runtime_key(value)
    return _status_out(keys, request, container)


@router.delete(
    "/llm-key",
    response_model=LLMKeyStatusOut,
    responses=ERRORS,
    summary="화면에서 입력한 키를 지운다 (파일·환경 변수 키는 그대로)",
)
def clear_llm_key(request: Request, container: ContainerDep) -> LLMKeyStatusOut:
    keys = _keys(container)
    _require_entry(request, container)
    keys.clear_runtime_key()
    return _status_out(keys, request, container)
