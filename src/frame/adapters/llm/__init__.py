"""LLM 클라이언트 어댑터. OpenAI 클라이언트는 openai 패키지가 있을 때만 쓰므로 모듈 경로로 직접 import 한다.

from frame.adapters.llm.openai_client import OpenAIJsonClient
"""

from .replay import ReplayLLMClient

__all__ = ["ReplayLLMClient"]
