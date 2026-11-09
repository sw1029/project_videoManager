"""OpenAIJsonClient: 키 해석(유출 없음), 요청 형식(strict json_schema), 오류 코드 매핑. 실제 API 는 호출하지 않는다."""

from types import SimpleNamespace

import httpx
import pytest

from frame.application.errors import LLMBadResponse, LLMError, LLMUnavailable

openai = pytest.importorskip("openai")

from frame.adapters.llm.openai_client import (  # noqa: E402 - importorskip 이후
    OpenAIJsonClient,
    mask_key,
    resolve_api_key,
)


class _FakeCompletions:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


def _sdk(response=None, error=None):
    return SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions(response, error)))


def _response(content, refusal=None, model="gpt-test"):
    message = SimpleNamespace(content=content, refusal=refusal)
    usage = SimpleNamespace(prompt_tokens=12, completion_tokens=7)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], model=model, usage=usage)


# ---- 키 해석 ---------------------------------------------------------------


def test_resolve_api_key_prefers_explicit_then_env_then_file(tmp_path):
    key_file = tmp_path / "key.txt"
    key_file.write_text("OPENAI_API_KEY=sk-from-file\n", encoding="utf-8")
    assert resolve_api_key("sk-explicit", {"OPENAI_API_KEY": "sk-env"}, key_file) == "sk-explicit"
    assert resolve_api_key(None, {"OPENAI_API_KEY": "sk-env"}, key_file) == "sk-env"
    assert resolve_api_key(None, {}, key_file) == "sk-from-file"


def test_resolve_api_key_accepts_bare_token_with_whitespace(tmp_path):
    key_file = tmp_path / "key.txt"
    key_file.write_text("  sk-bare-token \n", encoding="utf-8")
    assert resolve_api_key(None, {}, key_file) == "sk-bare-token"


def test_missing_key_raises_without_leaking_contents(tmp_path):
    with pytest.raises(LLMUnavailable) as excinfo:
        resolve_api_key(None, {}, tmp_path / "missing.txt")
    assert "sk-" not in str(excinfo.value)
    with pytest.raises(LLMUnavailable):
        resolve_api_key(None, {}, None)


def test_mask_key_hides_the_middle():
    assert mask_key("sk-abcdefghijklmnop") == "sk-...mnop"
    assert "abcdefg" not in mask_key("sk-abcdefghijklmnop")


# ---- 요청 형식 -------------------------------------------------------------


def test_request_uses_strict_json_schema_and_temperature_zero():
    sdk = _sdk(_response('{"ok": true}'))
    client = OpenAIJsonClient(model="gpt-4.1-mini", sdk_client=sdk)

    result = client.complete_json("SYS", "USR", {"type": "object"}, schema_name="frame_ir")

    kwargs = sdk.chat.completions.calls[0]
    assert kwargs["model"] == "gpt-4.1-mini"
    assert kwargs["temperature"] == 0.0
    assert [m["role"] for m in kwargs["messages"]] == ["system", "user"]
    assert kwargs["messages"][1]["content"] == "USR"
    assert kwargs["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "frame_ir", "schema": {"type": "object"}, "strict": True},
    }
    assert result.text == '{"ok": true}'
    assert (result.model, result.input_tokens, result.output_tokens) == ("gpt-test", 12, 7)


def test_reasoning_models_do_not_receive_temperature():
    sdk = _sdk(_response("{}"))
    OpenAIJsonClient(model="gpt-5-mini", sdk_client=sdk).complete_json("s", "u", {"type": "object"})
    assert "temperature" not in sdk.chat.completions.calls[0]


def test_per_call_timeout_is_forwarded():
    sdk = _sdk(_response("{}"))
    OpenAIJsonClient(model="gpt-4.1-mini", sdk_client=sdk, timeout_s=30).complete_json(
        "s", "u", {"type": "object"}, timeout_s=5
    )
    assert sdk.chat.completions.calls[0]["timeout"] == 5


@pytest.mark.parametrize(
    ("response", "needle"),
    [(_response(None, refusal="I can't help"), "거부"), (_response(""), "빈 응답")],
)
def test_refusal_and_empty_content_raise_bad_response(response, needle):
    client = OpenAIJsonClient(model="gpt-4.1-mini", sdk_client=_sdk(response))
    with pytest.raises(LLMBadResponse, match=needle):
        client.complete_json("s", "u", {"type": "object"})


def _status_error(cls, status):
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    return cls("boom", response=httpx.Response(status, request=request), body=None)


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (openai.APITimeoutError(request=httpx.Request("POST", "https://x")), "LLM_TIMEOUT"),
        (openai.APIConnectionError(request=httpx.Request("POST", "https://x")), "LLM_UNAVAILABLE"),
        (_status_error(openai.RateLimitError, 429), "LLM_RATE_LIMITED"),
        (_status_error(openai.AuthenticationError, 401), "LLM_UNAVAILABLE"),
        (_status_error(openai.NotFoundError, 404), "LLM_UNAVAILABLE"),
    ],
)
def test_sdk_errors_map_to_llm_error_codes(error, code):
    client = OpenAIJsonClient(model="gpt-4.1-mini", sdk_client=_sdk(error=error))
    with pytest.raises(LLMError) as excinfo:
        client.complete_json("s", "u", {"type": "object"})
    assert excinfo.value.code == code
