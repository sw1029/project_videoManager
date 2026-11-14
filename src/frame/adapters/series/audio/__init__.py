"""소리 편집 어댑터 (포트 ports/series/audio.py, ADR-0026).

맡는 개념: 영상에서 소리 뽑기, 무음 구간 재기, 녹음을 잘라 장면에 놓은 트랙 만들기, 영상에 트랙 붙이기.
소리 읽기(ports/sound.py 의 SoundReader)도 여기서 구현한다 — 대본 추출이 함께 쓴다.
말소리를 만들지는 않는다 (TTS 없음).
밖에서 쓰는 이름: FakeAudioEditor. ffmpeg 구현은 조립이 고를 때 import 한다.

모듈 지도
- fake    FakeAudioEditor: 가짜 영상·녹음(adapters/fake_media.py)에 적힌 말소리를 잘라 옮긴다
- ffmpeg  FfmpegAudioEditor: 무음 구간을 채워 장면 길이에 맞춘 트랙을 만들고 영상에 붙인다
"""

from frame.adapters.series.audio.fake import FakeAudioEditor

__all__ = ["FakeAudioEditor"]
