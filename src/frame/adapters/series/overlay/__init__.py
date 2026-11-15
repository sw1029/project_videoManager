"""후반 합성 어댑터 (포트 ports/series/overlay.py, ADR-0027).

맡는 개념: 화면 문구를 클립 위에 얹는 일(OverlayRenderer)과,
완성본의 그 자리에서 글자를 읽어 확인하는 일(ScreenTextReader).
밖에서 쓰는 이름: FakeOverlayRenderer · FakeScreenTextReader. ffmpeg·VLM 구현은 조립이 고를 때 import 한다.

모듈 지도
- fake             FakeOverlayRenderer · FakeScreenTextReader: 얹은 문구를 기록한 가짜 합성본을 만들고 읽는다
- ffmpeg_renderer  FfmpegOverlayRenderer: drawbox·drawtext 로 얹는다 (글꼴 찾기 · 영역에 맞는 글자 크기)
- vlm_reader       VlmScreenTextReader: 프레임의 그 영역을 잘라 모델에 글자를 묻는다
- caching_reader   CachingScreenTextReader: 같은 클립·시각·영역이면 다시 묻지 않는다 (과금은 한 번)
"""

from frame.adapters.series.overlay.fake import FakeOverlayRenderer, FakeScreenTextReader

__all__ = ["FakeOverlayRenderer", "FakeScreenTextReader"]
