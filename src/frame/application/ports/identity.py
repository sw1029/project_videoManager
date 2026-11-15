"""정체성 기준 이미지 추출 포트 (US-03, ADR-0015).

장면 클립에서 객체의 기준 이미지를 뽑는다. 뽑은 이미지는 그 객체가 나오는 다른 장면을 생성할 때
참조 이미지로 넘겨져 장면 사이에서 같은 모습을 유지하게 한다.
구현: Fake(테스트), ffmpeg 프레임(화면 전체), VLM 위치 찾기 + 잘라내기.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field

from frame.domain.artifacts import GeneratedClip
from frame.domain.ir import EntitySpec, SceneSpec


@dataclass(frozen=True)
class IdentityExtractionRequest:
    version_id: str
    scene: SceneSpec  # 클립이 만들어진 장면 조건
    clip: GeneratedClip
    entities: tuple[EntitySpec, ...]  # 기준 이미지가 필요한 객체 (이 장면에 등장하는 것만)


@dataclass(frozen=True)
class IdentityImage:
    entity_key: str
    image_uri: str  # ArtifactStore 에 저장한 이미지
    description: str = ""  # 이미지에서 읽은 외형 설명 (없어도 된다)
    detail: Mapping[str, object] = field(default_factory=dict)  # 프레임 시각·박스·방법 등


class IdentityReferenceExtractor(ABC):
    """계약
    - 요청한 객체마다 기준 이미지를 0개 또는 1개 돌려준다. 클립에서 객체를 찾지 못하면 그 객체는 빼고 돌려준다
      (예외가 아니다). 요청하지 않은 객체는 돌려주지 않는다.
    - 이미지는 ArtifactStore 에 저장하고 그 URI 를 돌려준다. 같은 클립·같은 객체면 같은 URI 다
      (생성 캐시·작업 원장 키가 흔들리지 않도록).
    - 실행 자체가 불가능하면 AdapterError 하위 예외를 던진다. 파이프라인은 이를 기록만 하고 생성을 계속한다
      (기준 이미지는 품질을 높이는 보조 수단이지 생성의 전제 조건이 아니다).
    """

    @abstractmethod
    def extract(self, request: IdentityExtractionRequest) -> tuple[IdentityImage, ...]: ...
