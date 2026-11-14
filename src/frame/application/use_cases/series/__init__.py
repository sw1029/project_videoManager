"""시리즈 유스케이스 (US-06·US-01, ADR-0025~0027).

맡는 개념: 숏폼에서 테마를 뽑고, 그 테마로 편을 만들고, 사람이 녹음한 음성을 붙이고, 화면 문구를 얹고,
화면 정보가 어긋난 곳을 그 부분만 고친다. 영상 생성·검수는 지금까지의 프로젝트·버전 흐름을 그대로 쓴다
(편은 프로젝트 하나에 서고, 편의 수정은 수정 버전이다). 유스케이스 하나가 모듈 하나다.

밖에서 쓰는 이름: 유스케이스와 그 명령·결과 타입 (__all__). 조립은 config/theme_wiring.py 가 한다.

모듈 지도 (쓰이는 순서)
- extract_theme           ExtractTheme: 영상 -> 숏폼 판정 -> 분석 -> 테마·원본 IR 저장. 같은 영상은 다시 뽑지 않는다
- create_episode          CreateEpisode: 테마 + 편 요청 -> 편 계획 -> 프로젝트와 DRAFT 버전
- adopt_anchors           AdoptAnchors: 완성된 편의 기준 이미지를 테마의 고정 출연에 붙인다 (편 사이 정체성)
- attach_narration        AttachNarration: 녹음을 대본과 맞춰 장면에 놓는다
- render_episode          RenderEpisode: 화면 문구를 얹고 소리를 붙여 완성본을 만든다 (바뀐 장면만 다시 합성)
- check_episode_screen    CheckEpisodeScreen: 화면·소리가 계획과 어긋난 곳을 찾는다 (ScreenReport)
- correct_episode_screen  CorrectEpisodeScreen: 어긋난 곳을 고친다. 문구 수정은 무료, 클립 수정은 짚은 것만
- revise_episode          ReviseEpisode: 편의 조건을 사람이 정해 고친다 (그 조건이 걸린 장면만 다시 만든다)
- settle_episode          SettleEpisode: 만들고 있는 수정 버전의 결말을 편에 반영한다 · 만들다 만 초안 버리기
- correct_source_script   CorrectSourceScript: 원본에서 뽑은 대본의 줄을 고치고, 그 줄에 걸린 것만 다시 맞춘다
- revise_episode_script   ReviseEpisodeScript: 편의 녹음 대본만 고친다 (영상은 그대로, 바뀐 줄만 다시 녹음)
- _narration_track        (안쪽) 줄마다의 맞춤으로 편 길이의 소리 트랙을 만든다
- _pending_revision       (안쪽) 수정 버전은 끝난 뒤에만 기준 버전이 된다: settle_pending · require_episode

돈이 드는 쪽은 기본이 아니다: 수정 버전의 생성 접수는 generate=True 를 줘야 하고,
클립 수정은 issue_ids 로 짚은 것만 한다.
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
from frame.application.use_cases.series.correct_source_script import (
    CorrectSourceScript,
    CorrectSourceScriptCommand,
    CorrectSourceScriptResult,
    LineCorrection,
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
from frame.application.use_cases.series.revise_episode_script import (
    ReviseEpisodeScript,
    ReviseEpisodeScriptCommand,
    ReviseEpisodeScriptResult,
)
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
    "CorrectSourceScript",
    "CorrectSourceScriptCommand",
    "CorrectSourceScriptResult",
    "CreateEpisode",
    "CreateEpisodeCommand",
    "ExtractTheme",
    "ExtractThemeCommand",
    "ExtractThemeResult",
    "LineCorrection",
    "NotShortform",
    "RecordingInput",
    "RenderEpisode",
    "RenderEpisodeResult",
    "ReviseEpisode",
    "ReviseEpisodeCommand",
    "ReviseEpisodeScript",
    "ReviseEpisodeScriptCommand",
    "ReviseEpisodeScriptResult",
    "ScreenReport",
    "SettleEpisode",
    "SettleEpisodeCommand",
]
