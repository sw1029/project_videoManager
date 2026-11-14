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
  그대로 둘 요소. 생성기는 편집 지시문·프롬프트에 쓴다. 생성 캐시·작업 원장 키에는 그대로 둘 요소와,
  다시 맞추기(REPAIR)일 때 고치려는 요구조건이 들어간다.

만들기 전 미리보기 (ADR-0020)
- preview(request): 이 요청을 지금 만들면 새로 생성할지 부모 클립을 편집할지와 그 어림 비용·시간.
  아무것도 제출하지 않는다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

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
        """생성 결과를 좌우하는 요청 쪽 값의 해시: 조건(fingerprint) + 정체성 기준 이미지 + 수정 지시의 일부.

        수정 지시에서 넣는 것
        - 그대로 둘 요소 (keep).
        - 다시 맞추기(REPAIR)가 고치려는 요구조건 (enforce). REPAIR 는 조건을 바꾸지 않고 seed 만 바꾸므로,
          이것이 없으면 같은 부모의 같은 장면에서 다른 요구조건을 고치려는 요청이 같은 키가 된다.
          CHANGE 의 enforce 는 넣지 않는다: 바뀐 값이 이미 조건에 들어 있다.

        넣을 것이 하나도 없으면 fingerprint 와 같다 (기준 이미지·수정 지시를 쓰지 않던 때의 캐시·원장 키와 호환).
        """
        directive = self.directive
        keep = (
            sorted((str(item.aspect), item.entity_key or "") for item in directive.keep)
            if directive is not None
            else []
        )
        repair = (
            sorted((r.key, r.expected_value, r.related_entity_key or "") for r in directive.enforce)
            if directive is not None and directive.kind is RevisionKind.REPAIR
            else []
        )
        if not self.identity_references and not keep and not repair:
            return self.fingerprint
        payload: dict[str, object] = {"fingerprint": self.fingerprint}
        if self.identity_references:
            payload["identity_references"] = sorted(
                (r.image_uri, r.entity_key or "", r.description) for r in self.identity_references
            )
        if keep:
            payload["keep"] = keep
        if repair:
            payload["repair"] = repair
        return canonical_hash(payload)


class ProductionMethod(StrEnum):
    """장면 클립을 만드는 방식."""

    GENERATE = "GENERATE"  # 새로 생성
    EDIT = "EDIT"  # 부모 클립을 편집 (ADR-0016)


@dataclass(frozen=True)
class ProductionPreview:
    """이 장면을 지금 만들면 어떻게 만들어질지 (ADR-0020). 변경 계획 미리보기가 쓴다.

    어림값은 아는 것만 채운다. cost_usd 는 그 방식의 단가로 계산한 값이고,
    seconds 는 같은 방식으로 만든 기록이 있을 때의 값이다.
    """

    method: ProductionMethod
    model: str | None = None
    cost_usd: float | None = None
    seconds: float | None = None
    # 편집하도록 설정됐지만 지금은 편집할 수 없어 새로 생성하게 되는 사유 (method 가 GENERATE 일 때만)
    edit_unavailable_reason: str | None = None


class VideoGenerator(ABC):
    """계약
    - 반환 클립의 scene_key 는 요청의 scene_key 와 같고, fingerprint 는 request.fingerprint 와 같다.
    - duration_ms 는 요청 장면 길이와 같아야 한다 (조립 시 타임라인이 어긋나지 않도록).
    - 실패/시간초과는 GenerationError / GenerationTimeout 으로 보고한다.
    - 클립 메타데이터에 남기는 공통 기록 (있는 것만. 계획의 어림값과 장면 응답의 clip_production 이 읽는다):
      model, cost_usd, generation_seconds, 그리고 부모 클립 편집을 시도했다면
      edit = {applied: 편집으로 만들었는가, fallback_reason: 편집하지 못하고 새로 생성한 사유}.
    - provisional = true (ADR-0021): 요청한 방식으로 만들지 못한 임시 결과다 (예: 편집할 수 없는 사정이 일시적이어서
      새로 생성함). 이 요청의 결과 키로는 남기지 않는다는 뜻이며, 생성 캐시도 저장하지 않는다. 다음에 같은 요청이 오면
      생성기가 다시 시도한다.
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

    def preview(self, request: SceneGenerationRequest) -> ProductionPreview | None:
        """이 요청을 지금 만든다면 어떤 방식·비용이 될지 (ADR-0020). 알 수 없으면 None.

        아무것도 제출하지 않는다 (과금 없음). 변경 계획 미리보기가 다시 만들 장면마다 부른다.
        편집하는 생성기는 여기서 편집에 필요한 것(부모 클립의 주소 등)을 미리 구해 둘 수 있다:
        계획을 확인한 사용자는 곧 실행하므로, 구해 둔 것을 그때 쓴다.
        데코레이터는 감싼 생성기의 값을 그대로 돌려준다.
        """
        return None

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
