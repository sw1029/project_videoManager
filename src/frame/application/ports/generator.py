"""영상 생성 포트 (US-01, E2).

장면(Scene) 단위로 생성하고 조립(Assemble)한다. 장면 단위이므로 수정 버전에서는
조건이 바뀐 장면만 재생성하고 나머지 장면 클립은 그대로 재사용할 수 있다 (비대상 보존).
구현: Fake, 외부 API(OpenRouter 영상 API, ADR-0011). 후보: diffusers 로컬 추론(CogVideoX/Wan 등).
각 어댑터는 IR -> 모델별 프롬프트/파라미터 변환(PromptCompiler)을 내부에 캡슐화한다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from frame.domain.artifacts import GeneratedClip, VideoArtifact
from frame.domain.hashing import canonical_hash
from frame.domain.ir import EntitySpec, SceneSpec

from .structurer import ReferenceImageInput


@dataclass(frozen=True)
class SceneGenerationRequest:
    version_id: str
    scene: SceneSpec
    entities: tuple[EntitySpec, ...]  # 이 장면에 등장하는 객체 정의
    references: tuple[ReferenceImageInput, ...] = ()
    seed: int | None = None

    @property
    def fingerprint(self) -> str:
        """생성 조건의 해시. version_id 를 제외하므로 버전 간 클립 재사용 판단에 쓸 수 있다."""
        return canonical_hash(
            {
                "scene": {
                    "scene_key": self.scene.scene_key,
                    "planned_start_ms": self.scene.planned_start_ms,
                    "planned_end_ms": self.scene.planned_end_ms,
                    "description": self.scene.description,
                    "entity_keys": list(self.scene.entity_keys),
                    "requirements": sorted(
                        (r.key, r.expected_value, r.related_entity_key or "")
                        for r in self.scene.requirements
                    ),
                },
                "entities": [
                    {"entity_key": e.entity_key, "kind": str(e.kind), "description": e.description}
                    for e in self.entities
                ],
                "references": sorted((r.image_uri, r.entity_key or "") for r in self.references),
                "seed": self.seed,
            }
        )


class VideoGenerator(ABC):
    """계약
    - 반환 클립의 scene_key 는 요청의 scene_key 와 같고, fingerprint 는 request.fingerprint 와 같다.
    - duration_ms 는 요청 장면 길이와 같아야 한다 (조립 시 타임라인이 어긋나지 않도록).
    - 실패/시간초과는 GenerationError / GenerationTimeout 으로 보고한다.
    """

    @abstractmethod
    def generate_scene(self, request: SceneGenerationRequest) -> GeneratedClip: ...

    def profile(self) -> Mapping[str, object]:
        """생성 결과를 바꾸는 생성기 설정 (종류·모델·해상도·화면비·fps 등, ADR-0013).

        fingerprint 는 IR 조건만 담으므로 생성 캐시·작업 원장의 키에 이 값을 함께 넣는다.
        기본값은 클래스 이름뿐이다. 모델이나 출력 규격을 설정으로 바꾸는 어댑터는 그 값을 모두 넣어야
        설정을 바꿨을 때 이전 결과가 재사용되지 않는다. 데코레이터는 감싼 생성기의 값을 그대로 돌려준다.
        """
        return {"generator": type(self).__name__}


class VideoAssembler(ABC):
    """장면 클립을 순서대로 이어 붙여 최종 영상을 만든다 (ffmpeg concat 등)."""

    @abstractmethod
    def assemble(self, version_id: str, clips: Sequence[GeneratedClip]) -> VideoArtifact: ...


__all__ = [
    "GeneratedClip",
    "SceneGenerationRequest",
    "VideoArtifact",
    "VideoAssembler",
    "VideoGenerator",
]
