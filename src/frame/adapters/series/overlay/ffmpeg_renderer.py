"""ffmpeg drawtext 로 장면 클립에 화면 문구를 얹는다 (ADR-0027). 추가 설치 없이 글꼴 파일 하나만 있으면 된다.

- 문구마다: (띠가 있으면) drawbox 로 영역을 칠하고, drawtext 로 영역 가운데에 글자를 그린다.
  둘 다 그 문구의 시간에만 켠다 (enable). 그 밖의 시간·영역의 그림은 그대로이고 다시 인코딩만 된다.
- 글자는 textfile 로 넘긴다 (한글·쉼표·따옴표를 필터 문법으로 옮기다 깨지는 일이 없다).
  색 같은 필터에 직접 들어가는 값은 형식을 확인한다 (모델이 낸 문자열이 필터 문법을 깨뜨리지 못하게).
- 글자가 영역보다 넓으면 글자 크기를 줄여 맞춘다 (어림 폭: 한글·한자 1 em, 영문·숫자 0.55 em).
- 출력은 입력과 같은 크기·fps·프레임 수의 H.264(yuv420p) 무음 mp4 다. 조립기가 스트림 복사로 이을 수 있다.
- 한글이 없는 글꼴을 주면 글자가 빈 네모로 그려진다. 그것은 화면 문구 검수(ScreenTextReader)가 잡는다.
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NamedTuple

from frame.adapters.shared.media.ffmpeg import require_ffmpeg
from frame.adapters.shared.media.media_tools import MediaToolError, probe_media, run_ffmpeg, tail
from frame.application.errors import ConfigurationError
from frame.application.ports.series.overlay import CompositionError, OverlayRenderer
from frame.domain.series.episode import OverlayItem
from frame.domain.series.theme import color_name

_MIN_FONT_PX = 10
_FILL = 0.94  # 글자가 영역의 폭을 이만큼까지 쓴다

# 글꼴을 지정하지 않았을 때 찾는 한글 글꼴의 파일 이름 (앞의 것이 먼저, 굵은 것 먼저).
# 폴더는 적지 않는다: 설치 위치는 컴퓨터마다 다르므로 운영체제의 글꼴 폴더에서 찾는다 (system_font_dirs)
KOREAN_FONT_FILES: tuple[str, ...] = (
    "malgunbd.ttf",  # Windows: 맑은 고딕
    "malgun.ttf",
    "AppleSDGothicNeo.ttc",  # macOS
    "NanumGothicBold.ttf",
    "NotoSansCJK-Bold.ttc",  # Linux: noto-cjk
    "NanumGothic.ttf",
    "NotoSansCJK-Regular.ttc",
)
# XDG Base Directory 규격이 정한 XDG_DATA_DIRS 의 기본값 (환경 변수가 없을 때)
_XDG_DATA_DIRS = "/usr/local/share:/usr/share"


def system_font_dirs(
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
    platform: str | None = None,
) -> tuple[Path, ...]:
    """운영체제가 글꼴을 두는 폴더. 환경 변수와 운영체제의 규약에서 얻는다 (특정 컴퓨터의 경로를 적지 않는다).

    - Windows: %WINDIR%/Fonts, %LOCALAPPDATA%/Microsoft/Windows/Fonts (사용자 글꼴)
    - macOS: ~/Library/Fonts, /Library/Fonts, /System/Library/Fonts
    - 그 밖 (XDG 규격): $XDG_DATA_HOME/fonts, ~/.fonts, $XDG_DATA_DIRS 의 폴더마다 fonts
    """
    env = os.environ if env is None else env
    home = Path.home() if home is None else home
    platform = sys.platform if platform is None else platform
    if platform == "win32":
        roots = (
            (env.get("WINDIR") or env.get("SystemRoot"), ("Fonts",)),
            (env.get("LOCALAPPDATA"), ("Microsoft", "Windows", "Fonts")),
        )
        return tuple(Path(root).joinpath(*parts) for root, parts in roots if root)
    if platform == "darwin":
        return (home / "Library" / "Fonts", Path("/Library/Fonts"), Path("/System/Library/Fonts"))
    data_home = env.get("XDG_DATA_HOME") or str(home / ".local" / "share")
    data_dirs = (env.get("XDG_DATA_DIRS") or _XDG_DATA_DIRS).split(":")
    return (
        Path(data_home) / "fonts",
        home / ".fonts",
        *(Path(folder) / "fonts" for folder in data_dirs if folder),
    )


def find_font(
    names: Sequence[str] = KOREAN_FONT_FILES, dirs: Sequence[Path] | None = None
) -> str | None:
    """글꼴 폴더들(하위 폴더까지)에서 이름이 맞는 파일을 찾는다.

    이름의 순서가 우선이고, 같은 이름이면 앞 폴더가 먼저다.
    """
    wanted = {name.lower(): rank for rank, name in enumerate(names)}
    best: tuple[int, str] | None = None
    for folder in system_font_dirs() if dirs is None else dirs:
        if not Path(folder).is_dir():
            continue
        try:
            found = sorted(p for p in Path(folder).rglob("*") if p.name.lower() in wanted)
        except OSError:
            continue  # 읽을 수 없는 폴더는 건너뛴다
        for path in found:
            rank = wanted[path.name.lower()]
            if path.is_file() and (best is None or rank < best[0]):
                best = (rank, str(path))
    return best[1] if best else None


def resolve_font(
    explicit: str | None = None,
    *,
    names: Sequence[str] = KOREAN_FONT_FILES,
    dirs: Sequence[Path] | None = None,
) -> str:
    """화면 문구에 쓸 글꼴 파일. 못 찾으면 ConfigurationError (기동 시점에 실패하도록 조립 단계가 부른다).

    지정한 파일(FRAME_OVERLAY_FONT)이 먼저다. 없으면 운영체제의 글꼴 폴더에서 한글 글꼴을 찾는다.
    """
    if explicit:
        if Path(explicit).is_file():
            return explicit
        raise ConfigurationError(f"FRAME_OVERLAY_FONT 에 지정한 글꼴 파일이 없습니다: {explicit}")
    found = find_font(names, dirs)
    if found is not None:
        return found
    raise ConfigurationError(
        "화면 문구에 쓸 글꼴을 찾지 못했습니다. FRAME_OVERLAY_FONT 로 한글이 든 글꼴 파일(.ttf·.ttc)을 지정하세요"
    )


def estimate_text_width(text: str, font_px: float) -> float:
    """글자 폭의 어림 (픽셀). 넓은 글자(한글·한자·전각)는 1 em, 그 밖에는 0.55 em, 공백·문장부호는 0.3 em."""
    width = 0.0
    for ch in text:
        if unicodedata.east_asian_width(ch) in ("W", "F"):
            width += 1.0
        elif ch.isalnum():
            width += 0.55
        else:
            width += 0.3
    return width * font_px


def fit_font_px(text: str, wanted_px: int, region_w_px: float, region_h_px: float) -> int:
    """영역에 들어가도록 줄인 글자 크기 (픽셀). 키우지는 않는다."""
    size = float(min(wanted_px, region_h_px * 0.9))
    width = estimate_text_width(text, size)
    limit = region_w_px * _FILL
    if width > limit > 0:
        size *= limit / width
    return max(_MIN_FONT_PX, int(size))


class _Canvas(NamedTuple):
    """문구를 얹을 클립의 겉모습. 영상이 아니면 만들어지지 않는다 (값이 모두 있다)."""

    width: int
    height: int
    frames: int


class FfmpegOverlayRenderer(OverlayRenderer):
    def __init__(
        self,
        ffmpeg: str | None,
        font_file: str,
        *,
        regular_font_file: str | None = None,
        crf: int = 18,
        preset: str = "medium",
        timeout_s: float = 300.0,
    ) -> None:
        self._ffmpeg = require_ffmpeg(ffmpeg)
        self.font_file = _checked_font(font_file)  # 굵은 문구에 쓴다
        self.regular_font_file = _checked_font(regular_font_file or font_file)
        self.crf = crf
        self.preset = preset
        self.timeout_s = timeout_s
        fonts = "+".join(
            dict.fromkeys(_font_id(path) for path in (font_file, regular_font_file or font_file))
        )
        self._signature = f"ffmpeg-drawtext:1:{fonts}:crf{crf}:{preset}"

    @property
    def signature(self) -> str:
        return self._signature

    def render(self, clip: bytes, overlays: Sequence[OverlayItem]) -> bytes:
        if not overlays:
            return clip
        with tempfile.TemporaryDirectory(prefix="frame-overlay-") as tmp:
            folder = Path(tmp)
            source, target = folder / "source.mp4", folder / "composed.mp4"
            source.write_bytes(clip)
            probe = self._probe(source)
            filters: list[str] = []
            for index, item in enumerate(overlays):
                (folder / f"t{index}.txt").write_text(_one_line(item.text), encoding="utf-8")
                filters += self._filters(item, index, probe)
            cmd = [self._ffmpeg, "-hide_banner", "-nostdin", "-y", "-i", source.name]
            cmd += ["-vf", ",".join(filters), "-an", "-sn", "-map_metadata", "-1"]
            cmd += ["-frames:v", str(probe.frames)]
            cmd += ["-c:v", "libx264", "-preset", self.preset, "-crf", str(self.crf)]
            cmd += ["-pix_fmt", "yuv420p", "-movflags", "+faststart", target.name]
            try:
                code, log = run_ffmpeg(cmd, self.timeout_s, cwd=folder)  # textfile 은 상대 경로
            except MediaToolError as exc:
                raise CompositionError(f"화면 문구를 얹지 못했습니다: {exc}") from exc
            if code != 0 or not target.is_file():
                raise CompositionError(f"화면 문구를 얹지 못했습니다: {tail(log)}")
            result = self._probe(target)
            if result != probe:
                raise CompositionError(
                    f"합성 결과가 원본과 다릅니다: {result.width}x{result.height} {result.frames} 프레임 "
                    f"(원본 {probe.width}x{probe.height} {probe.frames} 프레임)"
                )
            return target.read_bytes()

    def _probe(self, path: Path) -> _Canvas:
        try:
            probe = probe_media(self._ffmpeg, path, self.timeout_s)
        except MediaToolError as exc:
            raise CompositionError(f"장면 클립을 읽지 못했습니다: {exc}") from exc
        if not probe.has_video or not probe.width or not probe.height or not probe.frames:
            raise CompositionError("문구를 얹을 클립이 영상이 아닙니다")
        return _Canvas(probe.width, probe.height, probe.frames)

    def _filters(self, item: OverlayItem, index: int, probe: _Canvas) -> list[str]:
        region, style = item.region, item.style
        x, y = region.x * probe.width, region.y * probe.height
        w, h = region.w * probe.width, region.h * probe.height
        enable = f"enable='gte(t,{item.start_ms / 1000:.3f})*lt(t,{item.end_ms / 1000:.3f})'"
        filters: list[str] = []
        if style.background:
            band = f"{_color(style.background)}@{style.background_opacity:.2f}"
            filters.append(
                f"drawbox=x={x:.0f}:y={y:.0f}:w={w:.0f}:h={h:.0f}:color={band}:t=fill:{enable}"
            )
        size = fit_font_px(_one_line(item.text), round(probe.height * style.size), w, h)
        font = self.font_file if style.bold else self.regular_font_file
        filters.append(
            f"drawtext=fontfile='{font}':textfile='t{index}.txt':expansion=none"
            f":fontcolor={_color(style.color)}:fontsize={size}"
            f":x={x + w / 2:.1f}-text_w/2:y={y + h / 2:.1f}-text_h/2:{enable}"
        )
        return filters


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _color(value: str) -> str:
    """필터에 넣을 색. 아는 색 이름이나 #RRGGBB 만 받는다 (다른 문자열이 필터 문법에 섞이지 않게)."""
    known = color_name(value)
    if known is None:
        raise CompositionError(f"색은 CSS 색 이름이나 #RRGGBB 로 적어야 합니다: '{value}'")
    return known


def _font_id(path: str) -> str:
    """글꼴의 지문: 파일 이름 + 내용의 해시 앞부분. 폴더는 넣지 않는다 (저장소의 키에 경로가 남지 않게)."""
    file = Path(path)
    return f"{file.name}-{hashlib.sha256(file.read_bytes()).hexdigest()[:8]}"


def _checked_font(path: str) -> str:
    """필터 문법에 넣을 수 있게 옮긴 글꼴 경로 (역슬래시 -> 슬래시, 콜론은 \\: 로)."""
    if not Path(path).is_file():
        raise ConfigurationError(f"글꼴 파일이 없습니다: {path}")
    if "'" in path:
        raise ConfigurationError(f"글꼴 경로에 작은따옴표를 쓸 수 없습니다: {path}")
    return path.replace("\\", "/").replace(":", "\\:")
