"""OpenRouter 영상 모델에 보내는 프롬프트: 새로 생성(compile_video_prompt)과 부모 클립 편집(compile_edit_prompt).

IR 의 장면을 영어 틀의 글로 옮긴다. 사용자가 쓴 설명은 번역하지 않는다 (추가 호출 없이 결정적이고, 문구·철자를 지킨다).
참조 이미지가 있으면 무엇을 지켜야 하는지 한 줄을 덧붙이고(ADR-0015), 수정 지시가 있으면 꼭 지킬 요구조건과
그대로 둘 요소를 적는다 (ADR-0016, 0017). 문장 하나로 결과가 달라지므로 문구는 테스트가 고정한다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from frame.adapters.shared.vlm.wording import predicate_words
from frame.application.ports.generator import (
    RevisionDirective,
    SceneGenerationRequest,
)
from frame.domain.comparison import RequirementChangeKind, compare_requirements
from frame.domain.enums import KeepAspect, RevisionKind
from frame.domain.revision import KeepItem


def compile_video_prompt(
    request: SceneGenerationRequest,
    reference_numbers: Mapping[str | None, Sequence[int]] | None = None,
) -> str:
    """IR 장면 -> 영어 틀의 프롬프트. 사용자가 쓴 설명(한국어 등)은 번역하지 않고 그대로 넣는다.

    번역을 하지 않는 이유: 추가 호출·비용 없이 결정적이고(같은 IR -> 같은 프롬프트),
    문구(TEXT)·브랜드 철자를 보존한다.
    술어·값·객체 종류는 이미 영어 어휘라 틀만 영어로 짜도 모델이 조건을 구분해 읽는다.
    장면 수준 조건(분위기·조명 등)도 넣는다 (기본 compile_prompt 는 빠뜨린다).
    reference_numbers: 객체 키(None 은 장면 전체) -> input_references 의 1부터 센 번호.
    객체에 참조 이미지가 있으면 '참조와 똑같이 보이게, 자세·행동·배경은 이 장면대로' 라고 한 줄 덧붙인다.
    참조 이미지는 프롬프트가 무엇을 지켜야 하는지 말해 줄 때 더 잘 따른다 (ADR-0015).
    """
    numbers = reference_numbers or {}
    scene = request.scene
    looks = {
        r.entity_key: r.description.strip()
        for r in request.identity_references
        if r.entity_key and r.description.strip()
    }
    lines: list[str] = []
    if scene.description.strip():
        lines.append(f"Scene: {scene.description.strip()}")
    subjects: list[str] = []
    for entity in request.entities:
        details = [entity.description.strip()] if entity.description.strip() else []
        if entity.entity_key in looks:  # 기준 이미지에서 읽은 외형 (글로도 같은 모습을 고정한다)
            details.append(f"looks like: {looks[entity.entity_key]}")
        for r in scene.requirements:
            if r.entity_key != entity.entity_key:
                continue
            if r.related_entity_key is not None:
                details.append(f"{predicate_words(r.predicate)} {r.related_entity_key}")
            else:
                details.append(f"{predicate_words(r.predicate)}: {r.expected_value}")
        label = str(entity.kind).lower()
        refs = numbers.get(entity.entity_key) or ()
        if refs:
            label += ", " + _reference_phrase(refs)
        line = f"- {entity.entity_key} ({label})"
        subjects.append(line + (": " + "; ".join(details) if details else ""))
    if subjects:
        lines.append("Subjects:")
        lines.extend(subjects)
    conditions = [
        f"{predicate_words(r.predicate)}: {r.expected_value}"
        for r in scene.requirements
        if r.entity_key is None
    ]
    if conditions:
        lines.append("Scene conditions: " + "; ".join(conditions))
    shared = numbers.get(None) or ()
    if shared:
        lines.append(f"Overall look: follow {_reference_phrase(shared)}")
    if any(numbers.get(entity.entity_key) for entity in request.entities):
        lines.append(CONSISTENCY_LINE)
    if request.directive is not None and request.directive.enforce:
        lines.append(_must_line(request.directive))
    return "\n".join(lines)


def _must_line(directive: RevisionDirective) -> str:
    """수정 버전이 이 장면에서 꼭 맞춰야 하는 요구조건 한 줄 (ADR-0017).

    객체 설명이나 장면 설명에 수정 전 표현이 남아 조건과 어긋날 수 있다 (실호출: '빨간 머그컵' + color: teal).
    무엇을 고치려는 수정인지 맨 끝에 따로 적는다.
    """
    parts = []
    for r in directive.enforce:
        subject = r.entity_key or "scene"
        if r.related_entity_key is not None:
            parts.append(f"{subject} {predicate_words(r.predicate)} {r.related_entity_key}")
        else:
            parts.append(f"{subject} {predicate_words(r.predicate)}: {r.expected_value}")
    if directive.kind is RevisionKind.REPAIR:
        note = " (an earlier render got this wrong)"
    elif directive.previous_value:
        note = f" (changed from {directive.previous_value})"
    else:
        note = ""
    return f"Must show: {'; '.join(parts)}{note}."


# 참조 이미지가 있는 객체: 모습은 참조에서, 자세·행동·구도·배경은 이 장면의 글에서 가져오게 한다
CONSISTENCY_LINE = (
    "Consistency: each subject marked with an Image number is the same individual or object as in "
    "that reference image. Keep its face, hair, body, clothing, colors, shape and markings "
    "identical to the reference; take pose, action, framing and background from this scene, not "
    "from the reference."
)


# 편집 지시문의 마지막 줄: 바꾸라고 한 것 말고는 입력 영상 그대로 둔다
EDIT_KEEP_LINE = (
    "Keep everything else exactly as in the input video: the same people (face, hair, body, "
    "clothing), the same objects, background, composition, camera movement, lighting and timing."
)


# 그대로 둘 요소를 지시문에 적을 때 쓰는 말 (ADR-0017)
_KEEP_WORDS = {
    KeepAspect.APPEARANCE: "appearance",
    KeepAspect.ACTION: "action and pose",
    KeepAspect.SHAPE: "shape and size",
    KeepAspect.BACKGROUND: "the background",
    KeepAspect.LIGHTING: "the lighting",
    KeepAspect.COMPOSITION: "the framing and camera",
}


def _keep_phrase(keep: Sequence[KeepItem]) -> str:
    """'barista (action and pose, appearance); cup (shape and size); the lighting' 꼴. 객체별로 묶는다."""
    by_entity: dict[str, list[str]] = {}
    scene_level: list[str] = []
    for item in keep:
        if item.entity_key is None:
            scene_level.append(_KEEP_WORDS[item.aspect])
        else:
            by_entity.setdefault(item.entity_key, []).append(_KEEP_WORDS[item.aspect])
    parts = [f"{key} ({', '.join(words)})" for key, words in by_entity.items()]
    return "; ".join([*parts, *scene_level])


def compile_edit_prompt(request: SceneGenerationRequest) -> str | None:
    """부모 클립을 고칠 때의 지시문: 부모 쪽 장면 조건과 지금 조건의 차이만 적는다 (ADR-0016).

    부모 클립이 없거나 글로 옮길 차이가 없으면 None (편집할 것이 없다 -> 새로 생성).
    수정 지시(ADR-0017)가 있으면 반영한다: REPAIR 는 조건의 차이가 없으므로 지켜지지 않았던 요구조건을
    '... must be ...' 로 적고, 그대로 둘 요소가 있으면 'Do not change: ...' 한 줄을 더한다.
    """
    parent = request.parent
    if parent is None:
        return None
    scene, before = request.scene, parent.scene
    changes: list[str] = []
    for row in compare_requirements(before, scene):
        subject = row.entity_key or "the scene"
        name = predicate_words(row.predicate)
        old, new = row.before, row.after
        if row.change is RequirementChangeKind.CHANGED and old is not None and new is not None:
            if row.is_relational:
                changes.append(
                    f"- {subject}: now {name} {new.related_entity_key} "
                    f"(was {name} {old.related_entity_key})"
                )
            else:
                changes.append(
                    f"- {subject}: {name} changes from {old.expected_value} to {new.expected_value}"
                )
        elif row.change is RequirementChangeKind.ADDED and new is not None:
            if row.is_relational:
                changes.append(f"- {subject}: now {name} {new.related_entity_key}")
            else:
                changes.append(f"- {subject}: {name} is now {new.expected_value}")
        elif row.change is RequirementChangeKind.REMOVED and old is not None:
            if row.is_relational:
                changes.append(f"- {subject}: no longer {name} {old.related_entity_key}")
            else:
                changes.append(f"- {subject}: {name} ({old.expected_value}) is no longer required")
    described = {e.entity_key: e.description for e in parent.entities}
    for entity in request.entities:
        text = entity.description.strip()
        if text and entity.entity_key in described and described[entity.entity_key] != text:
            changes.append(f"- {entity.entity_key} is now: {text}")
    if scene.description.strip() and scene.description != before.description:
        changes.append(f"- the scene is now: {scene.description.strip()}")
    directive = request.directive
    if directive is not None and directive.kind is RevisionKind.REPAIR:
        for r in directive.enforce:
            subject = r.entity_key or "the scene"
            if r.related_entity_key is not None:
                changes.append(
                    f"- {subject}: must be shown as {predicate_words(r.predicate)} {r.related_entity_key}"
                )
            else:
                changes.append(
                    f"- {subject}: {predicate_words(r.predicate)} must be {r.expected_value}"
                )
    if not changes:
        return None
    lines = ["Edit the input video. Apply only these changes:", *changes]
    if directive is not None and directive.keep:
        lines.append(f"Do not change: {_keep_phrase(directive.keep)}.")
    lines.append(EDIT_KEEP_LINE)
    return "\n".join(lines)


def _reference_phrase(numbers: Sequence[int]) -> str:
    """input_references 의 순서 번호로 참조 이미지를 가리킨다: 'Image 1', 'Image 1 and Image 2'.

    Wan 의 참조 영상 생성 문서가 정한 지칭이다 (첫 글자 대문자, 낱말과 숫자 사이 한 칸, 배열 순서와 같은 번호).
    다른 모델도 자연어로 읽을 수 있는 표현이다.
    """
    return " and ".join(f"Image {n}" for n in numbers)
