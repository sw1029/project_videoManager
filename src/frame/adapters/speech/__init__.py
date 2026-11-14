"""음성 인식 어댑터 (포트 ports/speech.py, ADR-0025·0026): 말소리 -> 낱말 시각이 있는 받아쓰기.

맡는 개념: 테마 추출(원본의 말소리)과 녹음 붙이기(사람의 녹음)가 함께 쓰는 음성 인식.
시리즈 기능 밖에서도 쓸 수 있는 개념이라 adapters/series/ 밖에 둔다. 말소리를 만들지는 않는다 (TTS 없음).
밖에서 쓰는 이름: FakeSpeechRecognizer. 실제 구현은 조립이 고를 때 import 한다.

모듈 지도
- fake     FakeSpeechRecognizer: 가짜 음성(adapters/fake_media.py)에 적힌 말소리를 그대로 돌려준다
- openai   OpenAISpeechRecognizer: OpenAI 호환 /audio/transcriptions. 키는 Authorization 헤더에만 싣는다
- faster_whisper  FasterWhisperRecognizer: 이 컴퓨터에서 받아 적는다 (과금 없음, 말 구간 검출 포함. 선택 설치)
- caching  CachingSpeechRecognizer: 같은 소리·같은 설정이면 다시 부르지 않는다 (과금은 한 번)
"""

from frame.adapters.speech.fake import FakeSpeechRecognizer

__all__ = ["FakeSpeechRecognizer"]
