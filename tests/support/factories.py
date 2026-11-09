"""테스트 공용 팩토리. 모든 테스트는 Fake 어댑터 + 메모리 저장소 + Recording 디스패처로 조립된 컨테이너를 쓴다."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from frame.adapters.common import FixedClock, SequentialIdGenerator
from frame.adapters.jobs import RecordingJobDispatcher
from frame.application.dto import CreateProjectCommand, CreateProjectResult
from frame.application.ports.structurer import ReferenceImageInput
from frame.config import Container, Settings, build_container
from frame.domain.enums import EntityKind
from frame.domain.ir import EntitySpec, IntermediateRepresentation, RequirementSpec, SceneSpec
from frame.domain.models import VideoVersion

# 규칙 기반 구조화 DSL 로 쓴 예시 요구사항 (컨셉 UI 의 '카페 신제품 숏폼')
CAFE_PROMPT = """
scene intro 0-4000 : 바리스타가 카운터에서 컵을 들어 보인다
scene closeup 4000-8000 : 컵 클로즈업
scene outro 8000-12000 : 바리스타가 손을 흔든다
entity barista PERSON : 흰 셔츠를 입은 바리스타
entity cup PROP : 세라믹 머그컵
entity cafe BACKGROUND : 따뜻한 조명의 카페
in intro: barista, cup, cafe
in closeup: cup, cafe
in outro: barista, cafe
cup.color = red
barista.shirt_color = white
barista holds -> cup @ intro
cafe.lighting = warm
"""


def cafe_ir() -> IntermediateRepresentation:
    """CAFE_PROMPT 와 같은 내용을 프로그램으로 만든 IR (도메인 단위 테스트용, 구조화기에 의존하지 않음)."""
    return IntermediateRepresentation(
        entities=(
            EntitySpec("barista", EntityKind.PERSON, "흰 셔츠를 입은 바리스타"),
            EntitySpec("cup", EntityKind.PROP, "세라믹 머그컵"),
            EntitySpec("cafe", EntityKind.BACKGROUND, "따뜻한 조명의 카페"),
        ),
        scenes=(
            SceneSpec(
                "intro",
                0,
                4000,
                "바리스타가 카운터에서 컵을 들어 보인다",
                ("barista", "cup", "cafe"),
                (
                    RequirementSpec("intro", "color", "red", "cup"),
                    RequirementSpec("intro", "shirt_color", "white", "barista"),
                    RequirementSpec("intro", "holds", "cup", "barista", "cup"),
                    RequirementSpec("intro", "lighting", "warm", "cafe"),
                ),
            ),
            SceneSpec(
                "closeup",
                4000,
                8000,
                "컵 클로즈업",
                ("cup", "cafe"),
                (
                    RequirementSpec("closeup", "color", "red", "cup"),
                    RequirementSpec("closeup", "lighting", "warm", "cafe"),
                ),
            ),
            SceneSpec(
                "outro",
                8000,
                12000,
                "바리스타가 손을 흔든다",
                ("barista", "cafe"),
                (
                    RequirementSpec("outro", "shirt_color", "white", "barista"),
                    RequirementSpec("outro", "lighting", "warm", "cafe"),
                ),
            ),
        ),
    )


def make_container(settings: Settings | None = None, **overrides: Any) -> Container:
    settings = settings or Settings(jobs="recording", persistence="memory")
    base: dict[str, Any] = {"ids": SequentialIdGenerator(), "clock": FixedClock()}
    base.update(overrides)
    return build_container(settings, overrides=base)


def dispatcher_of(container: Container) -> RecordingJobDispatcher:
    assert isinstance(container.dispatcher, RecordingJobDispatcher)
    return container.dispatcher


def create_draft(
    container: Container,
    prompt: str = CAFE_PROMPT,
    references: Iterable[ReferenceImageInput] = (),
    title: str = "카페 신제품 숏폼",
) -> CreateProjectResult:
    """프로젝트 생성 + 구조화 작업 실행까지 (정상이면 DRAFT)."""
    result = container.create_project.execute(
        CreateProjectCommand(title=title, prompt=prompt, reference_images=tuple(references))
    )
    dispatcher_of(container).run_all()
    return result


def generate(container: Container, version_id: str) -> VideoVersion:
    """생성 접수 + 파이프라인 작업 실행까지 (정상이면 COMPLETED)."""
    container.accept_generation.execute(version_id)
    dispatcher_of(container).run_all()
    return container.queries.get_version(version_id).version


def version_of(container: Container, version_id: str) -> VideoVersion:
    return container.queries.get_version(version_id).version
