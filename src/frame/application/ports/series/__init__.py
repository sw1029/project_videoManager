"""시리즈 기능의 포트 (ADR-0025~0027). 구현은 adapters/series/ 에 있다.

맡는 개념: 테마 추출 · 편 기획 · 녹음 붙이기 · 화면 문구 합성 · 테마와 편의 저장이 바깥 세계에 요구하는 것.
음성 인식은 이 기능 밖에서도 쓰는 개념이라 ports/speech.py 에 따로 있다.

밖에서 쓰는 이름: 이 입구는 아무것도 내보내지 않는다. 아래 모듈을 직접 import 한다.

모듈 지도
- source_analysis   MediaProber (영상의 겉모습) · ShotDetector (컷 찾기). 모델을 쓰지 않는 결정적인 단계
- theme_extraction  ThemeExtractor: 분석 결과 + 영상 -> 테마 · 원본 IR · 화면 문구
- planning          SeriesPlanner: 테마 + 편 요청 -> 이번 편의 내용 (편마다 다른 것만)
- audio             AudioEditor: 영상에서 소리 뽑기 · 녹음을 잘라 장면에 놓기 · 영상에 트랙 붙이기
- overlay           OverlayRenderer (화면 문구 얹기) · ScreenTextReader (화면의 글자 읽기)
- repositories      ThemeRepository · EpisodeRepository (편의 키를 쥐는 lock 포함)

오류: 포트마다 AdapterError 아래의 오류 종류가 있다 (VideoAnalysisError · ThemeExtractionError · SeriesPlanningError ·
AudioError · CompositionError). LLM 을 쓰는 구현에서 LLM 호출의 오류(LLMTimeout 등)는 바꾸지 않고 그대로 올린다.
"""

__all__: list[str] = []
