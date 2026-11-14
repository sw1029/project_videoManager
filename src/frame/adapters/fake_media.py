"""가짜 영상·음성 형식 (테스트·오프라인 공용). 실제 미디어 대신 '무엇이 들어 있는지' 를 적은 JSON 이다.

가짜 분석기·인식기·소리 편집기·문구 합성기가 함께 읽는다. 그래서 실제 모델·ffmpeg 없이
추출 -> 기획 -> 녹음 붙이기 -> 합성을 끝까지 돌릴 수 있다.
- video: 겉모습(크기·길이·fps)과 컷 시각
- audio: 길이와 말소리(구간·낱말 시각이 붙은 글)
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from frame.domain.speech import Transcript

FAKE_MEDIA_KEY = "fake_media"
FAKE_MEDIA_TYPE = "application/json"


def fake_video(
    *,
    width: int = 480,
    height: int = 854,
    duration_ms: int = 16000,
    fps: float = 30.0,
    cuts_ms: Sequence[int] = (4000, 8000, 12000),
    transcript: Transcript | None = None,
    silences: Sequence[tuple[int, int]] | None = None,
) -> bytes:
    """가짜 영상. transcript 를 주면 소리가 있는 영상이다.

    silences 를 주면 그 구간만 무음이다 (나머지는 말이든 음악이든 소리가 난다).
    주지 않으면 말소리가 없는 시간이 모두 무음이다.
    """
    media: dict[str, Any] = {
        "video": {
            "width": width,
            "height": height,
            "duration_ms": duration_ms,
            "fps": fps,
            "cuts_ms": list(cuts_ms),
        }
    }
    if transcript is not None:
        media["audio"] = {"duration_ms": duration_ms, "transcript": transcript.to_dict()}
        if silences is not None:
            media["audio"]["silences"] = [list(span) for span in silences]
    return encode_fake_media(media)


def fake_recording(transcript: Transcript, duration_ms: int | None = None) -> bytes:
    """가짜 녹음 (사용자가 녹음한 음성 파일의 대역). 길이를 주지 않으면 마지막 말이 끝나고 0.3초 뒤까지다."""
    end = max((s.end_ms for s in transcript.segments), default=0)
    audio = {"duration_ms": duration_ms or end + 300, "transcript": transcript.to_dict()}
    return encode_fake_media({"audio": audio})


def encode_fake_media(media: Mapping[str, Any]) -> bytes:
    return json.dumps({FAKE_MEDIA_KEY: media}, ensure_ascii=False, sort_keys=True).encode("utf-8")


def decode_fake_media(data: bytes) -> dict[str, Any] | None:
    """가짜 영상·음성이면 그 내용, 아니면 None."""
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    media = document.get(FAKE_MEDIA_KEY) if isinstance(document, dict) else None
    return media if isinstance(media, dict) else None
