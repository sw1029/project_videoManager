"""테마 추출 어댑터 (ADR-0025). 프레임을 읽는 구현은 조립할 때 import 한다."""

from frame.adapters.series.theme_extraction.fake import FakeThemeExtractor

__all__ = ["FakeThemeExtractor"]
