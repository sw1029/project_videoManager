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

import re
import tempfile
import unicodedata
from collections.abc import Sequence
from pathlib import Path

from frame.adapters.media_tools import MediaProbe, MediaToolError, probe_media, run_ffmpeg, tail
from frame.adapters.video_tools import require_ffmpeg
from frame.application.errors import ConfigurationError
from frame.application.ports.compositor import CompositionError, OverlayRenderer
from frame.domain.episode import OverlayItem

_COLOR = re.compile(r"^(?:[A-Za-z]{3,20}|#[0-9A-Fa-f]{6})$")
_MIN_FONT_PX = 10
_FILL = 0.94  # 글자가 영역의 폭을 이만큼까지 쓴다

# 글꼴을 지정하지 않았을 때 찾아보는 곳 (한글이 든 글꼴). 굵은 것 먼저
DEFAULT_FONT_CANDIDATES: tuple[str, ...] = (
    "C:/Windows/Fonts/malgunbd.ttf",
    "C:/Windows/Fonts/malgun.ttf",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/Library/Fonts/NanumGothicBold.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
)


def resolve_font(
    explicit: str | None = None, candidates: Sequence[str] = DEFAULT_FONT_CANDIDATES
) -> str:
    """화면 문구에 쓸 글꼴 파일. 못 찾으면 ConfigurationError (기동 시점에 실패하도록 조립 단계가 부른다)."""
    if explicit:
        if Path(explicit).is_file():
            return explicit
        raise ConfigurationError(f"FRAME_OVERLAY_FONT 에 지정한 글꼴 파일이 없습니다: {explicit}")
    for path in candidates:
        if Path(path).is_file():
            return path
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
            if (result.width, result.height, result.frames) != (
                probe.width,
                probe.height,
                probe.frames,
            ):
                raise CompositionError(
                    f"합성 결과가 원본과 다릅니다: {result.width}x{result.height} {result.frames} 프레임 "
                    f"(원본 {probe.width}x{probe.height} {probe.frames} 프레임)"
                )
            return target.read_bytes()

    def _probe(self, path: Path) -> MediaProbe:
        try:
            probe = probe_media(self._ffmpeg, path, self.timeout_s)
        except MediaToolError as exc:
            raise CompositionError(f"장면 클립을 읽지 못했습니다: {exc}") from exc
        if not probe.has_video or not probe.height or not probe.frames:
            raise CompositionError("문구를 얹을 클립이 영상이 아닙니다")
        return probe

    def _filters(self, item: OverlayItem, index: int, probe: MediaProbe) -> list[str]:
        assert probe.width is not None and probe.height is not None
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
    if not _COLOR.match(value or ""):
        raise CompositionError(f"색은 이름이나 #RRGGBB 로 적어야 합니다: '{value}'")
    return value


def _checked_font(path: str) -> str:
    """필터 문법에 넣을 수 있게 옮긴 글꼴 경로 (역슬래시 -> 슬래시, 콜론은 \\: 로)."""
    if not Path(path).is_file():
        raise ConfigurationError(f"글꼴 파일이 없습니다: {path}")
    if "'" in path:
        raise ConfigurationError(f"글꼴 경로에 작은따옴표를 쓸 수 없습니다: {path}")
    return path.replace("\\", "/").replace(":", "\\:")
