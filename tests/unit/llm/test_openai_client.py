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


# ---- 응답 모양과 그 밖의 SDK 오류 (감사 A-13) ----------------------------------------------
# 이 클라이언트의 실패는 모두 LLMError 여야 한다. 다른 예외가 새면 검수기·구조화기가 '판정 불가·구조화 실패' 로
# 다루지 못하고 유료 생성이 끝난 버전이 예상 밖 오류로 끝난다.


@pytest.mark.parametrize(
    "response",
    [
        SimpleNamespace(choices=[], model="m", usage=None),
        SimpleNamespace(choices=None, model="m", usage=None),
        SimpleNamespace(model="m", usage=None),
        SimpleNamespace(choices=[SimpleNamespace(message=None)], model="m", usage=None),
    ],
    ids=["empty-choices", "null-choices", "no-choices", "no-message"],
)
def test_an_answer_without_a_message_is_a_bad_response(response):
    """OpenAI 호환 서버는 choices 를 비우거나 빼고 답할 때가 있다."""
    client = OpenAIJsonClient(model="gpt-4.1-mini", sdk_client=_sdk(response))

    with pytest.raises(LLMBadResponse, match="choices"):
        client.complete_json("s", "u", {"type": "object"})


def test_content_that_is_not_text_is_a_bad_response():
    response = _response([{"type": "text", "text": "{}"}])
    client = OpenAIJsonClient(model="gpt-4.1-mini", sdk_client=_sdk(response))

    with pytest.raises(LLMBadResponse, match="글이 아닙니다"):
        client.complete_json("s", "u", {"type": "object"})


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (
            openai.APIResponseValidationError(
                response=httpx.Response(200, request=httpx.Request("POST", "https://x")), body=None
            ),
            "LLM_BAD_RESPONSE",
        ),
        (
            openai.APIError("boom", request=httpx.Request("POST", "https://x"), body=None),
            "LLM_UNAVAILABLE",
        ),
        (openai.OpenAIError("something the SDK adds later"), "LLM_UNAVAILABLE"),
    ],
    ids=["response-validation", "api-error", "any-sdk-error"],
)
def test_the_rest_of_the_sdk_errors_are_llm_errors_too(error, code):
    client = OpenAIJsonClient(model="gpt-4.1-mini", sdk_client=_sdk(error=error))

    with pytest.raises(LLMError) as excinfo:
        client.complete_json("s", "u", {"type": "object"})

    assert excinfo.value.code == code


# ---- 재시도 상한 (감사 A-24) --------------------------------------------------------------


def _record_sdk_clients(monkeypatch):
    made = []
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: made.append(kwargs) or SimpleNamespace())
    return made


def test_the_sdk_retries_are_limited_explicitly(monkeypatch):
    """SDK 의 기본 재시도(2회)에 기대지 않는다. 응답하지 않는 서버에서 호출 하나가 시간 제한의 세 배까지 걸린다."""
    made = _record_sdk_clients(monkeypatch)

    client = OpenAIJsonClient(
        model="gpt-4.1-mini", api_key="sk-test-" + "a" * 24, env={}, timeout_s=30
    )

    assert client.max_retries == 1
    assert (made[0]["max_retries"], made[0]["timeout"]) == (1, 30)


@pytest.mark.parametrize(("given", "used"), [(0, 0), (3, 3), (-1, 0)])
def test_the_retry_limit_can_be_set(monkeypatch, given, used):
    made = _record_sdk_clients(monkeypatch)

    OpenAIJsonClient(model="gpt-4.1-mini", api_key="sk-test-" + "a" * 24, env={}, max_retries=given)

    assert made[0]["max_retries"] == used


# ---- 호출 시점 키 해석 (화면 입력 키 지원) -------------------------------------------------


def test_key_is_resolved_at_call_time_when_startup_check_is_off():
    from frame.adapters.llm.key_store import LLMKeyStore

    store = LLMKeyStore(env={})
    created = []

    def factory(key):
        created.append(key)
        return _sdk(_response('{"ok": true}'))

    client = OpenAIJsonClient(
        model="gpt-4.1-mini", key_source=store, sdk_factory=factory, require_key_at_start=False
    )
    with pytest.raises(LLMUnavailable, match="키"):
        client.complete_json("s", "u", {"type": "object"})
    assert created == []

    first = "sk-run-" + "a" * 20 + "1111"
    store.set_runtime_key(first)
    assert client.complete_json("s", "u", {"type": "object"}).text == '{"ok": true}'
    client.complete_json("s", "u", {"type": "object"})
    assert created == [first]  # 같은 키면 SDK 클라이언트를 다시 만들지 않는다

    second = "sk-run-" + "b" * 20 + "2222"
    store.set_runtime_key(second)
    client.complete_json("s", "u", {"type": "object"})
    assert created == [first, second]


def test_startup_check_still_fails_fast_by_default():
    from frame.adapters.llm.key_store import LLMKeyStore

    with pytest.raises(LLMUnavailable):
        OpenAIJsonClient(
            model="gpt-4.1-mini", key_source=LLMKeyStore(env={}), sdk_factory=lambda key: None
        )


# ---- 이미지 입력 (VLM, ADR-0015) --------------------------------------------------------


def test_images_are_sent_as_labelled_data_uri_parts():
    from frame.application.ports.llm import ImageInput

    sdk = _sdk(_response("{}"))
    client = OpenAIJsonClient(model="gpt-4.1", sdk_client=sdk)

    client.complete_json(
        "sys",
        "두 프레임을 비교하라",
        {"type": "object"},
        images=(
            ImageInput(b"JPEG-A", "image/jpeg", "Frame A"),
            ImageInput(b"PNG-B", "image/png"),
        ),
    )

    content = sdk.chat.completions.calls[0]["messages"][1]["content"]
    assert content == [
        {"type": "text", "text": "두 프레임을 비교하라"},
        {"type": "text", "text": "Frame A"},
        {
            "type": "image_url",
            "image_url": {"url": "data:image/jpeg;base64,SlBFRy1B", "detail": "high"},
        },
        {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,UE5HLUI=", "detail": "high"},
        },
    ]


def test_text_only_requests_keep_a_plain_string_message():
    sdk = _sdk(_response("{}"))
    OpenAIJsonClient(model="gpt-4.1-mini", sdk_client=sdk).complete_json(
        "sys", "user", {"type": "object"}
    )
    assert sdk.chat.completions.calls[0]["messages"][1]["content"] == "user"
