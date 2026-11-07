"""ERD 의 파생 식별자 규칙.

PROJECT / VIDEO_VERSION / CHECK_RESULT 는 생성기(IdGenerator)가 만든 불투명 ID 를 쓰고,
ENTITY / SCENE / REQUIREMENT 는 자연키(entity_key, scene_key, requirement.key)에서
결정적으로 파생한다. 저장소 구현(메모리/SQL)이 달라도 같은 ID 가 나오므로 API 와 테스트가 단순해진다.
"""


def entity_id(project_id: str, entity_key: str) -> str:
    return f"{project_id}:{entity_key}"


def scene_id(version_id: str, scene_key: str) -> str:
    return f"{version_id}:{scene_key}"


def requirement_id(version_id: str, requirement_key: str) -> str:
    return f"{version_id}:{requirement_key}"
