"""영상 생성 포트 (US-01, E2).

장면(Scene) 단위로 생성하고 조립(Assemble)한다. 장면 단위이므로 수정 버전에서는
조건이 바뀐 장면만 재생성하고 나머지 장면 클립은 그대로 재사용할 수 있다 (비대상 보존).
구현: Fake, 외부 API(OpenRouter 영상 API, ADR-0011). 후보: diffusers 로컬 추론(CogVideoX/Wan 등).
각 어댑터는 IR -> 모델별 프롬프트/파라미터 변환(PromptCompiler)을 내부에 캡슐화한다.

요청에 실리는 것 (ADR-0003, 0015, 0016)
- 조건: 장면·객체 정의·사용자 참조 이미지·seed. fingerprint 는 이것만 해시하며 버전 사이 클립 재사용 판단에 쓴다.
- 정체성 기준 이미지(identity_references): 다른 장면 클립에서 뽑은 객체의 기준 이미지. 조건이 아니라
  '어떻게 만들지' 이므로 fingerprint 에 넣지 않고 generation_fingerprint(생성 캐시·작업 원장 키)에만 넣는다.
- 부모 클립(parent): 수정 버전에서 다시 만드는 장면의 부모 클립과 그때의 장면 조건. 편집을 지원하는 생성기는
  부모 클립을 고쳐서 만들고, 지원하지 않는 생성기는 무시하고 새로 만든다.
- 수정 지시(directive, ADR-0017): 이 장면을 왜 다시 만드는지. 꼭 지켜야 할 요구조건(바꾼 값 / 반영되지 않았던 값)과
  그대로 둘 요소. 생성기는 편집 지시문·프롬프트에 쓴다. 그대로 둘 요소만 생성 캐시·작업 원장 키에 들어간다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from frame.domain.artifacts import GeneratedClip, VideoArtifact
from frame.domain.enums import RevisionKind
from frame.domain.hashing import canonical_hash
from frame.domain.ir import EntitySpec, RequirementSpec, SceneSpec
from frame.domain.revision import KeepItem

from .structurer import ReferenceImageInput


@dataclass(frozen=True)
class ParentClip:
    """수정 버전에서 다시 만드는 장면의 부모 쪽 결과 (ADR-0016).

    clip.metadata 에는 그 클립을 만든 생성기의 기록(모델·작업 id 등)이 있다. scene·entities 는 부모 버전에서의
    장면 조건·객체 정의로, 지금 장면과 비교해 '무엇을 바꿔야 하는지' 를 알아낸다.
    """

    version_id: str
    clip: GeneratedClip
    scene: SceneSpec
    entities: tuple[EntitySpec, ...] = ()


@dataclass(frozen=True)
class RevisionDirective:
    """수정 버전에서 다시 만드는 장면에 함께 넘기는 지시 (ADR-0017).

    - enforce: 이 장면에서 꼭 지켜져야 하는 요구조건. CHANGE 면 바뀐 조건, REPAIR 면 반영되지 않았던 조건이다.
    - previous_value: CHANGE 에서 바뀌기 전 값 (알 때만).
    - keep: 이 장면에서 그대로 둘 요소 (그 장면에 나오는 객체의 것과 장면 수준의 것만).
    """

    kind: RevisionKind
    enforce: tuple[RequirementSpec, ...] = ()
    previous_value: str | None = None
    keep: tuple[KeepItem, ...] = ()


@dataclass(frozen=True)
class SceneGenerationRequest:
    version_id: str
    scene: SceneSpec
    entities: tuple[EntitySpec, ...]  # 이 장면에 등장하는 객체 정의
    references: tuple[ReferenceImageInput, ...] = ()
    seed: int | None = None
    # 객체의 정체성 기준 이미지 (다른 장면 클립에서 뽑은 것). 조건 fingerprint 에는 들어가지 않는다
    identity_references: tuple[ReferenceImageInput, ...] = ()
    # 부모 버전에 같은 장면의 클립이 있으면 함께 넘긴다 (조건이 바뀌어 다시 만드는 장면)
    parent: ParentClip | None = None
    # 수정 버전이 이 장면을 다시 만드는 까닭과 그대로 둘 요소. 조건 fingerprint 에는 들어가지 않는다
    directive: RevisionDirective | None = None

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

    @property
    def generation_fingerprint(self) -> str:
        """생성 결과를 좌우하는 요청 쪽 값의 해시: 조건(fingerprint) + 정체성 기준 이미지 + 그대로 둘 요소.

        둘 다 없으면 fingerprint 와 같다 (기준 이미지·수정 지시를 쓰지 않던 때의 캐시·원장 키와 호환).
        수정 지시 가운데 그대로 둘 요소만 넣는다. 꼭 지켜야 할 요구조건은 이미 조건(또는 seed)에 들어 있다.
        """
        keep = (
            sorted((str(item.aspect), item.entity_key or "") for item in self.directive.keep)
            if self.directive is not None
            else []
        )
        if not self.identity_references and not keep:
            return self.fingerprint
        payload: dict[str, object] = {"fingerprint": self.fingerprint}
        if self.identity_references:
            payload["identity_references"] = sorted(
                (r.image_uri, r.entity_key or "", r.description) for r in self.identity_references
            )
        if keep:
            payload["keep"] = keep
        return canonical_hash(payload)


class VideoGenerator(ABC):
    """계약
    - 반환 클립의 scene_key 는 요청의 scene_key 와 같고, fingerprint 는 request.fingerprint 와 같다.
    - duration_ms 는 요청 장면 길이와 같아야 한다 (조립 시 타임라인이 어긋나지 않도록).
    - 실패/시간초과는 GenerationError / GenerationTimeout 으로 보고한다.
    """

    @abstractmethod
    def generate_scene(self, request: SceneGenerationRequest) -> GeneratedClip: ...

    def result_key(self, request: SceneGenerationRequest) -> str:
        """이 요청의 결과를 가르는 요청 쪽 키. 생성 캐시·작업 원장이 profile() 과 함께 키로 쓴다.

        기본값은 request.generation_fingerprint (조건 + 정체성 기준 이미지)다. 부모 클립을 편집해서 만드는
        생성기는 결과가 부모 클립에도 달려 있으므로 그 값을 더해 돌려준다 (ADR-0016).
        데코레이터는 감싼 생성기의 값을 그대로 돌려준다.
        """
        return request.generation_fingerprint

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
    "ParentClip",
    "RevisionDirective",
    "SceneGenerationRequest",
    "VideoArtifact",
    "VideoAssembler",
    "VideoGenerator",
]
