"""같은 입력의 유료 호출은 한 번 (감사 BTS-12, ADR-0013 의 원칙).

음성 인식과 화면 글자 읽기의 결과를 산출물 저장소에 남겨, 같은 소리·같은 화면을 다시 물어도 과금하지 않는다.
"""

from __future__ import annotations

import pytest

from frame.adapters.series.overlay.caching_reader import CachingScreenTextReader
from frame.adapters.speech.caching import CachingSpeechRecognizer
from frame.adapters.storage import InMemoryArtifactStore
from frame.application.ports.series.overlay import ScreenTextReader
from frame.application.ports.speech import SpeechAudio, SpeechRecognitionError, SpeechRecognizer
from frame.domain.screen import Region
from frame.domain.speech import SpeechSegment, Transcript, Word

AUDIO = SpeechAudio(b"RIFF-one", "audio/wav", 4000)
HEARD = Transcript(
    "ko", (SpeechSegment(300, 2400, "안녕하세요 오늘의 메뉴예요", (Word(300, 900, "안녕하세요"),)),)
)


class _CountingRecognizer(SpeechRecognizer):
    def __init__(self, *answers) -> None:
        self.answers = list(answers) or [HEARD]
        self.calls: list[tuple[bytes, str | None, str]] = []

    def transcribe(self, audio, *, language=None, hint=""):
        self.calls.append((audio.data, language, hint))
        answer = self.answers[min(len(self.calls), len(self.answers)) - 1]
        if isinstance(answer, Exception):
            raise answer
        return answer


def _speech(inner=None, store=None, profile="whisper-1@api"):
    inner = inner or _CountingRecognizer()
    store = store or InMemoryArtifactStore()
    return CachingSpeechRecognizer(inner, store, profile), inner, store


# ---- 음성 인식 ----
def test_same_audio_is_recognised_once():
    recognizer, inner, store = _speech()

    first = recognizer.transcribe(AUDIO, language="ko", hint="말차 라떼")
    again = recognizer.transcribe(AUDIO, language="ko", hint=" 말차 라떼 ")

    assert first == again == HEARD and len(inner.calls) == 1
    # 다른 조립(같은 저장소)도 그 결과를 쓴다: 다시 시작해도 과금하지 않는다
    other, other_inner, _ = _speech(store=store)
    assert other.transcribe(AUDIO, language="ko", hint="말차 라떼") == HEARD
    assert other_inner.calls == []
    assert recognizer.inner is inner


def test_anything_that_changes_the_answer_is_recognised_again():
    recognizer, inner, store = _speech()
    recognizer.transcribe(AUDIO, language="ko")

    recognizer.transcribe(SpeechAudio(b"RIFF-two", "audio/wav", 4000), language="ko")  # 다른 소리
    recognizer.transcribe(AUDIO, language="en")  # 다른 언어
    recognizer.transcribe(AUDIO, language="ko", hint="가격은 오천 원")  # 다른 힌트
    other_model, other_inner, _ = _speech(store=store, profile="another-model@api")
    other_model.transcribe(AUDIO, language="ko")  # 다른 모델

    assert len(inner.calls) == 4 and len(other_inner.calls) == 1


def test_failed_recognition_is_not_kept():
    inner = _CountingRecognizer(SpeechRecognitionError("한도 초과"), HEARD)
    recognizer, _, _ = _speech(inner)

    with pytest.raises(SpeechRecognitionError):
        recognizer.transcribe(AUDIO)
    assert recognizer.transcribe(AUDIO) == HEARD and len(inner.calls) == 2


