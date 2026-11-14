"""프레임을 읽는 LLM 으로 테마를 뽑는다 (FRAME_THEME_EXTRACTOR=vlm, ADR-0025).

1. 원본의 컷을 다시 만들 수 있는 장면 자리로 묶는다 (slot_spans_from_shots).
2. 자리마다 표본 프레임을 고르게 뽑는다 (ffmpeg). 전체 프레임 수에 상한을 두고 자리마다 나눈다.
3. 모델을 한 번 부른다 (_prompt·_schema). 응답은 _theme_builder 가 테마·원본 IR 로 다듬는다.
4. 다듬은 결과가 규칙을 통과하지 못하면 오류를 되돌려 보내 다시 묻는다 (max_attempts, 프레임이 붙는 요청이라
   글만 보내는 호출과 따로 둔다). 재질의마다 무엇이 걸렸는지 기록에 남긴다.
5. 얹은 문구의 영역은 문구마다 프레임 한 장으로 다시 묻는다 (_text_locator). 한 번에 여러 개를 물은 상자는 어긋난다.

한계: 프레임만 보므로 움직임·음악은 알 수 없다. 무엇이 다시 나올지는 영상 한 편으로는 모델의 짐작이다.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from frame.adapters.series.theme_extraction._text_locator import locate_text
from frame.adapters.video_tools import extract_frame, require_ffmpeg
from frame.application.errors import LLMError
from frame.application.ports.llm import ImageInput, JsonCompletion, LLMClient
from frame.application.ports.series.theme_extraction import (
    ThemeExtraction,
    ThemeExtractionError,
    ThemeExtractionRequest,
    ThemeExtractor,
)
from frame.domain.errors import DomainError
from frame.domain.series.theme import (
    slot_spans_from_shots,
)

from ._answer_parts import _slot_index, _squash
from ._prompt import PROMPT_VERSION, SCHEMA_NAME, build_system_prompt
from ._schema import RESPONSE_SCHEMA
from ._theme_builder import build_theme

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Frame:
    label: str
    slot_index: int
    at_ms: int
    image: ImageInput


class VlmThemeExtractor(ThemeExtractor):
    def __init__(
        self,
        ffmpeg: str | None,
        llm: LLMClient,
        *,
        frames_per_slot: int,  # 설정이 정한다 (FRAME_THEME_FRAMES)
        max_attempts: int,  # 설정이 정한다 (FRAME_THEME_ATTEMPTS)
        max_frames: int = 24,
        max_side: int = 768,
        language: str = "Korean",
        timeout_s: float | None = 240.0,
        refine_text_regions: bool = True,
        max_refined_texts: int = 8,
    ) -> None:
        self._ffmpeg = require_ffmpeg(ffmpeg)
        self._llm = llm
        self.frames_per_slot = max(1, int(frames_per_slot))
        self.max_frames = max(1, int(max_frames))
        self.max_side = max_side
        self.max_attempts = max(1, int(max_attempts))
        self.language = language  # 설명·구실·메모를 적을 언어
        self.timeout_s = timeout_s
        self.refine_text_regions = refine_text_regions  # 얹은 문구의 영역을 문구마다 다시 묻는다
        self.max_refined_texts = max(0, int(max_refined_texts))  # 다시 묻는 호출 수의 상한
        self.last_completion: JsonCompletion | None = None  # 디버깅·기록용 원본 응답

    @property
    def signature(self) -> str:
        model = getattr(self._llm, "model", "?")
        regions = "refined" if self.refine_text_regions else "plain"
        return (
            f"vlm_frames:{model}:{PROMPT_VERSION}:{self.frames_per_slot}x{self.max_frames}"
            f"@{self.max_side}:{regions}:{self.language}"
        )

    def extract(self, request: ThemeExtractionRequest) -> ThemeExtraction:
        analysis = request.analysis
        spans = request.slot_spans or slot_spans_from_shots(
            analysis.shots, min_slot_ms=request.min_slot_ms
        )
        frames = self._frames(request.video, spans)
        system = build_system_prompt()
        user = self._user_prompt(request, spans, frames)
        usage = {"input_tokens": 0, "output_tokens": 0}
        last_error = "알 수 없는 오류"
        rejections: list[str] = []  # 재질의마다 무엇이 걸렸는가 (기록에 남긴다)
        prompt = user
        for attempt in range(1, self.max_attempts + 1):
            completion = self._llm.complete_json(
                system,
                prompt,
                RESPONSE_SCHEMA,
                schema_name=SCHEMA_NAME,
                timeout_s=self.timeout_s,
                images=[f.image for f in frames],
            )
            self.last_completion = completion
            usage["input_tokens"] += completion.input_tokens or 0
            usage["output_tokens"] += completion.output_tokens or 0
            try:
                document = json.loads(completion.text)
                if not isinstance(document, dict):
                    raise ValueError("응답이 JSON 객체가 아닙니다")
                theme, source_ir, texts, notes = build_theme(request, spans, document)
            except (ValueError, KeyError, TypeError, AttributeError, DomainError) as exc:
                # AttributeError: strict 가 아닌 서버가 형이 어긋난 응답을 준 경우 (목록 자리에 글 등)
                last_error = (
                    exc.message if isinstance(exc, DomainError) else f"{type(exc).__name__}: {exc}"
                )
                rejections.append(last_error[:300])
                prompt = (
                    f"{user}\n\nYour previous answer was rejected: {last_error}\n"
                    f"Previous answer:\n{completion.text[:4000]}\nReturn the corrected JSON only."
                )
                continue
            refined: int | None = None
            if self.refine_text_regions:
                refined, refine_notes = self._refine_text_boxes(
                    document, request.video, spans, frames, usage
                )
                if refined:
                    theme, source_ir, texts, notes = build_theme(request, spans, document)
                notes = [*notes, *refine_notes]
            detail: dict[str, Any] = {
                "method": "vlm_frames",
                "model": completion.model,
                "prompt_version": PROMPT_VERSION,
                "frames": len(frames),
                "attempts": attempt,
                "rejections": rejections,
                "usage": usage,
            }
            if refined is not None:
                detail["refined_text_regions"] = refined
            return ThemeExtraction(theme, source_ir, texts, tuple(notes), detail)
        log.warning("테마 추출의 응답이 %d회 모두 거절됐습니다: %s", len(rejections), rejections)
        raise ThemeExtractionError(
            f"테마 추출이 {self.max_attempts}회 시도 후 실패했습니다: {last_error}"
        )

    # ---- 얹은 문구의 영역 다시 묻기 ----
    def _refine_text_boxes(
        self,
        document: dict[str, Any],
        video: bytes,
        spans: Sequence[tuple[int, int]],
        frames: Sequence[_Frame],
        usage: dict[str, int],
    ) -> tuple[int, list[str]]:
        """document 의 얹은 문구와 그 문구 규칙의 box 를 다시 물은 영역으로 바꾼다. (바꾼 수, 메모)"""
        notes: list[str] = []
        refined = 0
        asked = 0
        for item in document.get("screen_texts") or ():
            if not isinstance(item, dict) or str(item.get("source") or "").upper() == "IN_SCENE":
                continue
            text = " ".join(str(item.get("text") or "").split())
            index = _slot_index(item.get("slot"), len(spans))
            if not text or index is None:
                continue
            if asked >= self.max_refined_texts:
                notes.append(f"화면 문구가 많아 '{text}' 부터는 영역을 다시 묻지 않았습니다")
                break
            shown = [f.at_ms for f in frames if f.slot_index == index]
            at_ms = shown[len(shown) // 2] if shown else sum(spans[index]) // 2
            image = extract_frame(
                self._ffmpeg, video, at_ms, max_side=1280, min_side=720, image_format="png"
            )
            asked += 1
            try:
                region, completion = locate_text(
                    self._llm,
                    ImageInput(image.data, image.media_type, "frame"),
                    f'caption "{text}"',
                    timeout_s=self.timeout_s,
                )
            except LLMError as exc:
                notes.append(
                    f"화면 문구의 영역을 다시 묻지 못했습니다 ({exc.code}). 처음 받은 영역을 둡니다"
                )
                break  # 한 번 실패하면 나머지도 묻지 않는다 (같은 실패를 되풀이하지 않는다)
            usage["input_tokens"] += completion.input_tokens or 0
            usage["output_tokens"] += completion.output_tokens or 0
            if region is None:
                notes.append(f"화면 문구 '{text}' 의 영역을 다시 찾지 못해 처음 받은 영역을 둡니다")
                continue
            box = region.to_dict()
            item["box"] = box
            refined += 1
            for rule in document.get("text_rules") or ():
                if not isinstance(rule, dict) or _squash(rule.get("example")) != _squash(text):
                    continue
                slots = {_slot_index(s, len(spans)) for s in rule.get("slots") or ()} - {None}
                if not slots or index in slots:
                    rule["box"] = dict(box)
        return refined, notes

    # ---- 프레임 ----
    def _frames(self, video: bytes, spans: Sequence[tuple[int, int]]) -> list[_Frame]:
        per_slot = max(1, min(self.frames_per_slot, self.max_frames // max(1, len(spans))))
        frames: list[_Frame] = []
        for index, (start, end) in enumerate(spans):
            if len(frames) >= self.max_frames:
                break  # 자리가 상한보다 많으면 뒤의 자리는 프레임 없이 시간과 말소리만 준다
            for number in range(per_slot):
                at_ms = start + int((end - start) * (number + 0.5) / per_slot)
                label = f"S{index + 1}-F{number + 1} {at_ms / 1000:.1f}s"
                image = extract_frame(self._ffmpeg, video, at_ms, max_side=self.max_side)
                frames.append(
                    _Frame(label, index, at_ms, ImageInput(image.data, image.media_type, label))
                )
        return frames

    def _user_prompt(
        self,
        request: ThemeExtractionRequest,
        spans: Sequence[tuple[int, int]],
        frames: Sequence[_Frame],
    ) -> str:
        media = request.analysis.media
        lines = [
            f"Video: {media.width}x{media.height} ({media.aspect_label}), "
            f"{media.duration_ms / 1000:.1f} s, audio: {'yes' if media.has_audio else 'no'}.",
            "Write title, summary, description, purpose, role, notes, tone and narration_purpose in "
            f"{self.language}. Keep every requirement value, fixed attribute value and style condition "
            "value in English.",
        ]
        if request.title.strip():
            lines.append(f"Title given by the user: {request.title.strip()}")
        lines.append("Scene slots:")
        for index, (start, end) in enumerate(spans):
            labels = ", ".join(f.label for f in frames if f.slot_index == index) or "no frames"
            lines.append(f"S{index + 1} {start / 1000:.1f}-{end / 1000:.1f}s (frames: {labels})")
        cuts = ", ".join(f"{s.duration_ms / 1000:.1f}" for s in request.analysis.shots)
        lines.append(f"Original shot lengths in seconds: {cuts}")
        transcript = request.analysis.transcript
        if transcript is None or transcript.is_empty:
            lines.append("Transcript: none (no speech was recognised).")
        else:
            lines.append(f"Transcript (language: {transcript.language or 'unknown'}):")
            lines += [
                f"[{s.start_ms / 1000:.1f}-{s.end_ms / 1000:.1f}] {s.text.strip()}"
                for s in transcript.segments
                if s.text.strip()
            ]
        return "\n".join(lines)
