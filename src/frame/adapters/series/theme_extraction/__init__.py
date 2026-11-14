"""테마 추출 어댑터 (포트 ports/series/theme_extraction.py, ADR-0025).

맡는 개념: 분석 결과와 영상에서 테마 · 원본 IR · 화면 문구를 만든다.
밖에서 쓰는 이름: FakeThemeExtractor. 프레임을 읽는 구현(vlm.VlmThemeExtractor)은 조립이 고를 때 import 한다.

모듈 지도
- fake            FakeThemeExtractor: 관찰값만으로 가장 단순한 테마 (샷 -> 자리, '주인공' 하나)
- vlm             VlmThemeExtractor: 자리마다 프레임을 뽑아 모델에 한 번 묻고, 규칙에 걸리면 다시 묻는다
- _prompt         (안쪽) 모델에 주는 지시문과 그 판 번호 (PROMPT_VERSION)
- _schema         (안쪽) 응답의 JSON 스키마
- _answer_parts   (안쪽) 응답 조각을 IR 의 규칙에 맞게 읽는다: 자리 번호 · 객체 키 · 조건 · 요구조건
- _theme_builder  (안쪽) 응답 -> 테마 · 원본 IR · 화면 문구 (build_theme, 순수 함수)
- _text_locator   (안쪽) 얹은 문구의 영역을 문구마다 프레임 한 장으로 다시 묻는다 (locate_text)
"""

from frame.adapters.series.theme_extraction.fake import FakeThemeExtractor

__all__ = ["FakeThemeExtractor"]
