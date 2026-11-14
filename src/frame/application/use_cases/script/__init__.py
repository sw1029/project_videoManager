"""대본 유스케이스 (ADR-0028): 영상·음성 파일의 소리에서 대본을 뽑는다.

맡는 개념: 소리 읽기(SoundReader)와 음성 인식(SpeechRecognizer) 두 포트로 받아쓰기를 얻고, 도메인(domain/script)의
규칙으로 줄 단위의 대본을 만든다. 음성 인식처럼 시리즈 기능 밖에서도 쓰는 개념이라 use_cases/series 밖에 둔다.
뽑은 대본을 테마의 근거로 쓰는 일과 대본을 고치는 일은 시리즈 유스케이스가 한다.

밖에서 쓰는 이름: ExtractScript · ExtractScriptCommand · ExtractScriptResult · listen · Heard · ListeningPolicy

모듈 지도
- extract_script  ExtractScript: 소리 읽기 -> 받아쓰기 -> 줄 나누기·장면 배정·종류
- listening       listen: 받아 적기 전후에 소리를 본다 (무음뿐이면 부르지 않는다 · 줄이 없는 소리는 다시 받아 적는다).
                  테마 추출(use_cases/series/extract_theme)도 이것으로 받아 적는다
"""

from frame.application.use_cases.script.extract_script import (
    ExtractScript,
    ExtractScriptCommand,
    ExtractScriptResult,
)
from frame.application.use_cases.script.listening import (
    DEFAULT_LISTENING_POLICY,
    Heard,
    ListeningPolicy,
    listen,
)

__all__ = [
    "DEFAULT_LISTENING_POLICY",
    "ExtractScript",
    "ExtractScriptCommand",
    "ExtractScriptResult",
    "Heard",
    "ListeningPolicy",
    "listen",
]
