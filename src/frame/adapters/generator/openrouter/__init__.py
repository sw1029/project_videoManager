"""OpenRouter 영상 API 로 장면 클립을 만드는 생성기 (FRAME_GENERATOR=external_api, ADR-0011).

맡는 개념
    제공자 하나(OpenRouter)의 구현이다. 장면 하나를 제출하고, 끝날 때까지 기다려 내려받고, 한 규격으로 맞춰 저장한다.
    과금은 제출할 때 일어나므로 제출한 작업을 원장에 적어 두고, 프로세스가 죽어도 같은 작업을 이어 받는다 (ADR-0013).
    수정 버전에서는 부모 클립을 편집해서 만들 수 있다 (ADR-0016).

밖에서 쓰는 이름
    OpenRouterVideoGenerator          생성기
    resolve_openrouter_key · mask_key 키 찾기 (명시 인자 -> 환경 변수 -> 키 파일)와 표시
    output_size                       해상도 표기와 화면비에서 클립 크기 (조립기도 쓴다)
    compile_video_prompt · compile_edit_prompt    프롬프트 (새로 생성 · 부모 클립 편집)
    CONSISTENCY_LINE · EDIT_KEEP_LINE             프롬프트의 문구 상수
    VideoModelInfo                    모델 정보 (받는 길이 · 해상도 · 단가)
    EDIT_OFF · EDIT_PREVIOUS_JOB · EDIT_VIDEO_REFERENCE · EDIT_MODES    부모 클립 편집 방식 (FRAME_VIDEO_EDIT_MODE)
    KEY_ENV · LEDGER_TTL_SECONDS      키의 환경 변수 이름, 원장 항목을 이어 받는 기한
    (모델 · 주소 · 해상도 · 시간 제한의 기본값은 여기 없다. 설정 config/settings.py 에만 있다 - ADR-0032)

모듈 지도
    generator         생성기 클래스: 제출 · 대기 · 내려받기 · 원장으로 이어 받기 · 부모 클립 편집 · 미리보기 · 저장
                      (아직 한 클래스다. 일별로 나누는 것이 다음 단계다 - 감사 M-01)
    _prompts          프롬프트 (새로 생성 · 부모 클립 편집)
    _model_catalog    모델 정보
    _api_key          키 찾기 · 표시
    _output_size      출력 크기
"""

from ._api_key import KEY_ENV, mask_key, resolve_openrouter_key
from ._model_catalog import VideoModelInfo
from ._output_size import output_size
from ._prompts import (
    CONSISTENCY_LINE,
    EDIT_KEEP_LINE,
    compile_edit_prompt,
    compile_video_prompt,
)
from .generator import (
    EDIT_MODES,
    EDIT_OFF,
    EDIT_PREVIOUS_JOB,
    EDIT_VIDEO_REFERENCE,
    LEDGER_TTL_SECONDS,
    OpenRouterVideoGenerator,
)
from .generator import (
    _checked_base_url as _checked_base_url,  # 작업 중인 브랜치(theme-series)가 이 이름을 가져다 쓴다
)

__all__ = [
    "CONSISTENCY_LINE",
    "EDIT_KEEP_LINE",
    "EDIT_MODES",
    "EDIT_OFF",
    "EDIT_PREVIOUS_JOB",
    "EDIT_VIDEO_REFERENCE",
    "KEY_ENV",
    "LEDGER_TTL_SECONDS",
    "OpenRouterVideoGenerator",
    "VideoModelInfo",
    "compile_edit_prompt",
    "compile_video_prompt",
    "mask_key",
    "output_size",
    "resolve_openrouter_key",
]
