"""소리 편집 어댑터 (ADR-0026). ffmpeg 구현은 레지스트리가 필요할 때 import 한다."""

from .fake import FakeAudioEditor

__all__ = ["FakeAudioEditor"]
