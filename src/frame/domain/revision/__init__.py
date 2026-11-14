"""수정 의도와 그 규칙 (ADR-0017, 0019): 수정 버전이 무엇을 하려는지, 그 변경이 어느 장면에 미치는지.

맡는 개념
    사용자의 수정 요청 하나(AttributeChange)를 값 바꾸기(CHANGE)나 다시 맞추기(REPAIR)로 풀고, 그 변경을 중간표현에
    반영하고(전파), 그대로 두라고 한 요소(KeepItem)와 함께 수정 의도(RevisionIntent)로 남긴다.
    낱말은 docs/GLOSSARY.md 의 '수정' 계열을 따른다.

밖에서 쓰는 이름
    RevisionIntent · KeepItem                         수정 의도, 유지할 요소
    normalize_keep                                    유지할 요소의 검증 (이 수정이 바꾸는 것은 유지하라고 할 수 없다)
    AttributeChange                                   수정 요청 하나 (객체·술어·새 값·범위)
    target_scenes · repair_targets                    그 변경의 대상 장면, REPAIR 로 다시 만들 장면
    next_seed                                         다시 만드는 장면의 seed
    ChangePropagationPolicy · DefaultPropagationPolicy · PropagationResult
                                                      전파 정책 (변경을 중간표현에 반영한다)
    ENTITY_ASPECTS · SCENE_ASPECTS                    유지할 요소의 두 갈래 (객체의 것 · 장면의 것)

모듈 지도
    intent         수정 의도와 유지할 요소
    changes        속성 변경과 그 대상 장면
    propagation    전파 정책
    resolution     수정 요청 풀기: resolve_revision (CHANGE·REPAIR 로 푼다), revision_draft (수정 버전의 초안).
                   버전(domain/models.py)을 쓰는 모듈이라 이 입구에서 내보내지 않는다 (models 가 이 패키지를 쓴다):
                   frame.domain.revision.resolution 에서 import 한다
"""

from .changes import AttributeChange, next_seed, repair_targets, target_scenes
from .intent import ENTITY_ASPECTS, SCENE_ASPECTS, KeepItem, RevisionIntent, normalize_keep
from .propagation import ChangePropagationPolicy, DefaultPropagationPolicy, PropagationResult

__all__ = [
    "ENTITY_ASPECTS",
    "SCENE_ASPECTS",
    "AttributeChange",
    "ChangePropagationPolicy",
    "DefaultPropagationPolicy",
    "KeepItem",
    "PropagationResult",
    "RevisionIntent",
    "next_seed",
    "normalize_keep",
    "repair_targets",
    "target_scenes",
]
