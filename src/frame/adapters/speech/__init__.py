"""음성 인식 어댑터 (ADR-0026). OpenAI 호환 구현은 조립할 때 import 한다."""

from frame.adapters.speech.fake import FakeSpeechRecognizer

__all__ = ["FakeSpeechRecognizer"]
