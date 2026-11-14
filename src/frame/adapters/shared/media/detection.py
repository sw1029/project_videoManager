"""영상 · 소리를 끝까지 읽어 찾는 것: 컷의 시각(detect_cuts)과 무음 구간(detect_silences).

영상 분석 · 소리 편집 어댑터가 쓴다 (ADR-0025). ffprobe 없이 ffmpeg 하나로 읽는다. 실패는 VideoToolError 다.

실행과 출력의 끝(tail)은 공용 실행기(.ffmpeg)의 것을 쓴다. 이 모듈의 오류 문구는 어댑터의 포트 오류에 실려
밖으로 나가므로, 실행 파일의 경로와 임시 폴더가 문구에 실리지 않아야 한다 (그 규칙이 실행기 한 곳에 있다).
"""

from __future__ import annotations

import re
from pathlib import Path

from .ffmpeg import WORK_TIMEOUT_S, VideoToolError, run_reading, tail

__all__ = ["detect_cuts", "detect_silences"]

_PTS_TIME = re.compile(r"Parsed_showinfo.*?pts_time:\s*([0-9.]+)")
_SILENCE_START = re.compile(r"silence_start:\s*(-?[0-9.]+)")
_SILENCE_END = re.compile(r"silence_end:\s*([0-9.]+)")


def detect_cuts(
    ffmpeg: str, path: str | Path, *, threshold: float, timeout_s: float = WORK_TIMEOUT_S
) -> list[int]:
    """화면이 앞 프레임과 크게 달라지는 시각(ms). threshold 는 ffmpeg 의 scene 점수(0..1) 하한이다.

    threshold 의 기본값은 설정(FRAME_SHOT_THRESHOLD)에만 있다. 여기에 다시 적지 않는다.
    """
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-i", str(path), "-an"]
    cmd += ["-vf", f"select='gt(scene,{threshold:g})',showinfo", "-f", "null", "-"]
    code, log = run_reading(cmd, timeout_s)
    if code != 0:
        raise VideoToolError(f"컷을 찾지 못했습니다: {tail(log)}")
    return [round(float(value) * 1000) for value in _PTS_TIME.findall(log)]


def detect_silences(
    ffmpeg: str,
    path: str | Path,
    *,
    noise_db: float = -45.0,
    min_ms: int = 300,
    timeout_s: float = WORK_TIMEOUT_S,
) -> list[tuple[int, int | None]]:
    """소리가 noise_db 보다 작은 구간 (시작, 끝). 파일 끝까지 조용하면 끝이 None 이다."""
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-i", str(path), "-vn"]
    cmd += ["-af", f"silencedetect=noise={noise_db:g}dB:d={min_ms / 1000:g}", "-f", "null", "-"]
    code, log = run_reading(cmd, timeout_s)
    if code != 0:
        raise VideoToolError(f"조용한 구간을 찾지 못했습니다: {tail(log)}")
    starts = [max(0, round(float(v) * 1000)) for v in _SILENCE_START.findall(log)]
    ends = [round(float(v) * 1000) for v in _SILENCE_END.findall(log)]
    return [(start, ends[i] if i < len(ends) else None) for i, start in enumerate(starts)]
