"""서버 진입점.

uvicorn --factory frame.presentation.main:create_default_app --reload
"""

from __future__ import annotations

from fastapi import FastAPI

from frame.config import Settings, build_container
from frame.config.theme_settings import ThemeSettings

from .app import create_app


def create_default_app() -> FastAPI:
    # 환경 변수는 여기서 한 번만 읽는다. 테마·시리즈 기능의 설정도 함께 읽어 넘긴다
    overrides = {"theme_settings": ThemeSettings.from_env()}
    return create_app(build_container(Settings.from_env(), overrides=overrides))
