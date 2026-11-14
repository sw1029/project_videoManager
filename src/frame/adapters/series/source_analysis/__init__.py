"""원본 영상 분석 어댑터 (포트 ports/series/source_analysis.py, ADR-0025).

맡는 개념: 영상 한 편을 모델 없이 읽는 결정적인 단계 — 겉모습(크기·길이·fps·소리 유무·코덱)과 컷의 시각.
밖에서 쓰는 이름: FakeMediaProber · FakeShotDetector. ffmpeg 구현은 조립이 고를 때 import 한다.

모듈 지도
- fake    FakeMediaProber · FakeShotDetector: 가짜 영상(adapters/shared/media/fake_media.py)에 적힌 값을 그대로 돌려준다
- ffmpeg  FfmpegMediaProber (`ffmpeg -i` 의 출력, 영상 코덱 허용 목록) · FfmpegShotDetector (장면 점수)
"""

from frame.adapters.series.source_analysis.fake import FakeMediaProber, FakeShotDetector

__all__ = ["FakeMediaProber", "FakeShotDetector"]
