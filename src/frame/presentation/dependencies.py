"""라우터가 쓰는 의존성: 앱에 실린 컨테이너를 꺼낸다 (유스케이스와 설정은 컨테이너에 있다)."""

from __future__ import annotations

from fastapi import Request

from frame.config.container import Container


def get_container(request: Request) -> Container:
    return request.app.state.container
