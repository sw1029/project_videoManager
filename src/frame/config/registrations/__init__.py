"""기본 등록: 설정 키가 어느 어댑터가 되는지를 포트마다 레지스트리에 올린다.

맡는 개념
    조립(설정 → 어댑터) 가운데 "키 → 어댑터를 만드는 법" 이다. FRAME_GENERATOR=external_api 같은 설정 값이
    어떤 클래스가 되고 그 클래스에 무엇을 넘기는지가 여기에 있다.
    레지스트리 자체(키 → 팩토리 표)는 frame.config.registry 가 맡고,
    만든 어댑터를 엮는 일은 frame.config.container 가 맡는다.

밖에서 쓰는 이름
    register_defaults(registry)   모든 포트의 기본 어댑터를 올린다. frame.config.registry.default_registry() 가 부른다.

모듈 지도 — 모듈 하나가 포트 하나를 맡고, 저마다 register(registry) 하나를 내놓는다.
    llm              LLMClient                    글·이미지를 읽는 모델의 클라이언트
    structurer       RequirementStructurer        요구사항 구조화
    change_request   ChangeRequestInterpreter     글로 쓴 변경 요청의 해석
    generator        VideoGenerator               장면 클립 생성
    assembler        VideoAssembler               최종 영상 조립
    identity         IdentityReferenceExtractor   정체성 기준 이미지 추출
    tracker          ObjectTracker                객체 추적
    checker          Checker                      검수
    storage          ArtifactStore                산출물 저장
    persistence      UnitOfWork                   영속성
    jobs             JobDispatcher                작업 디스패처
    _dependencies    팩토리들이 함께 기대는 것 (먼저 만들어진 객체 · ffmpeg 경로 · LLM 클라이언트)

어댑터를 더하는 절차
    1. 포트를 구현한 어댑터를 frame.adapters 의 그 포트 패키지에 둔다.
    2. 그 포트의 모듈에서 register() 에 registry.register(Port, "키", factory) 한 줄을 더한다.
    3. 설정(FRAME_GENERATOR=키 등)만 바꾸면 교체된다. 코드의 다른 곳은 바뀌지 않는다.
    새 포트는 모듈을 하나 더하고 아래 _PORT_MODULES 에 올린다.
"""

from __future__ import annotations

from ..registry import AdapterRegistry
from . import (
    assembler,
    change_request,
    checker,
    generator,
    identity,
    jobs,
    llm,
    persistence,
    storage,
    structurer,
    tracker,
)

__all__ = ["register_defaults"]

# 올리는 순서는 결과에 영향이 없다. 팩토리는 불릴 때 레지스트리에서 다른 어댑터를 찾는다
_PORT_MODULES = (
    llm,
    structurer,
    change_request,
    generator,
    assembler,
    identity,
    tracker,
    checker,
    storage,
    persistence,
    jobs,
)


def register_defaults(registry: AdapterRegistry) -> None:
    for module in _PORT_MODULES:
        module.register(registry)
