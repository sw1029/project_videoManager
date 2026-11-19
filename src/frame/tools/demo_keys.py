"""시연 실행기의 API 키: 키 파일 · 상태 · 온라인 확인 (표준 라이브러리만 사용, ADR-0037).

다루는 키
    OpenAI      .demo/openai.key      OPENAI_API_KEY      자연어 요구사항 분석 · 인물 일관성 · 품질 검수(VLM)
    OpenRouter  .demo/openrouter.key  OPENROUTER_API_KEY  실제 영상 생성 (run --video)

- 상태는 실행기가 띄운 서버가 실제로 쓸 키를 알린다. 순서: 환경 변수 > .demo 키 파일(실행기가 서버에 넘긴다) >
  셸의 키 파일 변수(FRAME_LLM_KEY_FILE · FRAME_VIDEO_KEY_FILE, .demo 에 쓸 키가 없을 때 서버에 그대로 간다).
  OpenAI 는 서버에서 화면 입력이 그보다도 먼저다.
- 키 내용은 키 파일에 쓰는 것 말고는 남기지 않는다. 화면에는 끝 네 글자만 보인다 (ADR-0010).
- 온라인 확인은 돈이 들지 않는 조회 하나다 (OpenAI: 모델 목록, OpenRouter: 키 정보).
  - 주소는 여기 적지 않고 설치된 서버 코드에 묻는다 (ADDRESS_PROBE): 서버가 실제로 쓸 주소(기본값과
    FRAME_LLM_BASE_URL · FRAME_VIDEO_BASE_URL)가 그대로 나오고, 같은 주소를 두 곳에 적지 않는다 (ADR-0032).
    그래서 확인은 설치(setup) 뒤에 된다 — 키를 넣고 보는 것은 설치 전에도 된다.
  - 보내는 길은 어댑터가 쓰는 공용 전송(frame.adapters.shared.http)이다: https(또는 이 기기의 로컬 서버)로만,
    키 헤더는 리다이렉트로 넘기지 않고, 헤더에 쓸 수 없는 값이 오류 문구에 실리지 않는다.
  - 다른 서비스의 키는 보내지 않는다 (OpenAI 자리의 sk-or-... 등 — 서버도 그 키를 쓰지 않는다).
- 사람과 주고받는 부분(대화형 메뉴 · keys 명령)은 demo_key_guide 에 있다.
- 의존성을 설치하기 전에도 돌아야 해서 표준 라이브러리만 쓰는 frame 모듈(adapters.shared 의 api_keys · http)만
  가져온다.
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path

from frame.adapters.shared.api_keys import (
    OPENAI_KEY_TOKEN,
    OPENROUTER_KEY_TOKEN,
    find_key,
    mask_key,
)
from frame.adapters.shared.http import (
    HttpResponse,
    HttpTransport,
    TransportError,
    UrllibTransport,
    secure_base_url,
)

DEMO_DIR = ".demo"
CHECK_TIMEOUT_S = 15.0  # 온라인 확인 하나를 기다리는 시간
CHECK_MAX_BYTES = 2 * 1024 * 1024  # 확인 응답 본문의 상한 (모델 목록이 가장 크다, 수백 KB)
# 저장한 키는 폴더 안에 평문으로 남는다. 준비해 둔 폴더를 USB·드라이브로 넘기면 키가 따라간다
KEY_STAYS_IN_FOLDER = (
    "저장한 키는 암호화되지 않은 채 이 폴더 안에 있습니다. "
    "폴더를 복사하거나 다른 사람에게 넘기기 전에 .demo 폴더를 지우세요"
)


@dataclass(frozen=True)
class KeyKind:
    """키 하나의 규칙: 어디에 두고, 어떻게 생겼고, 어디로 보내 확인하는지."""

    name: str  # 명령줄에서 부르는 이름 (keys --remove openrouter)
    title: str
    purpose: str
    file_name: str  # .demo 아래의 키 파일
    env: str  # 서버가 키 파일보다 먼저 보는 환경 변수
    file_env: str  # 서버가 읽는 키 파일 경로의 설정 (실행기는 .demo 의 키가 있으면 이것을 그 파일로 바꾼다)
    token: re.Pattern[str]  # 입력받을 때 받는 키 모양 (다른 서비스의 키는 받지 않는다)
    server_token: re.Pattern[
        str
    ]  # 서버가 키 파일에서 키로 알아보는 모양 (shared.api_keys — 서버와 같은 것)
    prefix: str  # 키의 형식을 알리는 접두사 (표시에도 쓴다)
    issue_url: str  # 키를 만드는 곳
    check_path: str  # 돈이 들지 않는 조회 (서버가 쓸 주소 뒤에 붙인다)


OPENAI = KeyKind(
    name="openai",
    title="OpenAI API 키",
    purpose="자연어 요구사항 분석 · 인물 일관성 · 품질 검수(VLM)",
    file_name="openai.key",
    env="OPENAI_API_KEY",
    file_env="FRAME_LLM_KEY_FILE",
    # sk-or- 는 OpenRouter 의 것이다: 메모에 그 키가 먼저 적혀 있어도 건너뛰고 OpenAI 키를 찾는다.
    # 앞 글자를 보는 것은 다른 키의 한가운데에 든 'sk-' 를 키의 시작으로 잘못 집지 않으려고다
    token=re.compile(r"(?<![A-Za-z0-9_\-])sk-(?!or-)[A-Za-z0-9_\-]{17,400}"),
    server_token=OPENAI_KEY_TOKEN,
    prefix="sk-",
    issue_url="https://platform.openai.com/api-keys",
    check_path="/models",
)
OPENROUTER = KeyKind(
    name="openrouter",
    title="OpenRouter API 키",
    purpose="실제 영상 생성 (run --video, 장면마다 요금)",
    file_name="openrouter.key",
    env="OPENROUTER_API_KEY",
    file_env="FRAME_VIDEO_KEY_FILE",
    token=re.compile(r"(?<![A-Za-z0-9_\-])sk-or-[A-Za-z0-9_\-]{17,400}"),
    server_token=OPENROUTER_KEY_TOKEN,
    prefix="sk-or-",
    issue_url="https://openrouter.ai/keys",
    check_path="/key",
)
KINDS: tuple[KeyKind, ...] = (OPENAI, OPENROUTER)

# 설치된 서버 코드에게 '키를 보낼 주소' 를 묻는 한 줄 (서버의 Python 으로 돌린다: 번들의 python 또는 .venv).
# 서버가 요청을 보내는 바로 그 주소다 — LLM 은 FRAME_LLM_BASE_URL 이 없으면 OPENAI_BASE_URL,
# 영상은 FRAME_VIDEO_BASE_URL. 마지막 줄에 {키 이름: 주소} JSON 을 찍는다
ADDRESS_PROBE = (
    "import json\n"
    "from frame.adapters.llm.openai_client import OPENAI_BASE_URL\n"
    "from frame.config.settings import Settings\n"
    "s = Settings.from_env()\n"
    "print(json.dumps({'openai': s.llm_base_url or OPENAI_BASE_URL, 'openrouter': s.video_base_url}))\n"
)


def kind_named(name: str) -> KeyKind:
    for kind in KINDS:
        if kind.name == name:
            return kind
    raise KeyError(name)


# ---- 키 파일 ------------------------------------------------------------------------------


def extract(kind: KeyKind, text: str) -> str | None:
    """키 파일 내용이나 입력에서 그 서비스의 키를 찾는다 (키만 있는 줄과 `<환경 변수>=...` 줄 모두).

    다른 서비스의 키는 집지 않는다: OpenAI 키 자리에 OpenRouter 키(sk-or-...)를 넣어도 받지 않고, 반대도 같다.
    """
    key = find_key(text or "", name=kind.env, token=kind.token)
    if key is None or kind.token.fullmatch(key) is None:
        return None
    return key


def other_kind(kind: KeyKind, text: str) -> KeyKind | None:
    """넣은 글이 다른 서비스의 키인지 (잘못 넣었을 때 어디에 넣을지 알리려고)."""
    return next((o for o in KINDS if o is not kind and extract(o, text) is not None), None)


def key_shaped(text: str) -> tuple[KeyKind, str] | None:
    """글 전체가 키 하나인가 (경로를 넣을 자리에 키를 넣은 경우). 그 키를 화면에 그대로 내지 않으려고 쓴다."""
    value = text.strip()
    for kind in (OPENROUTER, OPENAI):  # sk-or- 가 sk- 보다 먼저: 더 좁은 모양부터
        if kind.server_token.fullmatch(value):
            return kind, value
    return None


def read_as_server(kind: KeyKind, text: str) -> str | None:
    """서버가 그 키 파일에서 읽을 키 (서버의 llm.keys.read_key_file · openrouter.resolve_openrouter_key 와 같은 규칙).

    입력받을 때의 규칙(extract)과 다를 수 있다: 서버는 OpenAI 키 파일에서도 sk-or- 키를 읽는다 (OpenRouter 로 돌린
    FRAME_LLM_BASE_URL 이면 그 키를 쓴다). 상태는 서버가 실제로 쓸 키를 알려야 한다.
    """
    key = find_key(text or "", name=kind.env, token=kind.server_token)
    if key is None or (kind is OPENROUTER and not key.startswith(OPENROUTER.prefix)):
        return None
    return key


def key_path(root: Path, kind: KeyKind) -> Path:
    return root / DEMO_DIR / kind.file_name


def usable_key_file(root: Path, kind: KeyKind) -> Path | None:
    """실행기가 서버에 넘길 .demo 키 파일: 서버가 거기서 키를 읽을 수 있을 때만 (아니면 셸의 설정을 덮지 않는다)."""
    path = key_path(root, kind)
    return path if _is_file(path) and read_as_server(kind, read_text(path)) else None


def _is_file(path: Path) -> bool:
    """파일인가. 읽을 수 없는 폴더 안이면(POSIX 의 권한) 예외 대신 False — 상태 보기가 traceback 으로 끝나지 않게."""
    try:
        return path.is_file()
    except OSError:
        return False


def save(demo_dir: Path, kind: KeyKind, key: str) -> Path:
    """키를 .demo/<키 파일> 에 쓴다. POSIX 는 처음부터 소유자만 읽고 쓰게 만든다 (Windows 는 사용자 폴더 권한)."""
    demo_dir.mkdir(parents=True, exist_ok=True)
    path = demo_dir / kind.file_name
    content = key.strip() + "\n"
    if os.name == "nt":  # 사용자 폴더의 접근 권한(ACL)을 그대로 따른다
        path.write_text(content, encoding="utf-8")
        return path
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)
    path.chmod(0o600)  # 이미 있던 파일이면 권한을 다시 맞춘다
    return path


def remove(root: Path, kind: KeyKind) -> bool:
    """저장한 키 파일을 지운다. 없었으면 False."""
    path = key_path(root, kind)
    if not path.is_file():
        return False
    path.unlink()
    return True


def mask(kind: KeyKind, key: str) -> str:
    """화면에 보일 표시: 끝 네 글자만 (sk-or-...abcd)."""
    return mask_key(key, prefix=kind.prefix)


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


# ---- 상태 ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class KeyStatus:
    kind: KeyKind
    file_key: str | None  # .demo 키 파일에서 서버가 읽을 키
    file_present: bool  # .demo 키 파일이 있는지 (키 모양이 아니어도)
    env_key: str | None  # 환경 변수의 값
    shell_file: str | None = None  # 셸의 키 파일 변수(FRAME_*_KEY_FILE)의 값 (경로여야 한다)
    shell_file_key: str | None = None  # 그 파일에서 서버가 읽을 키
    shell_file_exists: bool = False  # 그 경로에 파일이 있는지

    @property
    def active(self) -> str | None:
        """실행기가 띄운 서버가 쓸 키 (환경 변수 > .demo 키 파일 > 셸의 키 파일 변수)."""
        return self.env_key or self.file_key or self.shell_file_key

    def describe(self) -> str:
        kind, file = self.kind, f".demo/{self.kind.file_name}"
        if self.env_key:
            text = f"환경 변수 {kind.env} 있음 ({self._shown(self.env_key)})"
            return text + (f" — {file} 보다 먼저 쓰입니다" if self.file_key else "")
        if self.file_key:
            return f"{file} 있음 ({self._shown(self.file_key)})"
        broken = (
            f" ({file} 에는 {kind.prefix} 로 시작하는 키가 없습니다)" if self.file_present else ""
        )
        if self.shell_file_key:
            return f"{kind.file_env} 의 파일 있음 ({self._shown(self.shell_file_key)}){broken}"
        if self.shell_file is not None:
            return self._shell_problem() + broken
        if self.file_present:
            return f"{file} 에 {kind.prefix} 로 시작하는 키가 없습니다 — 다시 넣으세요"
        return "없음"

    def _shown(self, key: str) -> str:
        """끝 네 글자. OpenAI 자리의 sk-or- 키면 그렇다고 덧붙인다 (서버는 OpenAI 주소로 보내지 않는다)."""
        shown = mask(self.kind, key)
        if self.kind is OPENAI and key.startswith(OPENROUTER.prefix):
            return f"{shown} — OpenRouter 키라서 OpenAI 주소로는 쓰이지 않습니다"
        return shown

    def _shell_problem(self) -> str:
        """셸의 키 파일 변수가 쓸 수 없을 때의 까닭. 그 값을 그대로 보이지 않는다 (경로 대신 키를 넣었을 수 있다)."""
        kind, value = self.kind, self.shell_file or ""
        shaped = key_shaped(value)
        if shaped is not None:
            found, key = shaped
            return f"{kind.file_env} 에 경로 대신 키가 들어 있습니다 ({mask(found, key)}) — 키 파일의 경로를 넣으세요"
        if not self.shell_file_exists:
            return (
                f"{kind.file_env} 의 파일이 없습니다: {value} (상대 경로는 저장소 · 번들 폴더 기준)"
            )
        return f"{kind.file_env} 의 파일에 {kind.prefix} 로 시작하는 키가 없습니다: {value}"


def status(root: Path, kind: KeyKind, env: Mapping[str, str]) -> KeyStatus:
    """그 서비스의 키를 서버가 찾는 순서대로 본다. 경로 설정은 서버(Settings)처럼 다듬지 않고 그대로 쓴다."""
    path = key_path(root, kind)
    present = _is_file(path)
    file_key = read_as_server(kind, read_text(path)) if present else None
    env_value = (env.get(kind.env) or "").strip()
    shell_file = env.get(kind.file_env) or None
    shell_key, exists = None, False
    if shell_file is not None and key_shaped(shell_file) is None:
        target = Path(shell_file)
        target = target if target.is_absolute() else root / target  # 서버는 root 에서 뜬다
        exists = _is_file(target)
        shell_key = read_as_server(kind, read_text(target)) if exists else None
    return KeyStatus(kind, file_key, present, env_value or None, shell_file, shell_key, exists)


def any_key_file(root: Path) -> bool:
    return any(key_path(root, kind).is_file() for kind in KINDS)


# ---- 온라인 확인 --------------------------------------------------------------------------


@dataclass(frozen=True)
class CheckResult:
    ok: (
        bool | None
    )  # True 통함 · False 거절됨(또는 쓸 수 없는 키) · None 확인하지 못함 (연결 · 한도 · 권한 · 주소)
    message: str


NOT_INSTALLED = "확인은 설치(setup) 뒤에 됩니다 — 키를 보낼 주소를 설치된 서버 코드에 묻습니다"


def default_transport() -> HttpTransport:
    """실제 네트워크. 시스템 프록시(HTTPS_PROXY)를 따른다 — 회사망에서도 외부 서비스로 나가야 한다."""
    return UrllibTransport(max_response_bytes=CHECK_MAX_BYTES, total_timeout_factor=2.0)


@dataclass(frozen=True)
class Online:
    """온라인 확인에 쓰는 것: 서버가 쓸 주소를 묻는 방법(설치 전이면 None 을 돌려준다)과 전송."""

    addresses: Callable[[], Mapping[str, str] | None]  # 키 이름 -> 주소 (ADDRESS_PROBE 의 답)
    transport: HttpTransport | None = None  # 없으면 실제 네트워크 (default_transport)


def check(
    kind: KeyKind, key: str, base_url: str, *, transport: HttpTransport | None = None
) -> CheckResult:
    """돈이 들지 않는 조회 하나로 키가 통하는지 본다. base_url 은 서버가 그 키를 보낼 주소다 (ADDRESS_PROBE)."""
    if not key.isascii() or any(ch.isspace() or not ch.isprintable() for ch in key):
        return CheckResult(
            False, "키에 공백 · 줄바꿈 같은 글자가 섞여 있어 쓸 수 없습니다. 보내지 않았습니다"
        )
    base = secure_base_url(base_url)
    if base is None:
        return CheckResult(
            None, f"서버 설정의 주소가 https 가 아니라 키를 보내지 않았습니다: {base_url.strip()}"
        )
    wrong = _wrong_service(kind, key, base)
    if wrong is not None:
        return CheckResult(False, wrong)
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    try:
        response = (transport or default_transport()).request(
            "GET", base + kind.check_path, headers=headers, timeout=CHECK_TIMEOUT_S
        )
    except TransportError as exc:  # 문구에 헤더 값(키)은 실리지 않는다 (shared.http)
        return CheckResult(
            None, f"연결하지 못했습니다 ({exc}). 인터넷과 프록시(HTTPS_PROXY)를 확인하세요"
        )
    if response.status == HTTPStatus.OK:
        return CheckResult(True, "통합니다" + _account_note(kind, response.body))
    if response.status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
        return _refused(kind, response)
    if response.status == HTTPStatus.TOO_MANY_REQUESTS:
        return CheckResult(None, "요청이 많아 지금은 확인하지 못했습니다 (HTTP 429). 잠시 뒤 다시")
    return CheckResult(None, f"서비스가 HTTP {response.status} 로 답해 확인하지 못했습니다")


def _wrong_service(kind: KeyKind, key: str, base: str) -> str | None:
    """이 주소로 보내면 안 되는 다른 서비스의 키면 그 까닭 (보내지 않는다).

    - OpenAI 주소로 가는 sk-or- 키: 서버도 보내지 않는다 (openai_client 의 _goes_to_openai 와 같은 판정).
      FRAME_LLM_BASE_URL 을 OpenRouter 로 돌렸으면 서버는 그 키를 쓰므로 확인도 보낸다.
    - OpenRouter 자리의 OpenAI 키: OpenAI 키를 OpenRouter 에 흘리지 않는다.
    """
    if kind is OPENAI and key.startswith(OPENROUTER.prefix) and goes_to_openai(base):
        return "OpenAI 주소로 가는 OpenRouter 키(sk-or-...)입니다 — 서버도 보내지 않습니다. 보내지 않았습니다"
    if kind is OPENROUTER and not key.startswith(OPENROUTER.prefix) and extract(OPENAI, key):
        return "OpenRouter 자리에 OpenAI 키(sk-...)가 있습니다. 보내지 않았습니다"
    return None


def goes_to_openai(base_url: str) -> bool:
    """요청이 OpenAI 의 주소로 가는가 (서버 openai_client._goes_to_openai 와 같다 — test_demo_keys 가 맞춰 본다)."""
    host = urllib.parse.urlsplit(base_url).hostname or ""
    return host == "openai.com" or host.endswith(".openai.com")


def _refused(kind: KeyKind, response: HttpResponse) -> CheckResult:
    """401 · 403. 권한을 줄인 OpenAI 키는 모델 목록을 볼 수 없어도 분석에는 쓸 수 있다 — 거절로 보지 않는다."""
    if kind is OPENAI and _missing_scope(response.body):
        return CheckResult(
            None,
            "키는 인식됐지만 모델 목록을 볼 권한이 없어 확인하지 못했습니다 (권한을 줄인 키). "
            "분석 · 검수에 쓰는 권한은 따로입니다",
        )
    return CheckResult(
        False, f"키가 거절됐습니다 (HTTP {response.status}). {kind.issue_url} 에서 확인하세요"
    )


def _missing_scope(body: bytes) -> bool:
    try:
        error = json.loads(body or b"{}").get("error")
    except (ValueError, AttributeError):
        return False
    message = str(error.get("message", "")).lower() if isinstance(error, dict) else ""
    return "missing scopes" in message or "insufficient permissions" in message


def _account_note(kind: KeyKind, body: bytes) -> str:
    """OpenRouter 의 키 정보에서 쓴 금액과 남은 한도 (있을 때만). OpenAI 의 모델 목록에는 덧붙일 것이 없다."""
    if kind is not OPENROUTER:
        return ""
    try:
        data = json.loads(body or b"{}").get("data")
    except (ValueError, AttributeError):
        return ""
    if not isinstance(data, dict):
        return ""
    notes = []
    usage, remaining = data.get("usage"), data.get("limit_remaining")
    if isinstance(usage, int | float):
        notes.append(f"이 키로 쓴 금액 ${usage:.2f}")
    if isinstance(remaining, int | float):
        notes.append(f"키의 남은 한도 ${remaining:.2f}")
    elif "limit" in data and data.get("limit") is None:
        notes.append("키 자체의 한도 없음 (계정 잔액을 따름)")
    return f" · {', '.join(notes)}" if notes else ""


def ask_addresses(online: Online | None) -> Mapping[str, str] | None:
    """서버가 쓸 주소들 (설치 전이거나 물을 수 없으면 None). 서버 Python 을 띄워 묻는다 — 확인 한 번에 한 번만."""
    return online.addresses() if online is not None else None


def check_key(
    kind: KeyKind, key: str, addresses: Mapping[str, str] | None, online: Online | None
) -> CheckResult:
    """그 키를 서버가 쓸 주소로 확인한다. 주소를 모르면(설치 전) 보내지 않고 그렇다고 알린다."""
    base = (addresses or {}).get(kind.name)
    if not base or online is None:
        return CheckResult(None, NOT_INSTALLED)
    return check(kind, key, base, transport=online.transport)
