"""영상 분석 어댑터 (ADR-0025): 겉모습·컷·말소리·테마 추출. 실제 구현은 레지스트리가 필요할 때 import 한다."""

from .fake import FakeMediaProber, FakeShotDetector, FakeSpeechRecognizer, FakeThemeExtractor

__all__ = ["FakeMediaProber", "FakeShotDetector", "FakeSpeechRecognizer", "FakeThemeExtractor"]
