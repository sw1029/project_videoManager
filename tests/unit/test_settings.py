"""설정 기본값과 LLM 설정 오류의 조기 실패 (FRAME_STRUCTURER=llm 만 설정해도 실제 LLM 을 쓴다)."""

import sys

import pytest

from frame.adapters.generator import CachingVideoGenerator
from frame.application.errors import ConfigurationError, LLMUnavailable
from frame.config import Settings, build_container


def test_llm_provider_defaults_to_openai():
    assert Settings().llm_provider == "openai"
    assert Settings.from_env({"FRAME_STRUCTURER": "llm"}).llm_provider == "openai"


def test_frontend_integration_settings_are_parsed_from_env(tmp_path):
    settings = Settings.from_env(
        {
            "FRAME_CORS_ORIGINS": "http://localhost:5173, https://frame.example",
            "FRAME_FRONTEND_DIST": str(tmp_path),
            "FRAME_RATE_LIMIT_PER_MINUTE": "30",
        }
    )
    assert settings.cors_origins == ("http://localhost:5173", "https://frame.example")
    assert settings.frontend_dist == str(tmp_path)
    assert settings.rate_limit_per_minute == 30
    defaults = Settings.from_env({})
    assert (defaults.cors_origins, defaults.frontend_dist, defaults.rate_limit_per_minute) == (
        (),
        None,
        0,
    )


def test_llm_structurer_without_a_key_fails_at_startup(monkeypatch, tmp_path):
    pytest.importorskip("openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = Settings(structurer="llm", llm_key_file=str(tmp_path / "missing.txt"))
    with pytest.raises(LLMUnavailable):
        build_container(settings)


def test_llm_structurer_without_openai_package_fails_with_install_hint(monkeypatch):
    monkeypatch.setitem(sys.modules, "openai", None)  # import openai -> ImportError
    with pytest.raises(ConfigurationError, match="llm"):
        build_container(Settings(structurer="llm"))


def test_generator_cache_is_on_by_default():
    """#7: 같은 조건의 장면을 다시 만들지 않도록 생성 결과 캐시는 기본으로 켠다."""
    assert Settings().generator_cache is True
    assert Settings.from_env({}).generator_cache is True
    assert Settings.from_env({"FRAME_GENERATOR_CACHE": "false"}).generator_cache is False
    assert isinstance(build_container(Settings()).generator, CachingVideoGenerator)
    assert not isinstance(
        build_container(Settings(generator_cache=False)).generator, CachingVideoGenerator
    )
