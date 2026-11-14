"""테마 추출 모델에 주는 지시문 (ADR-0025). 프롬프트를 고치면 PROMPT_VERSION 을 올린다.

어휘(술어 이름)는 IR 의 어휘에서 만들어 넣는다. 값은 영어로, 얹은 문구는 객체가 아니라 화면 문구로,
카메라는 스타일이 아니라 자리의 조건으로 적게 한다 (실호출에서 나온 것을 막은 규칙들).
"""

from __future__ import annotations

from collections.abc import Sequence

from frame.domain.enums import EntityKind
from frame.domain.screen import TextRole
from frame.domain.script import SceneSpan, build_script, quantities
from frame.domain.speech import Transcript
from frame.domain.vocabulary import DEFAULT_VOCABULARY, PredicateVocabulary

from ._answer_parts import _SCENE_LEVEL

PROMPT_VERSION = "2026-11-14.7"


SCHEMA_NAME = "theme_observation"


def build_system_prompt(vocabulary: PredicateVocabulary = DEFAULT_VOCABULARY) -> str:
    attributes = ", ".join(
        s.name
        for s in vocabulary.specs()
        if not s.relational and not s.transient and s.name not in _SCENE_LEVEL
    )
    transient = ", ".join(s.name for s in vocabulary.specs() if s.transient)
    relations = ", ".join(s.name for s in vocabulary.specs() if s.relational)
    kinds = ", ".join(str(k) for k in EntityKind)
    roles = ", ".join(str(r) for r in TextRole)
    return f"""You analyse ONE short-form video so that new episodes of the same series can be made.
You get sampled frames grouped by scene slot (S1, S2, ... in time order; each frame is labelled
like "S2-F1 5.3s"), the time range of every slot, and the script of the speech: one line per
sentence with the slot it was said in and its time (it may be empty). Numbers said aloud are also
given as values, e.g. "(values: 5000원)". Return ONLY JSON that matches the schema.

Entities
- List every person, animal and object that matters. entity_key is a unique snake_case ASCII name
  for what the thing is, never how it looks: "mug", not "red_mug". kind is one of {kinds}.
- recurring is true when the entity would come back in other episodes of the series (the host, a
  mascot, a signature prop) and false when it belongs to this episode's topic (today's product).
- fixed_attributes are looks that stay the same through the whole video. Predicates: {attributes}.
  Never put actions, poses, expressions, positions or relations there. Values are short lowercase
  English phrases.

Slots (one entry per slot, in order)
- purpose: what this part does in the format, worded so that it fits any episode
  ("the host greets the viewer", "close-up of today's product").
- description: what this video actually shows in the slot.
- purpose and description say what is filmed (people, objects, actions, place). Do not describe
  overlay text there ("shows the price"): overlay text is recorded in screen_texts and text_rules.
- entity_keys: the entities visible in the slot.
- requirements: what is visible in the slot. Object attributes ({attributes}) and what the entity
  does ({transient}) need entity_key. Relations ({relations}) need entity_key and
  related_entity_key, and never involve a BACKGROUND entity. Scene-level conditions
  ({", ".join(_SCENE_LEVEL)}) use entity_key null.
- narration_purpose: what the speech does during the slot ("names the product"), or "" if silent.
- State an attribute in every slot where it can be seen: a red mug is "color: red" in each slot
  it appears in.
- Use only the predicate names listed above. If something does not fit any of them, leave it
  out of requirements and mention it in the description instead.
- Every value is a short lowercase ENGLISH phrase ("white shirt and brown apron", "close up",
  "waves hand to camera"), whatever language the video and the other fields use. The only
  exceptions are text and brand, which keep the exact spelling seen on screen.

Style: conditions that hold for the whole video (scene-level predicates only, and never camera:
framing changes from slot to slot, so camera belongs to the slots), 3 to 5 palette colour names,
and notes on composition, texture and editing feel.

On-screen text
- Text added in editing (OVERLAY) is never an entity and never a requirement: it goes only into
  screen_texts and text_rules. Only text that is physically in the scene (IN_SCENE) may be a
  TEXT entity or a text/brand requirement.
- screen_texts: every piece of text you can read. Copy it exactly, character by character; do not
  correct it, not even when the script says something different (the two are compared later).
  A caption that writes down what is being said has role CAPTION. role is one of {roles}.
  source is OVERLAY for text added in editing (captions, titles, price tags, watermarks) and
  IN_SCENE for text that is physically in the scene (signs,
  product labels). box is the position as fractions of the frame (x, y = top-left corner, w, h),
  or null if you cannot tell.
- text_rules: generalise the OVERLAY texts into rules for future episodes. template is either
  fixed text or text with variables in braces ("{{menu}} {{price}}"). example is the text seen in
  this video. color and background are CSS colour names ("white", "lightyellow") or #RRGGBB
  (background null if none).
- variables: the values that change from episode to episode (names, prices, per-slot captions).
  key is snake_case ASCII, example is this video's value, scope is EPISODE (one value per
  episode) or SLOT (a different value in each slot). Declare every variable a template uses.
  A value may be said in the script without being shown on screen (a price that is only spoken):
  declare that variable too, with the example written the way it would appear on screen
  ("5,000원").

Narration tone: how the speaker talks (register, energy), judged from the transcript; "" if there
is no speech. Music: you only see frames, so present is null and mood is "".
Do not invent anything the frames and the transcript do not show."""


def script_block(transcript: Transcript | None, spans: Sequence[tuple[int, int]]) -> list[str]:
    """질의에 넣을 대본: 줄마다 어느 자리에서 언제 한 말인지, 말로 한 수는 값으로도 적는다.

    받아쓰기를 그대로 주지 않고 대본의 줄로 다듬어 준다 (domain/script).
    자리는 질의에서 쓰는 이름(S1, S2 ...)이다.
    """
    scenes = [SceneSpan(f"S{index + 1}", start, end) for index, (start, end) in enumerate(spans)]
    script = build_script(transcript, scenes)
    if script.is_empty:
        return ["Script: none (no speech was recognised)."]
    lines = [
        (
            f"Script (language: {script.language or 'unknown'}), "
            "one line per sentence with the slot it was said in:"
        )
    ]
    for number, line in enumerate(script.lines, start=1):
        slots = "+".join(line.scene_keys) or "no slot"
        values = ", ".join(quantity.text for quantity in quantities(line.text))
        lines.append(
            f"line {number} [{slots} {line.start_ms / 1000:.1f}-{line.end_ms / 1000:.1f}s] "
            f"{line.text}" + (f" (values: {values})" if values else "")
        )
    return lines
