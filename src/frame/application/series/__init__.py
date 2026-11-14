"""테마·시리즈 유스케이스 (US-06·US-01, ADR-0025~0027).

외부 숏폼에서 테마를 뽑고(ExtractTheme), 그 테마로 편을 만들고(CreateEpisode, AdoptAnchors),
사용자가 녹음한 음성을 붙이고(AttachNarration), 화면 문구를 얹어 완성본을 만들고(RenderEpisode),
화면 정보가 어긋난 곳을 찾아(CheckEpisodeScreen) 그 부분만 고친다(CorrectEpisodeScreen).
영상 생성·검수는 지금까지의 프로젝트·버전 흐름을 그대로 쓴다.
"""

from .attach_narration import AttachNarration, AttachNarrationCommand, RecordingInput
from .correct_screen import (
    CorrectEpisodeScreen,
    CorrectionStep,
    CorrectScreenCommand,
    CorrectScreenResult,
    PatchRegion,
    StepStatus,
)
from .create_episode import (
    AdoptAnchors,
    AdoptAnchorsCommand,
    AdoptAnchorsResult,
    CreateEpisode,
    CreateEpisodeCommand,
)
from .extract_theme import ExtractTheme, ExtractThemeCommand, ExtractThemeResult, NotShortform
from .render_episode import RenderEpisode, RenderEpisodeResult
from .screen_check import CheckEpisodeScreen, ScreenReport, expected_overlay_texts

__all__ = [
    "AdoptAnchors",
    "AdoptAnchorsCommand",
    "AdoptAnchorsResult",
    "AttachNarration",
    "AttachNarrationCommand",
    "CheckEpisodeScreen",
    "CorrectEpisodeScreen",
    "CorrectScreenCommand",
    "CorrectScreenResult",
    "CorrectionStep",
    "CreateEpisode",
    "CreateEpisodeCommand",
    "ExtractTheme",
    "ExtractThemeCommand",
    "ExtractThemeResult",
    "NotShortform",
    "PatchRegion",
    "RecordingInput",
    "RenderEpisode",
    "RenderEpisodeResult",
    "ScreenReport",
    "StepStatus",
    "expected_overlay_texts",
]
