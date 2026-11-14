"""원본 영상 분석 어댑터 (ADR-0025). ffmpeg 구현은 조립할 때 import 한다."""

from frame.adapters.series.source_analysis.fake import FakeMediaProber, FakeShotDetector

__all__ = ["FakeMediaProber", "FakeShotDetector"]
