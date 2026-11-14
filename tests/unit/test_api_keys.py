"""API 키 다루기 (감사 A-25, A-29): 키 파일에서 키 찾기와 화면·로그에 보일 표시.

키 파일은 사람이 손으로 쓴 메모일 수도 .env 일 수도 있다. 다른 서비스의 키나 다른 비밀을 집어 가면 그 값이
엉뚱한 곳(LLM 공급자)으로 나간다. 여기의 값은 모두 가짜다.
"""

from __future__ import annotations

import re

import pytest

from frame.adapters.shared.api_keys import find_key, mask_key

OPENAI = "sk-proj-" + "a1B2" * 10
OPENROUTER = "sk-or-v1-" + "f00d" * 12
SK = re.compile(r"sk-[A-Za-z0-9_\-]+")


def _find(text: str) -> str | None:
    return find_key(text, name="OPENAI_API_KEY", token=SK)


@pytest.mark.parametrize(
    "text",
    [
        OPENAI,
        f"  {OPENAI}  \n",
        f"OPENAI_API_KEY={OPENAI}\n",
        f'export OPENAI_API_KEY="{OPENAI}"\r\n',
        f"OPENAI_API_KEY = '{OPENAI}'  # 내 키\n",
        f"# OpenAI 키\n\n{OPENAI}\n",
        f"OpenAI 키: {OPENAI}",
        f"key = {OPENAI}",
    ],
    ids=[
        "bare",
        "spaces",
        "named",
        "export-quoted",
        "named-comment",
        "label-line",
        "prose",
        "lowercase-name",
    ],
)
def test_the_key_is_found_in_the_ways_people_write_key_files(text):
    assert _find(text) == OPENAI


def test_the_named_line_wins_wherever_it_is():
    """다른 서비스의 키가 앞줄에 있어도 찾는 변수의 줄을 쓴다. 앞의 것을 집으면 그 키가 다른 공급자로 나간다."""
    text = f"OPENROUTER_API_KEY={OPENROUTER}\nOPENAI_API_KEY={OPENAI}\n"

    assert _find(text) == OPENAI


@pytest.mark.parametrize(
    "text",
    [
        f"OPENROUTER_API_KEY={OPENROUTER}\n",
        f"export ANTHROPIC_API_KEY={OPENROUTER}\n",
        "DB_PASSWORD=hunter2\nSECRET_TOKEN=abc123\n",
        f"# OPENAI_API_KEY={OPENAI}\n",
        "OPENAI_API_KEY=\n",
        "",
        "키를 여기에 적으세요",
    ],
    ids=[
        "other-service",
        "other-service-export",
        "other-secrets",
        "commented-out",
        "empty",
        "blank",
        "prose",
    ],
)
def test_values_of_other_variables_are_never_taken_for_the_key(text):
    """.env 를 키 파일로 줘도 다른 비밀이 나가지 않는다 (예전에는 첫 '이름=값' 줄의 값을 키로 썼다)."""
    assert _find(text) is None


def test_a_named_value_that_is_not_a_token_is_used_as_written():
    """OpenAI 호환 로컬 서버는 sk- 모양이 아닌 키를 쓴다."""
    assert _find("OPENAI_API_KEY=local-server-key\n") == "local-server-key"


def test_another_service_can_look_for_its_own_variable_and_token():
    text = f"OPENAI_API_KEY={OPENAI}\nOPENROUTER_API_KEY={OPENROUTER}\n"

    found = find_key(text, name="OPENROUTER_API_KEY", token=re.compile(r"sk-or-[A-Za-z0-9_\-]+"))

    assert found == OPENROUTER


# ---- 화면·로그에 보일 표시 -----------------------------------------------------------------


def test_only_the_last_four_characters_are_shown():
    assert mask_key(OPENROUTER) == "..." + OPENROUTER[-4:]
    assert mask_key(OPENAI, prefix="sk-") == "sk-..." + OPENAI[-4:]


def test_the_prefix_is_shown_only_when_the_key_really_starts_with_it():
    """접두사 자리에 키의 앞 글자를 그대로 보이면 비밀의 일부가 나간다."""
    assert mask_key("local-server-key-0001", prefix="sk-") == "...0001"


@pytest.mark.parametrize("key", ["", "abc", "sk-123456", "sk-1234567890ab"])
def test_short_keys_show_nothing(key):
    """네 글자가 키의 큰 부분이면 보이지 않는다 (9자 키에서 7자가 드러난 적이 있다)."""
    assert mask_key(key) == "***" and mask_key(key, prefix="sk-") == "***"
