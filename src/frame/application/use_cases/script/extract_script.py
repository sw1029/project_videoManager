"""영상·음성 파일의 소리에서 대본을 뽑는다 (ADR-0028).

1. 소리를 읽는다: 길이와 무음 구간 (SoundReader). 소리가 없으면 빈 대본으로 끝난다 (인식기를 부르지 않는다).
2. 말소리를 받아 적는다 (SpeechRecognizer, 과금). 같은 소리를 다시 받아 적지 않는 것은 인식기 쪽의 일이다
   (결과를 남기는 래퍼 CachingSpeechRecognizer).
3. 받아쓰기를 줄로 다듬는다 (domain/script 의 build_script): 줄 나누기 · 장면 배정 · 신뢰도 · 말이 아닌 소리.
4. 말할 사람이 화면에 없는 장면의 줄은 내레이션으로 정한다 (classify_kinds).

저장하지 않는다. 대본을 어디에 둘지는 부른 쪽이 정한다 (테마 추출은 테마의 기록에 함께 둔다).
인식 서비스의 오류(SpeechServiceError·SpeechRecognitionError)는 그대로 올린다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from frame.application.ports.sound import SoundReader
from frame.application.ports.speech import SpeechRecognizer
from frame.domain.errors import ValidationError
from frame.domain.script import (
    DEFAULT_SCRIPT_POLICY,
    SceneSpan,
    ScriptPolicy,
    SpokenScript,
    build_script,
    classify_kinds,
)
from frame.domain.speech import Transcript


@dataclass(frozen=True)
class ExtractScriptCommand:
    media: bytes  # 영상 또는 음성 파일
    scenes: tuple[SceneSpan, ...] = ()  # 장면의 시간. 주면 줄마다 걸치는 장면을 적는다
    language: str | None = None  # 말소리의 언어 (알면 인식이 안정된다)
    hint: str = ""  # 인식을 돕는 낱말 (상품 이름 같은 고유명)
    # 장면 키 -> 말할 수 있는 사람이 보이는가. 보이지 않는 장면의 줄은 내레이션이다
    speaker_visible: Mapping[str, bool] = field(default_factory=dict)


@dataclass(frozen=True)
class ExtractScriptResult:
    script: SpokenScript
    transcript: Transcript | None  # 인식기가 준 그대로. 소리가 없었으면 None
    has_sound: bool


class ExtractScript:
    def __init__(
        self,
        sound: SoundReader,
        recognizer: SpeechRecognizer | None,
        policy: ScriptPolicy = DEFAULT_SCRIPT_POLICY,
    ) -> None:
        self._sound = sound
        self._recognizer = recognizer
        self._policy = policy

    def execute(self, command: ExtractScriptCommand) -> ExtractScriptResult:
        if not command.media:
            raise ValidationError("대본을 뽑을 파일이 비어 있습니다")
        if self._recognizer is None:
            raise ValidationError(
                "음성 인식이 꺼져 있어 대본을 뽑을 수 없습니다 (FRAME_SPEECH_RECOGNIZER)"
            )
        profile = self._sound.sound_profile(command.media)
        speech = self._sound.speech_audio(command.media) if profile is not None else None
        if profile is None or speech is None:
            return ExtractScriptResult(build_script(None, command.scenes), None, has_sound=False)
        transcript = self._recognizer.transcribe(
            speech, language=command.language, hint=command.hint
        )
        transcript.validate(profile.duration_ms)
        script = build_script(
            transcript,
            command.scenes,
            duration_ms=profile.duration_ms,
            silences=profile.silences,
            policy=self._policy,
        )
        script = classify_kinds(script, command.speaker_visible)
        script.validate()
        return ExtractScriptResult(script, transcript, has_sound=True)
