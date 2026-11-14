"""LLMKeyStore: 화면 입력 키(메모리) > 설정 키 > 환경 변수 > 키 파일 순으로 찾고, 상태에는 끝 4자리만 보인다."""

import pytest

from frame.adapters.llm.key_store import LLMKeyStore
from frame.application.ports.llm import LLMKeyStatus

RUNTIME = "sk-run-" + "c" * 20 + "3333"
ENV = "sk-env-" + "b" * 20 + "2222"
FILE = "sk-file-" + "a" * 20 + "1111"


def test_runtime_key_takes_precedence_and_can_be_cleared(tmp_path):
    key_file = tmp_path / "key.txt"
    key_file.write_text(FILE, encoding="utf-8")
    store = LLMKeyStore(key_file=key_file, env={"OPENAI_API_KEY": ENV})
    assert store.resolve() == ENV
    assert store.status() == LLMKeyStatus(configured=True, source="env", hint="sk-...2222")

    store.set_runtime_key(RUNTIME)
    assert store.resolve() == RUNTIME
    assert store.status() == LLMKeyStatus(configured=True, source="runtime", hint="sk-...3333")

    store.clear_runtime_key()
    assert store.status().source == "env"


def test_key_file_formats_and_missing_file(tmp_path):
    missing = LLMKeyStore(key_file=tmp_path / "none.txt", env={})
    assert missing.resolve() is None
    assert missing.status() == LLMKeyStatus(configured=False, source=None, hint=None)
    assert "키 파일이 없습니다" in missing.describe_missing()

    key_file = tmp_path / "key.txt"
    key_file.write_text(f"OPENAI_API_KEY={FILE}\n", encoding="utf-8")
    assert LLMKeyStore(key_file=key_file, env={}).status() == LLMKeyStatus(
        True, "file", "sk-...1111"
    )


def test_configured_key_sits_between_runtime_and_environment():
    store = LLMKeyStore(explicit=FILE, env={"OPENAI_API_KEY": ENV})
    assert store.status().source == "config"
    store.set_runtime_key(RUNTIME)
    assert store.status().source == "runtime"


def test_without_any_source_the_message_points_to_all_options():
    store = LLMKeyStore(env={})
    assert store.resolve() is None
    message = store.describe_missing()
    assert "화면" in message and "OPENAI_API_KEY" in message and "FRAME_LLM_KEY_FILE" in message


def test_status_and_repr_never_contain_the_full_key():
    store = LLMKeyStore(env={})
    store.set_runtime_key(RUNTIME)
    assert RUNTIME not in repr(store.status())
    assert RUNTIME not in repr(store)


def test_blank_runtime_key_is_rejected():
    with pytest.raises(ValueError):
        LLMKeyStore(env={}).set_runtime_key("   ")


# ---- 키 파일에 다른 것이 함께 있을 때 (감사 A-25) -------------------------------------------

OTHER_SERVICE = "sk-or-v1-" + "f00d" * 12


def test_a_key_file_that_also_holds_another_services_key_gives_the_openai_key(tmp_path):
    """앞줄의 OpenRouter 키를 집으면 그 키가 OpenAI(또는 FRAME_LLM_BASE_URL)로 나간다."""
    key_file = tmp_path / "keys.env"
    key_file.write_text(
        f"OPENROUTER_API_KEY={OTHER_SERVICE}\nOPENAI_API_KEY={FILE}\n", encoding="utf-8"
    )

    assert LLMKeyStore(key_file=key_file, env={}).resolve() == FILE


def test_a_dotenv_without_an_openai_key_gives_no_key(tmp_path):
    """.env 를 키 파일로 줘도 다른 비밀을 키로 쓰지 않는다 (예전에는 첫 '이름=값' 줄의 값을 썼다)."""
    key_file = tmp_path / ".env"
    key_file.write_text(
        f"DB_PASSWORD=hunter2\nOPENROUTER_API_KEY={OTHER_SERVICE}\n", encoding="utf-8"
    )
    store = LLMKeyStore(key_file=key_file, env={})

    assert store.resolve() is None and store.status().configured is False


@pytest.mark.parametrize(
    ("key", "hint"),
    [("sk-123456", "***"), ("local-server-key-0001", "...0001"), (FILE, "sk-...1111")],
    ids=["short", "another-shape", "openai"],
)
def test_the_status_hint_never_shows_more_than_the_last_four_characters(key, hint):
    """9자 키에서 7자가 드러난 적이 있다 (앞 3자 + 끝 4자). 앞 글자는 sk- 일 때만 보인다 (감사 A-29)."""
    assert LLMKeyStore(explicit=key, env={}).status().hint == hint
