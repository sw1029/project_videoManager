"""이미지를 읽는 모델에 묻기 (ask_json) 와 질의 글 다듬기: 검수기 셋과 정체성 추출기가 함께 쓴다 (감사 M-04)."""

from __future__ import annotations

import pytest

from frame.adapters.shared.vlm.ask import ask_json
from frame.adapters.shared.vlm.wording import described, predicate_words
from frame.application.errors import AdapterError, LLMBadResponse
from frame.application.ports.llm import ImageInput, JsonCompletion, LLMClient


class _Model(LLMClient):
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[dict] = []

    def complete_json(
        self, system, user, json_schema, *, schema_name="response", timeout_s=None, images=()
    ):
        self.calls.append(
            {
                "system": system,
                "user": user,
                "schema": json_schema,
                "schema_name": schema_name,
                "timeout_s": timeout_s,
                "images": list(images),
            }
        )
        return JsonCompletion(self.text, "vlm-1", input_tokens=120, output_tokens=30)


def _ask(model: _Model, read=lambda data: data["items"]):
    return ask_json(
        model,
        "system",
        "user",
        {"type": "object"},
        schema_name="audit",
        timeout_s=42.0,
        images=[ImageInput(b"jpg", "image/jpeg", "F1")],
        read=read,
        what="요구 반영 판정",
    )


def test_the_question_goes_to_the_model_as_given_and_the_answer_is_read_as_json():
    model = _Model('{"items": [{"id": "R1"}]}')

    answer = _ask(model)

    assert answer.value == [{"id": "R1"}]
    assert answer.model == "vlm-1"
    assert answer.usage == {"input_tokens": 120, "output_tokens": 30}
    call = model.calls[0]
    assert (call["system"], call["user"], call["schema"]) == ("system", "user", {"type": "object"})
    assert (call["schema_name"], call["timeout_s"]) == ("audit", 42.0)
    assert [image.label for image in call["images"]] == ["F1"]


@pytest.mark.parametrize(
    "text",
    ["not json", '{"other": []}', '{"items": null}', "[]"],
    ids=["not-json", "missing-key", "wrong-type", "not-an-object"],
)
def test_an_answer_that_cannot_be_read_is_a_bad_response_with_the_checkers_wording(text):
    with pytest.raises(LLMBadResponse, match="요구 반영 판정 응답을 해석하지 못했습니다") as caught:
        _ask(_Model(text), read=lambda data: list(data["items"]))

    # 검수기는 AdapterError 를 잡아 미판정으로 남기고, 사유에 이 코드를 적는다
    assert isinstance(caught.value, AdapterError) and caught.value.code == "LLM_BAD_RESPONSE"


def test_wording_helpers_for_question_lines():
    assert predicate_words("shirt_color") == "shirt color"
    assert described("  빨간 머그컵 ") == ": 빨간 머그컵"
    assert described("   ") == ""
