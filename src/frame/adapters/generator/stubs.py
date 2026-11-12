"""실제 생성 모델 어댑터의 뼈대 (추론 코드만 비어 있음).

핵심은 IR -> 모델 입력 변환(PromptCompiler)을 어댑터 안에 가두는 것이다.
모델이 바뀌면 이 파일의 어댑터만 바뀌고, 유스케이스·파이프라인·IR 은 그대로다.
외부 API(external_api) 생성기는 openrouter.py, ffmpeg 조립기는 ffmpeg.py 에 있다 (ADR-0011).
"""

from __future__ import annotations

from frame.application.errors import GenerationError
from frame.application.ports.generator import (
    GeneratedClip,
    SceneGenerationRequest,
    VideoGenerator,
)


def compile_prompt(request: SceneGenerationRequest) -> str:
    """IR 장면 -> 텍스트 프롬프트. 모델별로 어댑터가 재정의할 수 있는 기본 변환."""
    parts: list[str] = []
    if request.scene.description:
        parts.append(request.scene.description)
    for entity in request.entities:
        attrs = [
            f"{r.predicate} {r.expected_value}"
            for r in request.scene.requirements
            if r.entity_key == entity.entity_key and r.related_entity_key is None
        ]
        relations = [
            f"{r.predicate} {r.related_entity_key}"
            for r in request.scene.requirements
            if r.entity_key == entity.entity_key and r.related_entity_key is not None
        ]
        desc = entity.description or entity.entity_key
        detail = ", ".join(attrs + relations)
        parts.append(f"{desc} ({entity.kind.lower()}{': ' + detail if detail else ''})")
    return "; ".join(parts)


class DiffusersVideoGenerator(VideoGenerator):
    """로컬 추론 (예: diffusers 의 CogVideoX / Wan 파이프라인). ml extra 설치 필요."""

    def __init__(self, model_id: str = "unconfigured", device: str = "cuda") -> None:
        self.model_id = model_id
        self.device = device

    def generate_scene(self, request: SceneGenerationRequest) -> GeneratedClip:
        prompt = compile_prompt(request)  # noqa: F841 - 실제 구현에서 파이프라인에 전달
        raise GenerationError(
            f"DiffusersVideoGenerator(model_id={self.model_id}) 추론 코드가 아직 없습니다. "
            "pipeline(prompt, image=references, num_frames=..., generator=seed) 호출을 구현하세요."
        )
