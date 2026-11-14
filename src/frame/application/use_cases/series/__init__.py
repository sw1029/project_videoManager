"""테마·시리즈 유스케이스 (US-06·US-01, ADR-0025~0027).

외부 숏폼에서 테마를 뽑고(ExtractTheme), 그 테마로 편을 만들고(CreateEpisode, AdoptAnchors),
사용자가 녹음한 음성을 붙이고(AttachNarration), 화면 문구를 얹어 완성본을 만들고(RenderEpisode),
화면 정보가 어긋난 곳을 찾아(CheckEpisodeScreen) 그 부분만 고친다(CorrectEpisodeScreen).
편의 조건을 사람이 정해 고칠 때는 ReviseEpisode 를 쓴다 (그 조건이 걸린 장면만 다시 만든다).
수정 버전은 끝난 뒤에만 편의 기준 버전이 된다 (pending_revision: 만들고 있는 버전의 결말을 편에 반영한다).
영상 생성·검수는 지금까지의 프로젝트·버전 흐름을 그대로 쓴다.
"""

from frame.application.use_cases.series.adopt_anchors import (
    AdoptAnchors,
    AdoptAnchorsCommand,
    AdoptAnchorsResult,
)
from frame.application.use_cases.series.attach_narration import (
    AttachNarration,
    AttachNarrationCommand,
    RecordingInput,
)
from frame.application.use_cases.series.check_episode_screen import (
    CheckEpisodeScreen,
    ScreenReport,
)
from frame.application.use_cases.series.correct_episode_screen import (
    CorrectEpisodeScreen,
    CorrectScreenCommand,
    CorrectScreenResult,
)
from frame.application.use_cases.series.create_episode import CreateEpisode, CreateEpisodeCommand
from frame.application.use_cases.series.extract_theme import (
    ExtractTheme,
    ExtractThemeCommand,
    ExtractThemeResult,
    NotShortform,
)
from frame.application.use_cases.series.render_episode import RenderEpisode, RenderEpisodeResult
from frame.application.use_cases.series.revise_episode import ReviseEpisode, ReviseEpisodeCommand
from frame.application.use_cases.series.settle_episode import SettleEpisode, SettleEpisodeCommand

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
    "CreateEpisode",
    "CreateEpisodeCommand",
    "ExtractTheme",
    "ExtractThemeCommand",
    "ExtractThemeResult",
    "NotShortform",
    "RecordingInput",
    "RenderEpisode",
    "RenderEpisodeResult",
    "ReviseEpisode",
    "ReviseEpisodeCommand",
    "ScreenReport",
    "SettleEpisode",
    "SettleEpisodeCommand",
]
