"""음성 인식 어댑터 테스트의 도우미."""

from __future__ import annotations


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
