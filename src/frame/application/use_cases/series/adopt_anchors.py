"""완성된 편의 정체성 기준 이미지를 테마의 고정 출연에 붙인다 (US-06, ADR-0026).

그 뒤에 만드는 편은 그 이미지를 사용자 참조로 받아 같은 모습으로 나온다 (편 사이 정체성).
- PILOT: 그 테마로 만든 편의 버전에서만 받는다 (부른 쪽의 선언에만 기대지 않는다).
- SOURCE: FRAME 이 만든 원본 영상의 버전. 외부 영상에서 온 테마에는 쓸 수 없다 (Theme.validate 가 막는다).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace

from frame.application.ports.series.repositories import (
    EpisodeRepository,
    ThemeRecord,
    ThemeRepository,
    theme_lock_key,
)
from frame.application.ports.unit_of_work import UnitOfWorkFactory
from frame.domain.errors import NotFound, ValidationError
from frame.domain.series.theme import AnchorOrigin


@dataclass(frozen=True)
class AdoptAnchorsCommand:
    theme_id: str
    version_id: str  # 기준 이미지를 가져올 버전
    origin: AnchorOrigin = AnchorOrigin.PILOT
    # 테마의 고정 출연 키 -> 그 버전의 객체 키. 같은 키면 적지 않아도 된다
    key_map: Mapping[str, str] = field(default_factory=dict)
    overwrite: bool = False  # 이미 기준 이미지가 있는 출연도 바꾼다


@dataclass(frozen=True)
class AdoptAnchorsResult:
    record: ThemeRecord
    adopted: tuple[str, ...]  # 기준 이미지를 붙인 고정 출연


class AdoptAnchors:
    def __init__(
        self,
        themes: ThemeRepository,
        episodes: EpisodeRepository,
        uow_factory: UnitOfWorkFactory,
    ) -> None:
        self._themes = themes
        self._episodes = episodes
        self._uow_factory = uow_factory

    def execute(self, command: AdoptAnchorsCommand) -> AdoptAnchorsResult:
        # 테마 문서를 읽고 -> 고쳐 -> 덮어쓴다. 같은 문서를 고쳐 쓰는 CorrectSourceScript 와 같은 키를 쥔다:
        # 쥐지 않으면 그 사이에 고친 대본이 이 저장에 지워진다 (감사 BTS-D03·D38)
        with self._episodes.lock(theme_lock_key(command.theme_id)):
            return self._adopt(command)

    def _adopt(self, command: AdoptAnchorsCommand) -> AdoptAnchorsResult:
        record = self._themes.get(command.theme_id)
        if record is None:
            raise NotFound(
                f"테마 {command.theme_id} 을 찾을 수 없습니다", theme_id=command.theme_id
            )
        unknown = sorted(set(command.key_map) - {c.entity_key for c in record.theme.cast})
        if unknown:
            raise ValidationError(f"테마에 없는 고정 출연입니다: {unknown}")
        with self._uow_factory() as uow:
            if uow.versions.get(command.version_id) is None:
                raise NotFound(
                    f"버전 {command.version_id} 을 찾을 수 없습니다", version_id=command.version_id
                )
            references = uow.reference_images.list_by_version(command.version_id)
        if command.origin is AnchorOrigin.PILOT:
            # '처음 만든 편의 것' 이라는 규칙을 부른 쪽의 말에만 기대지 않는다: 그 테마의 편의 버전이어야 한다
            own = {
                version_id
                for episode in self._episodes.list_by_theme(command.theme_id)
                for version_id in (episode.version_id, *episode.history)
            }
            if command.version_id not in own:
                raise ValidationError(
                    "이 테마로 만든 편의 버전이 아닙니다. 처음 만든 편(PILOT)의 기준 이미지만 받습니다",
                    version_id=command.version_id,
                )
        theme = record.theme
        adopted: list[str] = []
        for member in record.theme.cast:
            if member.reference_image_uri and not command.overwrite:
                continue
            wanted = command.key_map.get(member.entity_key, member.entity_key)
            candidates = [r for r in references if r.entity_key == wanted]
            if not candidates:
                continue
            # 클립에서 뽑은 정체성 기준(외형 설명이 있다)을 사용자 참조보다 먼저 쓴다
            best = next((r for r in candidates if r.is_identity), candidates[0])
            theme = theme.with_reference(
                member.entity_key, best.image_uri, command.origin, best.description
            )
            adopted.append(member.entity_key)
        updated = replace(record, theme=theme)
        if adopted:
            self._themes.save(updated)
        return AdoptAnchorsResult(updated, tuple(adopted))
