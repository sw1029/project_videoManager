"""이미지를 읽는 LLM 으로 화면의 글자를 읽는다 (ADR-0027). 얹은 문구가 제대로 보이는지 확인하는 데 쓴다.

방법
1. 클립의 그 시각 프레임에서 영역마다 잘라 낸다 (ffmpeg). 글자 가장자리가 잘리지 않게 조금 넓히고,
   작은 글자는 키운다.
2. 잘라 낸 이미지를 한 번에 모델에 보여 주고, 이미지마다 보이는 글자를 그대로 받아 적게 한다.
3. 모델이 답하지 않은 영역이 있으면 읽지 못한 것이다 (AdapterError). '글자가 없다' 고 답한 영역만 빈 문자열이다.
   둘을 같은 빈 문자열로 두면 검수가 읽지 못한 것을 '다르게 읽힘' 으로 센다 (감사 BTS-27).

'그대로 받아 적기' 가 중요하다. 모델은 깨진 글자를 그럴듯하게 고쳐 읽으려 한다. 고치지 말라고 하고,
읽을 수 없으면 비우라고 한다. 그래도 한 번 읽은 것은 흔들릴 수 있으므로, 불일치는 사람이 볼 수 있게 근거와 함께 남긴다.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from frame.adapters.video_tools import Box, extract_frame, require_ffmpeg
from frame.adapters.vlm_answers import match_answers
from frame.application.errors import AdapterError
from frame.application.ports.compositor import ScreenTextReader
from frame.application.ports.llm import ImageInput, JsonCompletion, LLMClient
from frame.domain.screen import Region

SYSTEM_PROMPT = (
    "You read text that is drawn on video frames. You get several cropped images (T1, T2, ...) "
    "taken from one frame. For each image, transcribe exactly the text that is visible in it, "
    "character by character, in its original language, spelling, digits and punctuation. Do not "
    "translate, correct, complete or guess: if a character is replaced by an empty box or is cut "
    "off, do not restore it. If an image shows no text, or the text cannot be read, return an "
    "empty string for it. Ignore the picture behind the text. Return every id exactly once."
)

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["texts"],
    "properties": {
        "texts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "text"],
                "properties": {"id": {"type": "string"}, "text": {"type": "string"}},
            },
        }
    },
}


class _BadAnswer(AdapterError):
    code = "LLM_BAD_RESPONSE"


class VlmScreenTextReader(ScreenTextReader):
    def __init__(
        self,
        ffmpeg: str | None,
        llm: LLMClient,
        *,
        padding: float = 0.08,
        min_side: int = 160,
        max_side: int = 1024,
        timeout_s: float | None = 120.0,
    ) -> None:
        self._ffmpeg = require_ffmpeg(ffmpeg)
        self._llm = llm
        self.padding = padding  # 영역을 넓히는 비율 (글자 가장자리가 잘리지 않게)
        self.min_side = min_side  # 잘라 낸 이미지의 짧은 변이 이보다 작으면 키운다
        self.max_side = max_side
        self.timeout_s = timeout_s
        self.last_completion: JsonCompletion | None = None  # 근거·토큰 수 기록용

    def read(self, clip: bytes, at_ms: int, regions: Sequence[Region]) -> tuple[str, ...]:
        if not regions:
            return ()
        ids = [f"T{n}" for n in range(1, len(regions) + 1)]
        images = []
        for image_id, region in zip(ids, regions, strict=True):
            grown = region.padded(self.padding)
            crop = extract_frame(
                self._ffmpeg,
                clip,
                at_ms,
                crop=Box(grown.x, grown.y, grown.w, grown.h),
                max_side=self.max_side,
                min_side=self.min_side,
                image_format="png",
            )
            images.append(ImageInput(crop.data, crop.media_type, image_id))
        completion = self._llm.complete_json(
            SYSTEM_PROMPT,
            "Images: " + ", ".join(ids) + ". Transcribe the text in each image.",
            RESPONSE_SCHEMA,
            schema_name="screen_text",
            timeout_s=self.timeout_s,
            images=images,
        )
        self.last_completion = completion
        try:
            items = list(json.loads(completion.text)["texts"])
        except (ValueError, KeyError, TypeError) as exc:
            raise _BadAnswer(f"화면 문구 읽기 응답을 해석하지 못했습니다: {exc}") from exc
        answers = match_answers(items, ids, field="id")
        missing = [i for i in ids if i not in answers]
        if missing:
            raise _BadAnswer(f"화면 문구 읽기 응답에 빠진 영역이 있습니다: {missing}")
        return tuple(
            " ".join(str(answers[i].get("text") or "").split()) if i in answers else "" for i in ids
        )
