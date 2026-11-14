"""테마·편 저장 어댑터 (포트 ports/series/repositories.py, ADR-0025·0026).

맡는 개념: 테마와 편을 문서 하나씩으로 두고, 편마다 '읽고 -> 고쳐 -> 덮어쓰는' 사이를 지키는 잠금을 준다.
밖에서 쓰는 이름: 네 저장소 (__all__).

모듈 지도
- memory      InMemoryThemeRepository · InMemoryEpisodeRepository (테스트·오프라인)
- artifact    ArtifactThemeRepository · ArtifactEpisodeRepository: 산출물 저장소에 JSON 문서로 둔다
- _key_locks  (안쪽) 키마다 하나씩 쥐는 잠금 (같은 스레드는 다시 들어갈 수 있다. 프로세스 하나 안에서만 지킨다)
"""

from frame.adapters.series.store.artifact import ArtifactEpisodeRepository, ArtifactThemeRepository
from frame.adapters.series.store.memory import InMemoryEpisodeRepository, InMemoryThemeRepository

__all__ = [
    "ArtifactEpisodeRepository",
    "ArtifactThemeRepository",
    "InMemoryEpisodeRepository",
    "InMemoryThemeRepository",
]
