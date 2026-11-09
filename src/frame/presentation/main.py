"""서버 진입점.

uvicorn --factory frame.presentation.main:create_default_app --reload
"""

from __future__ import annotations

from fastapi import FastAPI

from frame.config import Settings, build_container

from .app import create_app


def create_default_app() -> FastAPI:
    return create_app(build_container(Settings.from_env()))
