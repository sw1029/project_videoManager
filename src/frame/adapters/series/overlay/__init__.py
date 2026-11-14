"""후반 합성 어댑터 (ADR-0027). ffmpeg·VLM 구현은 레지스트리가 필요할 때 import 한다."""

from frame.adapters.series.overlay.fake import FakeOverlayRenderer, FakeScreenTextReader

__all__ = ["FakeOverlayRenderer", "FakeScreenTextReader"]
