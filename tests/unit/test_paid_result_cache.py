"""같은 입력의 유료 호출은 한 번 (감사 BTS-12, ADR-0013 의 원칙).

음성 인식과 화면 글자 읽기의 결과를 산출물 저장소에 남겨, 같은 소리·같은 화면을 다시 물어도 과금하지 않는다.
같은 것을 두 요청이 동시에 물어도 한 번이다: 뒤에 온 쪽은 앞선 쪽이 끝나기를 기다렸다가 남긴 결과를 쓴다.
"""

from __future__ import annotations

import threading

import pytest

from frame.adapters.series.overlay.caching_reader import CachingScreenTextReader
from frame.adapters.speech.caching import CachingSpeechRecognizer
from frame.adapters.storage import InMemoryArtifactStore
from frame.application.errors import StorageError
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


def test_transcripts_kept_in_an_older_shape_are_not_read_back():
    """남기는 받아쓰기의 형식이 바뀌면 (구간에 신뢰도가 더해졌다) 옛 캐시를 쓰지 않는다 (감사 BTS-D10)."""
    from frame.adapters.speech import caching

    recognizer, inner, store = _speech()
    recognizer.transcribe(AUDIO)
    original = caching.TRANSCRIPT_FORMAT
    try:
        caching.TRANSCRIPT_FORMAT = original + 1
        newer, newer_inner, _ = _speech(store=store)
        assert newer.transcribe(AUDIO) == HEARD and len(newer_inner.calls) == 1
    finally:
        caching.TRANSCRIPT_FORMAT = original


# ---- 같은 것을 두 요청이 동시에 물으면 (감사 BTS-12 의 남은 것) ----
WAIT = 5.0  # 테스트가 멈춰 버리지 않게 하는 안전 상한
SETTLE = (
    0.2  # 뒤에 온 요청이 안쪽 어댑터에 닿을 만한 시간. 기다리게 했다면 이 시간이 지나도 닿지 않는다
)


class _Overlap:
    """안쪽 어댑터의 호출을 붙잡아 두 요청이 겹치게 한다: 첫 호출이 안에 있는 동안 둘째 요청을 보낸다."""

    def __init__(self) -> None:
        self.first_inside = threading.Event()
        self.second_inside = threading.Event()
        self.leave = threading.Event()

    def entered(self, calls: int) -> None:
        (self.first_inside if calls == 1 else self.second_inside).set()
        self.leave.wait(WAIT)

    def run(self, ask) -> list:
        """같은 요청을 두 스레드로 겹쳐 보내고 두 답을 돌려준다."""
        answers: list = []
        threads = [threading.Thread(target=lambda: answers.append(ask())) for _ in range(2)]
        threads[0].start()
        assert self.first_inside.wait(WAIT)
        threads[1].start()
        self.second_inside.wait(SETTLE)
        self.leave.set()
        for thread in threads:
            thread.join(WAIT)
        return answers


def test_the_same_audio_asked_twice_at_once_is_recognised_once():
    """전에는 둘 다 저장된 결과가 없다고 보고 둘 다 인식기를 불렀다 (두 번 과금)."""
    overlap = _Overlap()

    class _Slow(_CountingRecognizer):
        def transcribe(self, audio, *, language=None, hint=""):
            answer = super().transcribe(audio, language=language, hint=hint)
            overlap.entered(len(self.calls))
            return answer

    recognizer, inner, _ = _speech(_Slow())

    answers = overlap.run(lambda: recognizer.transcribe(AUDIO, language="ko"))

    assert answers == [HEARD, HEARD]
    assert len(inner.calls) == 1


def test_recognition_of_another_audio_is_not_kept_waiting():
    """기다리는 것은 같은 소리의 요청뿐이다. 다른 소리는 앞선 인식이 끝나기를 기다리지 않는다."""
    inside, leave = threading.Event(), threading.Event()
    other = SpeechAudio(b"RIFF-two", "audio/wav", 4000)

    class _SlowOnTheFirst(_CountingRecognizer):
        def transcribe(self, audio, *, language=None, hint=""):
            answer = super().transcribe(audio, language=language, hint=hint)
            if audio.data == AUDIO.data:
                inside.set()
                leave.wait(WAIT)
            return answer

    recognizer, inner, _ = _speech(_SlowOnTheFirst())
    held = threading.Thread(target=lambda: recognizer.transcribe(AUDIO))
    free = threading.Thread(target=lambda: recognizer.transcribe(other))
    held.start()
    try:
        assert inside.wait(WAIT)
        free.start()
        free.join(WAIT / 2)
        assert not free.is_alive()  # 앞선 인식이 아직 안에 있는데도 끝났다
    finally:
        leave.set()
        held.join(WAIT)
        free.join(WAIT)

    assert len(inner.calls) == 2


def test_a_failed_recognition_lets_the_one_that_waited_ask_again():
    """앞선 쪽이 실패하면 남긴 결과가 없다. 기다린 쪽은 그 실패를 물려받지 않고 스스로 묻는다."""
    overlap = _Overlap()

    class _FailsFirst(_CountingRecognizer):
        def transcribe(self, audio, *, language=None, hint=""):
            self.calls.append((audio.data, language, hint))
            number = len(self.calls)  # 기다리는 동안 다른 호출이 들어와도 이 호출의 차례는 그대로다
            overlap.entered(number)
            if number == 1:
                raise SpeechRecognitionError("한도 초과")
            return HEARD

    recognizer, inner, _ = _speech(_FailsFirst())

    def ask():
        try:
            return recognizer.transcribe(AUDIO)
        except SpeechRecognitionError as exc:
            return exc

    outcomes = overlap.run(ask)

    assert len(inner.calls) == 2
    assert sorted(type(outcome).__name__ for outcome in outcomes) == [
        "SpeechRecognitionError",
        "Transcript",
    ]


def test_the_same_screen_asked_twice_at_once_is_read_once():
    overlap = _Overlap()

    class _Slow(_CountingReader):
        def read(self, clip, at_ms, regions):
            texts = super().read(clip, at_ms, regions)
            overlap.entered(len(self.calls))
            return texts

    reader, inner, _ = _reader(_Slow())

    answers = overlap.run(lambda: reader.read(b"clip-a", 2000, (PRICE, TITLE)))

    assert answers == [("문구 1", "문구 2"), ("문구 1", "문구 2")]
    assert len(inner.calls) == 1


class _FullStore(InMemoryArtifactStore):
    """쓰기가 실패하는 저장소 (디스크가 찼거나 권한이 없다)."""

    def __init__(self, error: Exception) -> None:
        super().__init__()
        self._error = error

    def put(self, key, data, content_type="application/octet-stream"):
        raise self._error


@pytest.mark.parametrize(
    "error", [StorageError("디스크가 찼습니다"), OSError(28, "No space left on device")]
)
def test_result_is_returned_even_when_it_cannot_be_kept(error):
    """남기지 못해도 방금 과금한 결과는 돌려준다 (감사 BTS-D04·D35).

    이 브랜치의 로컬 파일 저장소는 쓰기 오류를 OSError 그대로 낸다. 그것이 포트 밖으로 나가면 과금한 결과가
    예외와 함께 사라지고, 다시 부르면 또 과금한다.
    """
    recognizer, inner, _ = _speech(store=_FullStore(error))
    assert recognizer.transcribe(AUDIO) == HEARD and len(inner.calls) == 1

    reader, read_inner, _ = _reader(store=_FullStore(error))
    assert reader.read(b"clip-a", 2000, (PRICE,)) == ("문구 1",) and len(read_inner.calls) == 1


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
