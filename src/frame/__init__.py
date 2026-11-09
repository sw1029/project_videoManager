"""FRAME: 구조화된 중간 표현(IR) 기반 숏폼 비디오 선택적 편집·일관성 제어 백엔드/파이프라인.

패키지 구조(의존 방향은 안쪽으로만 향한다):

    presentation -> application -> domain
    adapters     -> application(ports) / domain
    config       -> 모든 계층을 조립하는 Composition Root
"""

__version__ = "0.1.0"
