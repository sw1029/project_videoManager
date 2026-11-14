"""음성 인식 어댑터 테스트의 도우미."""

from __future__ import annotations

from frame.domain.series.source_analysis import SpeechSegment, Transcript


def dump_request_fields(body: bytes) -> dict[str, list[str]]:
    """multipart 본문에서 글 필드만 꺼낸다 (테스트·기록용. 파일 내용은 꺼내지 않는다)."""
    fields: dict[str, list[str]] = {}
    for part in body.split(b"\r\n--"):
        head, _, value = part.partition(b"\r\n\r\n")
        if b"filename=" in head or b'name="' not in head:
            continue
        name = head.split(b'name="', 1)[1].split(b'"', 1)[0].decode("utf-8")
        fields.setdefault(name, []).append(value.rstrip(b"\r\n").decode("utf-8"))
    return fields


# 계약 테스트가 함께 쓰는 받아쓰기 한 토막 (두 문장, 한국어)
SAID = Transcript(
    "ko",
    (
        SpeechSegment(300, 1800, "안녕하세요, 오늘의 메뉴예요."),
        SpeechSegment(2300, 3700, "바닐라 라떼입니다."),
    ),
)