def test_transcript_that_breaks_the_contract_is_not_kept():
    """계약을 어긴 받아쓰기(구간이 소리 길이를 넘는다)는 남기지 않는다 (감사 BTS-D61).

    남기면 다시 불러도 같은 받아쓰기가 나와 그 파일은 계속 실패한다. 결과는 그대로 돌려준다 (부른 쪽이 검사한다).
    """
    beyond = Transcript("ko", (SpeechSegment(300, 4300, "소리 길이를 넘는 구간"),))
    inner = _CountingRecognizer(beyond, HEARD)
    recognizer, _, _ = _speech(inner)

    assert recognizer.transcribe(AUDIO) == beyond
    assert recognizer.transcribe(AUDIO) == HEARD and len(inner.calls) == 2
    assert recognizer.transcribe(AUDIO) == HEARD and len(inner.calls) == 2  # 맞는 결과는 남긴다


def test_broken_or_missing_cache_falls_back_to_the_recognizer():
    recognizer, inner, store = _speech()
    recognizer.transcribe(AUDIO)
    for uri in list(store._blobs):
        store._blobs[uri] = (b"not json", "application/json")  # 잘린 문서

    assert recognizer.transcribe(AUDIO) == HEARD and len(inner.calls) == 2
    assert recognizer.transcribe(AUDIO) == HEARD and len(inner.calls) == 2  # 다시 써 두었다


# ---- 화면 글자 읽기 ----
class _CountingReader(ScreenTextReader):
    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[bytes, int, tuple[Region, ...]]] = []

    def read(self, clip, at_ms, regions):
        self.calls.append((clip, at_ms, tuple(regions)))
        if self.answers:
            return self.answers[min(len(self.calls), len(self.answers)) - 1]
        return tuple(f"문구 {n}" for n in range(1, len(regions) + 1))


PRICE = Region(0.1, 0.72, 0.8, 0.08)
TITLE = Region(0.1, 0.07, 0.8, 0.08)


def _reader(inner=None, store=None, profile="gpt@vlm"):
    inner = inner or _CountingReader()
    store = store or InMemoryArtifactStore()
    return CachingScreenTextReader(inner, store, profile), inner, store


def test_same_screen_is_read_once():
    reader, inner, store = _reader()

    first = reader.read(b"clip-a", 2000, (PRICE, TITLE))
    again = reader.read(b"clip-a", 2000, [PRICE, TITLE])

    assert first == again == ("문구 1", "문구 2") and len(inner.calls) == 1
    other, other_inner, _ = _reader(store=store)
    assert other.read(b"clip-a", 2000, (PRICE, TITLE)) == first and other_inner.calls == []
    assert reader.inner is inner


def test_another_clip_time_region_or_model_is_read_again():
    reader, inner, store = _reader()
    reader.read(b"clip-a", 2000, (PRICE,))

    reader.read(b"clip-b", 2000, (PRICE,))  # 문구를 고쳐 다시 합성한 장면
    reader.read(b"clip-a", 3000, (PRICE,))
    reader.read(b"clip-a", 2000, (TITLE,))
    other, other_inner, _ = _reader(store=store, profile="other@vlm")
    other.read(b"clip-a", 2000, (PRICE,))

    assert len(inner.calls) == 4 and len(other_inner.calls) == 1
    assert (
        reader.read(b"clip-a", 2000, ()) == () and len(inner.calls) == 4
    )  # 물을 것이 없으면 부르지 않는다


def test_reading_with_an_empty_answer_is_not_kept():
    """빈 답은 '글자 없음' 일 수도 '읽지 못함' 일 수도 있다. 읽지 못한 것을 굳히지 않도록 남기지 않는다."""
    inner = _CountingReader(("말차 라떼 5,000원", ""), ("말차 라떼 5,000원", "오늘의 메뉴"))
    reader, _, _ = _reader(inner)

    assert reader.read(b"clip-a", 2000, (PRICE, TITLE)) == ("말차 라떼 5,000원", "")
    assert reader.read(b"clip-a", 2000, (PRICE, TITLE)) == ("말차 라떼 5,000원", "오늘의 메뉴")
    assert reader.read(b"clip-a", 2000, (PRICE, TITLE)) == ("말차 라떼 5,000원", "오늘의 메뉴")
    assert len(inner.calls) == 2
