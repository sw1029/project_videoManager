"""영상·음성 파일의 소리에서 대본을 뽑는다 (ADR-0028).

1. 소리를 읽는다: 길이와 무음 구간 (SoundReader). 소리가 없으면 빈 대본으로 끝난다 (인식기를 부르지 않는다).
2. 말소리를 받아 적는다 (SpeechRecognizer, 과금). 처음부터 끝까지 무음이면 부르지 않고, 줄이 없는 소리는 그 구간만
   다시 받아 적는다 (listening.listen). 같은 소리를 다시 받아 적지 않는 것은 인식기 쪽의 일이다
   (결과를 남기는 래퍼 CachingSpeechRecognizer).
3. 받아쓰기를 줄로 다듬는다 (domain/script 의 build_script): 줄 나누기 · 장면 배정 · 신뢰도 · 말이 아닌 소리.
4. 말할 사람이 화면에 없는 장면의 줄은 내레이션으로 정한다 (classify_kinds).

저장하지 않는다. 대본을 어디에 둘지는 부른 쪽이 정한다 (테마 추출은 테마의 기록에 함께 둔다).
인식 서비스의 오류(SpeechServiceError·SpeechRecognitionError)는 그대로 올린다.
받기 전에 보는 것 (감사 BTS-D37): 파일의 크기(MAX_MEDIA_BYTES)와 소리의 길이(MAX_MEDIA_MS). 읽을 수 없는 파일은
'소리 없는 파일' 이 아니라 입력 오류(ValidationError)다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace

from frame.application.errors import AdapterError
from frame.application.ports.sound import SoundReader
from frame.application.ports.speech import SpeechRecognizer
from frame.application.use_cases.script.listening import (
    DEFAULT_LISTENING_POLICY,
    ListeningPolicy,
    listen,
)
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

# 겉모습을 읽기 전에 보는 크기 상한. 파일은 단계마다 임시 파일로 다시 쓰이므로 터무니없이 큰 것을 먼저 끊는다.
# 숏폼 플랫폼이 받는 크기(Reels 300MB)보다 넉넉하게 둔다. 테마 추출도 이 값을 쓴다 (감사 BTS-30)
MAX_MEDIA_BYTES = 512 * 1024 * 1024
# 받아 적을 소리의 길이 상한. 숏폼(3분)과 그 녹음을 넉넉히 덮는다. 받아 적기는 분 단위로 과금하고,
# 테마 추출의 자리 묶기는 샷 수가 늘면 급히 느려진다. 숏폼 판정을 꺼도 이 길이는 넘지 못한다
MAX_MEDIA_MS = 20 * 60 * 1000


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
        listening: ListeningPolicy = DEFAULT_LISTENING_POLICY,
    ) -> None:
        self._sound = sound
        self._recognizer = recognizer
        self._policy = policy
        self._listening = listening

    def execute(self, command: ExtractScriptCommand) -> ExtractScriptResult:
        if not command.media:
            raise ValidationError("대본을 뽑을 파일이 비어 있습니다")
        if self._recognizer is None:
            raise ValidationError(
                "음성 인식이 꺼져 있어 대본을 뽑을 수 없습니다 (FRAME_SPEECH_RECOGNIZER)"
            )
        if len(command.media) > MAX_MEDIA_BYTES:
            raise ValidationError(
                f"파일이 너무 큽니다: {len(command.media) / 1_048_576:.0f}MB "
                f"(상한 {MAX_MEDIA_BYTES // 1_048_576}MB)"
            )
        try:
            profile = self._sound.sound_profile(command.media)
        except AdapterError as exc:  # 영상·음성 파일이 아니거나 깨졌다 — 입력의 문제다
            raise ValidationError(
                "파일을 읽을 수 없습니다 (영상·음성 파일이 아니거나 깨졌습니다)"
            ) from exc
        if profile is None:
            return ExtractScriptResult(build_script(None, command.scenes), None, has_sound=False)
        if profile.duration_ms > MAX_MEDIA_MS:
            raise ValidationError(
                f"소리가 너무 깁니다: {profile.duration_ms / 60_000:.0f}분 "
                f"(상한 {MAX_MEDIA_MS // 60_000}분)"
            )
        heard = listen(
            self._sound,
            self._recognizer,
            command.media,
            profile,
            language=command.language,
            hint=command.hint,
            script_policy=self._policy,
            policy=self._listening,
        )
        script = build_script(
            heard.transcript,
            command.scenes,
            duration_ms=profile.duration_ms,
            silences=profile.silences,
            policy=self._policy,
        )
        script = classify_kinds(script, command.speaker_visible)
        script = replace(script, notes=(*heard.notes, *script.notes))
        script.validate()
        return ExtractScriptResult(script, heard.transcript, has_sound=True)
