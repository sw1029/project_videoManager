"""사용자가 녹음한 음성을 편에 붙인다 (US-06, ADR-0026). 음성은 합성하지 않는다.

받는 녹음
- 편 전체를 한 번에 읽은 파일 하나 (line_key 없음): 인식 결과를 대본 줄에 맞춘다.
- 줄마다 따로 녹음한 파일 (line_key 있음): 파일 전체가 그 줄이다. 인식 결과로 대본과 맞는지만 본다.
  이미 녹음을 붙인 편에 일부 줄만 다시 주면 그 줄만 갈아 끼운다 (국소 수정: 나머지 줄은 예전 녹음 그대로).

순서: 인식 -> 줄에 맞추기 -> 소리가 나는 곳으로 자르기 -> 장면에 놓기 -> 편 길이의 트랙 만들기 -> 저장.
인식기는 녹음 앞의 무음을 말에 붙여 준다. 녹음의 무음을 재서 줄의 구간을 자른다 (말이 제때 시작한다).
대본과 다르게 읽었거나(핵심어가 빠짐 포함), 읽지 않았거나, 장면보다 긴 줄은 결과에 남는다. 붙이는 것 자체는 막지 않는다.
영상에 소리를 합치는 것은 완성본을 만들 때(RenderEpisode) 한다.

과금하기 전에 (감사 BTS-12·23·30)
- 모든 녹음을 먼저 확인한다 (비었는가, 소리가 있는가, 길이가 맞는가). 하나라도 걸리면 아무것도 인식하지 않는다.
- 녹음이 덮는 시간(편 전체 또는 그 줄의 장면)보다 터무니없이 길면 거절한다. 인식은 분 단위로 과금한다.
- 녹음 파일은 내용의 해시로 이름을 짓는다. 편이 가리키는 파일의 내용이 나중에 바뀌지 않는다
  (번호로 지으면 실패한 붙이기가 예전 파일을 덮어써, 예전에 잰 시각이 새 파일에 적용됐다).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import PurePosixPath

from frame.application.errors import AdapterError
from frame.application.ports.artifact_store import ArtifactStore
from frame.application.ports.series.audio import AudioEditor
from frame.application.ports.series.repositories import EpisodeRepository
from frame.application.ports.speech import SpeechAudio, SpeechRecognizer
from frame.application.use_cases.series._narration_track import narration_track
from frame.domain.errors import NotFound, ValidationError
from frame.domain.script import audible_spans
from frame.domain.series.episode import ScriptLine
from frame.domain.series.narration import (
    DEFAULT_NARRATION_POLICY,
    LineAlignment,
    LineStatus,
    NarrationPolicy,
    align_script,
    check_line,
    fit_alignment,
)
from frame.domain.series.record import Episode
from frame.domain.speech import Transcript

_SUFFIX = re.compile(r"\.[a-z0-9]{1,5}")
# 녹음은 그것이 덮는 시간(편 전체, 또는 그 줄의 장면)의 이 배수 + 여유까지만 받는다.
# 다시 읽은 부분·쉼을 넉넉히 봐준 값이다. 이보다 길면 장면에 놓을 수 없고, 인식 과금만 는다
MAX_RECORDING_FACTOR = 3.0
MAX_RECORDING_SLACK_MS = 10_000


@dataclass(frozen=True)
class RecordingInput:
    data: bytes
    line_key: str | None = None  # 줄마다 녹음했으면 그 줄. 없으면 편 전체를 읽은 녹음
    filename: str = ""  # 확장자를 저장 형식의 실마리로 쓴다


@dataclass(frozen=True)
class AttachNarrationCommand:
    episode_id: str
    recordings: tuple[RecordingInput, ...]
    language: str | None = None


class AttachNarration:
    def __init__(
        self,
        episodes: EpisodeRepository,
        store: ArtifactStore,
        audio: AudioEditor,
        recognizer: SpeechRecognizer | None,
        policy: NarrationPolicy = DEFAULT_NARRATION_POLICY,
    ) -> None:
        self._episodes = episodes
        self._store = store
        self._audio = audio
        self._recognizer = recognizer
        self._policy = policy

    def execute(self, command: AttachNarrationCommand) -> Episode:
        # 인식은 오래 걸린다. 그 사이 다른 유스케이스가 고친 문구·버전을 마지막 저장이 지우지 않게 편의 키를 쥔다
        with self._episodes.lock(command.episode_id):
            return self._attach(command)

    def _attach(self, command: AttachNarrationCommand) -> Episode:
        episode = self._episodes.get(command.episode_id)
        if episode is None:
            raise NotFound(
                f"편 {command.episode_id} 을 찾을 수 없습니다", episode_id=command.episode_id
            )
        script = episode.plan.script
        if not script:
            raise ValidationError("이 편에는 녹음 대본이 없습니다")
        if self._recognizer is None:
            raise ValidationError(
                "음성 인식이 꺼져 있어 녹음을 대본에 맞출 수 없습니다 (FRAME_SPEECH_RECOGNIZER)"
            )
        whole = _mode(command.recordings, script)

        # 예전 녹음을 이어 쓰는 경우(일부 줄만 다시 녹음)에는 그 파일들이 앞 번호를 차지한다
        previous = None if whole else episode.narration
        uris = list(previous.recording_uris) if previous is not None else []
        sources = [self._store.get(uri) for uri in uris]
        offset = len(sources)
        key_terms = tuple(v for v in episode.plan.variables.values() if v.strip())
        # 1) 과금하기 전에 모든 녹음을 확인한다
        prepared = [self._prepare(episode, recording) for recording in command.recordings]
        # 2) 내용으로 이름을 지어 둔다 (편이 가리키는 파일의 내용은 바뀌지 않는다)
        for recording in command.recordings:
            sources.append(recording.data)
            digest = hashlib.sha256(recording.data).hexdigest()[:16]
            key = f"episodes/{episode.episode_id}/recordings/{digest}{_suffix(recording)}"
            uris.append(self._store.put(key, recording.data))
        # 3) 인식 (과금)
        transcripts: list[Transcript] = []
        for speech in prepared:
            transcript = self._recognizer.transcribe(
                speech, language=command.language, hint=", ".join(key_terms)
            )
            transcript.validate()
            transcripts.append(transcript)
        if whole:
            sound = self._sounding(command.recordings[0].data)
            alignments = tuple(
                fit_alignment(alignment, sound)
                for alignment in align_script(
                    script, transcripts[0], self._policy, key_terms=key_terms
                )
            )
        else:
            kept = {a.line_key: a for a in previous.alignments} if previous is not None else {}
            for number, recording in enumerate(command.recordings):
                if recording.line_key is None:  # _mode 가 이미 걸렀다
                    raise ValidationError("줄마다 녹음한 파일에는 줄(line_key)이 있어야 합니다")
                checked = check_line(
                    episode.plan.line(recording.line_key),
                    transcripts[number],
                    source_index=offset + number,
                    policy=self._policy,
                    key_terms=key_terms,
                )
                kept[recording.line_key] = fit_alignment(checked, self._sounding(recording.data))
            alignments = tuple(
                kept.get(line.line_key, LineAlignment(line.line_key, LineStatus.MISSING))
                for line in script
            )
        narration = narration_track(
            self._store, self._audio, episode, uris, sources, alignments, self._policy
        )
        # 소리가 바뀌었으니 완성본은 다시 만들어야 한다
        updated = replace(episode, narration=narration, render=None)
        self._episodes.save(updated)
        return updated

    def _sounding(self, recording: bytes) -> tuple[tuple[int, int], ...]:
        """녹음에서 소리가 나는 구간. 재지 못하면 비어 있다 (줄의 구간을 인식기가 준 대로 쓴다)."""
        try:
            profile = self._audio.sound_profile(recording)
        except AdapterError:
            return ()
        if profile is None:
            return ()
        return audible_spans(profile.duration_ms, profile.silences) or ()

    def _prepare(self, episode: Episode, recording: RecordingInput) -> SpeechAudio:
        """녹음 하나를 인식할 수 있는 소리로 바꾼다. 받을 수 없는 녹음이면 ValidationError (인식하기 전이다)."""
        if not recording.data:
            raise ValidationError("녹음 파일이 비어 있습니다")
        unreadable = "녹음 파일에 소리가 없습니다 (읽을 수 없는 형식일 수 있습니다)"
        try:
            speech = self._audio.speech_audio(recording.data)
            info = self._audio.probe(recording.data)
        except AdapterError as exc:  # 실제 편집기는 읽을 수 없는 파일에 오류를 던진다 (가짜는 None)
            raise ValidationError(unreadable) from exc
        if speech is None or info is None:
            raise ValidationError(unreadable)
        covered = episode.plan.ir.total_duration_ms
        if recording.line_key is not None:
            covered = episode.plan.ir.scene(
                episode.plan.line(recording.line_key).scene_key
            ).duration_ms
        limit = int(covered * MAX_RECORDING_FACTOR) + MAX_RECORDING_SLACK_MS
        if info.duration_ms > limit:
            raise ValidationError(
                f"녹음이 너무 깁니다: {info.duration_ms / 1000:.0f}초 "
                f"(이 녹음이 덮는 시간은 {covered / 1000:.0f}초, 받는 상한은 {limit / 1000:.0f}초)"
            )
        return speech


def _mode(recordings: Sequence[RecordingInput], script: Sequence[ScriptLine]) -> bool:
    """편 전체를 읽은 녹음 하나면 True, 줄마다 녹음한 것이면 False. 섞여 있으면 ValidationError."""
    if not recordings:
        raise ValidationError("녹음 파일이 하나 이상 있어야 합니다")
    keys = [r.line_key for r in recordings]
    if all(key is None for key in keys):
        if len(recordings) != 1:
            raise ValidationError(
                "편 전체를 읽은 녹음은 하나만 받을 수 있습니다. 줄마다 녹음했다면 줄(line_key)을 적어 주세요"
            )
        return True
    if any(key is None for key in keys):
        raise ValidationError("편 전체를 읽은 녹음과 줄마다 녹음한 것을 함께 줄 수 없습니다")
    if len(keys) != len(set(keys)):
        raise ValidationError(f"같은 줄의 녹음이 두 번 들어 있습니다: {keys}")
    known = {line.line_key for line in script}
    unknown = sorted(key for key in keys if key not in known)
    if unknown:
        raise ValidationError(f"대본에 없는 줄입니다: {unknown}")
    return False


def _suffix(recording: RecordingInput) -> str:
    suffix = PurePosixPath(recording.filename.replace("\\", "/")).suffix.lower()
    return suffix if _SUFFIX.fullmatch(suffix) else ".bin"  # match 는 줄 끝의 \\n 앞에서도 맞는다
