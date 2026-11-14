"""ffmpeg 기반 소리 편집기 (ADR-0026). 사용자가 녹음한 음성을 잘라 장면에 놓고 영상에 붙인다.

- speech_audio: 모노 16 kHz WAV. 음성 인식기에 넘기기 위한 것이다. 구간을 주면 그 구간만 자른다.
- render_track: 조각마다 atrim(자르기) -> atempo(빠르기, 음높이 유지) -> adelay(놓을 시각) 를 걸고,
  total_ms 길이의 무음 위에 섞는다. 결과는 모노 WAV (표본화율은 sample_rate).
  normalize=True 면 마지막에 음량을 EBU R128 기준(-16 LUFS)으로 맞춘다. 기본은 녹음을 건드리지 않는다.
- mux: 영상 스트림은 복사하고(다시 인코딩하지 않는다) 소리만 AAC 로 붙인다. 길이는 영상의 길이다.
"""

from __future__ import annotations

import tempfile
from collections.abc import Sequence
from pathlib import Path

from frame.adapters.media_tools import (
    MediaToolError,
    detect_silences,
    probe_media,
    run_ffmpeg,
    tail,
)
from frame.adapters.video_tools import require_ffmpeg
from frame.application.ports.series.audio import AudioEditor, AudioError, AudioInfo, TrackCut
from frame.application.ports.sound import SoundProfile
from frame.application.ports.speech import SpeechAudio

SPEECH_SAMPLE_RATE = 16000


