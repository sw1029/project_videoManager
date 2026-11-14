"""사용자가 녹음한 음성을 편에 붙인다 (US-06, ADR-0026). 음성은 합성하지 않는다.

받는 녹음
- 편 전체를 한 번에 읽은 파일 하나 (line_key 없음): 인식 결과를 대본 줄에 맞춘다.
- 줄마다 따로 녹음한 파일 (line_key 있음): 파일 전체가 그 줄이다. 인식 결과로 대본과 맞는지만 본다.
  이미 녹음을 붙인 편에 일부 줄만 다시 주면 그 줄만 갈아 끼운다 (국소 수정: 나머지 줄은 예전 녹음 그대로).

순서: 인식 -> 줄에 맞추기 -> 장면에 놓기 -> 편 길이의 트랙 만들기 -> 저장.
대본과 다르게 읽었거나(핵심어가 빠짐 포함), 읽지 않았거나, 장면보다 긴 줄은 결과에 남는다. 붙이는 것 자체는 막지 않는다.
영상에 소리를 합치는 것은 완성본을 만들 때(RenderEpisode) 한다.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import PurePosixPath

from frame.domain.episode import ScriptLine
from frame.domain.errors import NotFound, ValidationError
from frame.domain.series import Episode, NarrationResult
from frame.domain.services.narration import (
    DEFAULT_NARRATION_POLICY,
    LineAlignment,
    LineStatus,
    NarrationPolicy,
    PlacementStatus,
    align_script,
    check_line,
    place_lines,
)
from frame.domain.video_analysis import Transcript

from ..ports.artifact_store import ArtifactStore
from ..ports.audio import AudioEditor, TrackCut
from ..ports.theme_store import EpisodeRepository
from ..ports.video_analysis import SpeechRecognizer

_SUFFIX = re.compile(r"^\.[a-z0-9]{1,5}$")


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
        transcripts: list[Transcript] = []
        key_terms = tuple(v for v in episode.plan.variables.values() if v.strip())
        for number, recording in enumerate(command.recordings):
            transcripts.append(self._recognize(recording, command.language, key_terms))
            sources.append(recording.data)
            key = f"episodes/{episode.episode_id}/recordings/{offset + number}{_suffix(recording)}"
            uris.append(self._store.put(key, recording.data))
        durations = [self._duration(data) for data in sources]

        if whole:
            alignments = align_script(script, transcripts[0], self._policy, key_terms=key_terms)
        else:
            kept = {a.line_key: a for a in previous.alignments} if previous is not None else {}
            for number, recording in enumerate(command.recordings):
                assert recording.line_key is not None
                kept[recording.line_key] = check_line(
                    episode.plan.line(recording.line_key),
                    transcripts[number],
                    source_index=offset + number,
                    policy=self._policy,
                    key_terms=key_terms,
                )
            alignments = tuple(
                kept.get(line.line_key, LineAlignment(line.line_key, LineStatus.MISSING))
                for line in script
            )
        spans = {
            scene.scene_key: (scene.planned_start_ms, scene.planned_end_ms)
            for scene in episode.plan.ir.scenes
        }
        placements = place_lines(script, alignments, spans, durations, self._policy)
        cuts = [
            TrackCut(p.source_index, p.source_start_ms, p.source_end_ms, p.target_start_ms, p.tempo)
            for p in placements
            if p.status is not PlacementStatus.MISSING
        ]
        track = self._audio.render_track(sources, cuts, episode.plan.ir.total_duration_ms)
        track_uri = self._store.put(
            f"episodes/{episode.episode_id}/narration{self._audio.track_suffix}", track
        )
        updated = replace(
            episode,
            narration=NarrationResult(tuple(uris), tuple(alignments), tuple(placements), track_uri),
            render=None,  # 소리가 바뀌었으니 완성본은 다시 만들어야 한다
        )
        self._episodes.save(updated)
        return updated

    def _recognize(
        self, recording: RecordingInput, language: str | None, key_terms: Sequence[str]
    ) -> Transcript:
        if not recording.data:
            raise ValidationError("녹음 파일이 비어 있습니다")
        speech = self._audio.speech_audio(recording.data)
        if speech is None:
            raise ValidationError("녹음 파일에 소리가 없습니다 (읽을 수 없는 형식일 수 있습니다)")
        assert self._recognizer is not None
        transcript = self._recognizer.transcribe(
            speech, language=language, hint=", ".join(key_terms)
        )
        transcript.validate()
        return transcript

    def _duration(self, data: bytes) -> int:
        info = self._audio.probe(data)
        if info is None:
            raise ValidationError("녹음 파일에 소리가 없습니다")
        return info.duration_ms


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
    return suffix if _SUFFIX.match(suffix) else ".bin"
