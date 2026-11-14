"""원본에서 뽑은 대본의 줄을 고치고, 그 줄에 걸린 구조화 결과만 다시 맞춘다 (ADR-0029).

인식기가 잘못 받아 적은 줄(가격·상품 이름)이나 종류(내레이션·대사)를 사람이 고친다.
- 대본: 고친 글이 줄의 글이 되고, 인식기가 적은 글은 heard 에 남는다.
- 테마: 그 줄을 근거로 삼았던 것만 다시 맞춘다 (내레이션 규칙, 말에만 있던 변수 값, 말을 따르는 자막 규칙의 예).
  화면에서 본 값은 바꾸지 않는다. 말과 달라지면 어긋남으로 남는다.
- 근거: 화면에서 읽은 것과 고친 대본을 다시 견준다. 사라진 어긋남과 새로 생긴 어긋남을 돌려준다.

음성 인식도 테마 추출(모델)도 다시 부르지 않는다. 이 유스케이스에는 그 포트가 없다.
이미 만든 편은 바꾸지 않는다 (편은 만들 때의 계획을 갖고 있다).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from frame.application.ports.series.repositories import (
    EpisodeRepository,
    ThemeRecord,
    ThemeRepository,
    theme_lock_key,
)
from frame.domain.errors import NotFound, ValidationError
from frame.domain.script import LineKind
from frame.domain.series.script_correction import refit_after_correction
from frame.domain.series.script_evidence import Discrepancy


@dataclass(frozen=True)
class LineCorrection:
    line_id: str
    text: str | None = None  # 고친 글. None 이면 글은 그대로
    kind: LineKind | None = None  # 내레이션·대사. None 이면 종류는 그대로


@dataclass(frozen=True)
class CorrectSourceScriptCommand:
    theme_id: str
    corrections: tuple[LineCorrection, ...]


@dataclass(frozen=True)
class CorrectSourceScriptResult:
    record: ThemeRecord
    changed_lines: tuple[str, ...] = ()
    # 다시 맞춘 것: 'narration:<자리>' · 'pace' · 'variable:<키>' · 'rule:<키>'
    refit: tuple[str, ...] = ()
    resolved: tuple[Discrepancy, ...] = ()  # 사라진 어긋남
    raised: tuple[Discrepancy, ...] = ()  # 새로 생긴 어긋남


class CorrectSourceScript:
    def __init__(self, themes: ThemeRepository, episodes: EpisodeRepository) -> None:
        self._themes = themes
        self._episodes = episodes  # 테마의 키를 쥐는 데만 쓴다 (편 순번·기준 이미지 채택과 같은 키)

    def execute(self, command: CorrectSourceScriptCommand) -> CorrectSourceScriptResult:
        # 읽고 -> 고쳐 -> 덮어쓰는 사이를 지킨다. 같은 테마 문서를 고쳐 쓰는 AdoptAnchors 와 같은 키다
        with self._episodes.lock(theme_lock_key(command.theme_id)):
            return self._correct(command)

    def _correct(self, command: CorrectSourceScriptCommand) -> CorrectSourceScriptResult:
        record = self._themes.get(command.theme_id)
        if record is None:
            raise NotFound(
                f"테마 {command.theme_id} 를 찾을 수 없습니다", theme_id=command.theme_id
            )
        if record.script is None or record.script.is_empty:
            raise ValidationError("이 테마에는 고칠 대본이 없습니다 (원본에 말소리가 없었습니다)")
        ids = [c.line_id for c in command.corrections]
        if len(ids) != len(set(ids)):
            raise ValidationError(f"같은 줄을 두 번 고칠 수 없습니다: {ids}")
        script = record.script
        for correction in command.corrections:
            line = script.line(correction.line_id)  # 없는 줄이면 NotFound
            if correction.text is not None:
                line = line.with_text(correction.text)
            if correction.kind is not None:
                line = line.with_kind(correction.kind)
            script = script.with_line(line)
        result = refit_after_correction(
            record.theme, record.script, script, record.screen_texts, record.evidence
        )
        if not result.changed_lines:
            return CorrectSourceScriptResult(record)
        result.theme.validate()
        updated = replace(record, theme=result.theme, script=script, evidence=result.evidence)
        self._themes.save(updated)
        return CorrectSourceScriptResult(
            updated, result.changed_lines, result.refit, result.resolved, result.raised
        )
