"""참조 이미지 업로드 테스트용 작은 이미지 바이트 (표준 라이브러리만 쓴다)."""

from __future__ import annotations

import struct
import zlib


def png_bytes(width: int = 1, height: int = 1) -> bytes:
    """실제로 열리는 RGB PNG (검은 화면)."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x00\x00\x00" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def jpeg_bytes() -> bytes:
    """JPEG 시작 표지(SOI + APP0 JFIF)로 시작하는 바이트. 형식 판별만 보는 테스트용."""
    return b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + b"\x00" * 16


def webp_bytes() -> bytes:
    """RIFF....WEBP 머리로 시작하는 바이트. 형식 판별만 보는 테스트용."""
    payload = b"VP8L" + struct.pack("<I", 5) + b"\x2f\x00\x00\x00\x00"
    return b"RIFF" + struct.pack("<I", 4 + len(payload)) + b"WEBP" + payload


GIF_BYTES = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00"
)