class FfmpegAudioEditor(AudioEditor):
    def __init__(
        self,
        ffmpeg: str | None,
        *,
        sample_rate: int = 44100,
        normalize: bool = False,
        audio_bitrate: str = "128k",
        timeout_s: float = 300.0,
    ) -> None:
        self._ffmpeg = require_ffmpeg(ffmpeg)
        self.sample_rate = sample_rate
        self.normalize = normalize
        self.audio_bitrate = audio_bitrate
        self.timeout_s = timeout_s

    def probe(self, media: bytes) -> AudioInfo | None:
        with tempfile.TemporaryDirectory(prefix="frame-audio-") as tmp:
            path = Path(tmp) / "media"
            path.write_bytes(media)
            return self._info(path)

    def speech_audio(
        self, media: bytes, *, start_ms: int | None = None, end_ms: int | None = None
    ) -> SpeechAudio | None:
        with tempfile.TemporaryDirectory(prefix="frame-audio-") as tmp:
            source, target = Path(tmp) / "media", Path(tmp) / "speech.wav"
            source.write_bytes(media)
            info = self._info(source)
            if info is None:
                return None
            start = max(0, min(start_ms or 0, info.duration_ms))
            end = info.duration_ms if end_ms is None else max(start, min(end_ms, info.duration_ms))
            cmd = [self._ffmpeg, "-hide_banner", "-nostdin", "-y", "-i", str(source), "-vn"]
            if (start, end) != (0, info.duration_ms):  # 구간만 꺼낸다 (시각은 0부터)
                cmd += [
                    "-af",
                    f"atrim=start={start / 1000:.3f}:end={end / 1000:.3f},asetpts=PTS-STARTPTS",
                ]
            cmd += ["-ac", "1", "-ar", str(SPEECH_SAMPLE_RATE), "-c:a", "pcm_s16le", str(target)]
            self._run(cmd, target, "인식용 소리를 만들지 못했습니다")
            return SpeechAudio(target.read_bytes(), "audio/wav", end - start)

    def sound_profile(self, media: bytes) -> SoundProfile | None:
        with tempfile.TemporaryDirectory(prefix="frame-audio-") as tmp:
            path = Path(tmp) / "media"
            path.write_bytes(media)
            info = self._info(path)
            if info is None:
                return None
            try:
                found = detect_silences(self._ffmpeg, path, timeout_s=self.timeout_s)
            except MediaToolError as exc:
                raise AudioError(f"무음 구간을 재지 못했습니다: {exc}") from exc
        quiet: list[tuple[int, int]] = []
        for start, end in found:
            # 파일 끝까지 조용하면 끝이 없다. 길이로 닫는다
            span = (
                max(0, start),
                min(info.duration_ms, end if end is not None else info.duration_ms),
            )
            if span[1] > span[0] and (not quiet or span[0] >= quiet[-1][1]):
                quiet.append(span)
        return SoundProfile(info.duration_ms, tuple(quiet))

    def render_track(
        self, sources: Sequence[bytes], cuts: Sequence[TrackCut], total_ms: int
    ) -> bytes:
        if total_ms <= 0:
            raise AudioError(f"트랙 길이가 잘못되었습니다: {total_ms} ms")
        for cut in cuts:
            # 조각의 시각·빠르기는 TrackCut 이 만들 때 검증한다. 여기서는 녹음이 있는지만 본다
            if not 0 <= cut.source_index < len(sources):
                raise AudioError(f"조각이 가리키는 녹음이 없습니다: {cut.source_index}")
        with tempfile.TemporaryDirectory(prefix="frame-audio-") as tmp:
            folder = Path(tmp)
            used = sorted({cut.source_index for cut in cuts})
            cmd = [self._ffmpeg, "-hide_banner", "-nostdin", "-y"]
            # 입력 0: 트랙 길이만큼의 무음 (조각이 없어도 길이가 정해진다)
            cmd += ["-f", "lavfi", "-t", f"{total_ms / 1000:.3f}"]
            cmd += ["-i", f"anullsrc=r={self.sample_rate}:cl=mono"]
            position: dict[int, int] = {}
            for index in used:
                path = folder / f"source_{index}"
                path.write_bytes(sources[index])
                if self._info(path) is None:
                    raise AudioError(f"녹음 {index} 에 소리가 없습니다")
                position[index] = len(position) + 1
                cmd += ["-i", str(path)]
            chains: list[str] = []
            labels = ["[0:a]"]
            for number, cut in enumerate(cuts):
                chain = (
                    f"[{position[cut.source_index]}:a]"
                    f"atrim=start={cut.source_start_ms / 1000:.3f}:end={cut.source_end_ms / 1000:.3f},"
                    f"asetpts=PTS-STARTPTS,aformat=sample_rates={self.sample_rate}:channel_layouts=mono"
                )
                if abs(cut.tempo - 1.0) > 1e-6:
                    chain += f",atempo={cut.tempo:.6f}"
                chain += f",adelay={cut.target_start_ms}:all=1[c{number}]"
                chains.append(chain)
                labels.append(f"[c{number}]")
            mix = (
                f"{''.join(labels)}amix=inputs={len(labels)}:duration=first:normalize=0,"
                f"atrim=end={total_ms / 1000:.3f}"
            )
            if self.normalize and cuts:
                mix += ",loudnorm=I=-16:TP=-1.5:LRA=11"
            chains.append(mix + "[out]")
            target = folder / "track.wav"
            cmd += ["-filter_complex", ";".join(chains), "-map", "[out]"]
            cmd += ["-ac", "1", "-ar", str(self.sample_rate), "-c:a", "pcm_s16le", str(target)]
            self._run(cmd, target, "트랙을 만들지 못했습니다")
            return target.read_bytes()

    def mux(self, video: bytes, track: bytes) -> bytes:
        with tempfile.TemporaryDirectory(prefix="frame-audio-") as tmp:
            folder = Path(tmp)
            picture, sound, target = folder / "video.mp4", folder / "track", folder / "out.mp4"
            picture.write_bytes(video)
            sound.write_bytes(track)
            try:
                found = probe_media(self._ffmpeg, picture, self.timeout_s)
            except MediaToolError as exc:
                raise AudioError(f"영상을 읽지 못했습니다: {exc}") from exc
            if not found.has_video or not found.duration_ms:
                raise AudioError("소리를 붙일 영상에 영상 스트림이 없습니다")
            if self._info(sound) is None:
                raise AudioError("붙일 트랙에 소리가 없습니다")
            seconds = f"{found.duration_ms / 1000:.3f}"
            cmd = [self._ffmpeg, "-hide_banner", "-nostdin", "-y"]
            cmd += ["-i", str(picture), "-i", str(sound)]
            # 트랙이 영상보다 짧으면 무음으로 채우고, 길면 영상 길이에서 자른다
            cmd += ["-filter_complex", f"[1:a]apad,atrim=end={seconds}[a]"]
            cmd += ["-map", "0:v:0", "-map", "[a]", "-c:v", "copy"]
            cmd += ["-c:a", "aac", "-b:a", self.audio_bitrate, "-t", seconds]
            cmd += ["-map_metadata", "-1", "-movflags", "+faststart", str(target)]
            self._run(cmd, target, "영상에 소리를 붙이지 못했습니다")
            return target.read_bytes()

    # ---- 내부 ----
    def _info(self, path: Path) -> AudioInfo | None:
        try:
            found = probe_media(self._ffmpeg, path, self.timeout_s)
        except MediaToolError as exc:
            raise AudioError(f"파일을 읽지 못했습니다: {exc}") from exc
        if not found.has_audio or not found.duration_ms:
            return None
        return AudioInfo(found.duration_ms, found.sample_rate, found.channels, found.audio_codec)

    def _run(self, cmd: list[str], target: Path, message: str) -> None:
        try:
            code, log = run_ffmpeg(cmd, self.timeout_s)
        except MediaToolError as exc:
            raise AudioError(f"{message}: {exc}") from exc
        if code != 0 or not target.is_file() or target.stat().st_size == 0:
            raise AudioError(f"{message}: {tail(log)}")
