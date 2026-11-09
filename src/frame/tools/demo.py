"""새 기기에서 FRAME 시연을 준비하고 실행한다 (Windows / macOS / Linux 공통, 표준 라이브러리만 사용).

    python scripts/frame_demo.py doctor   # 실행 환경 점검
    python scripts/frame_demo.py setup    # 가상환경·의존성 설치, 화면 빌드, (선택) API 키 저장
    python scripts/frame_demo.py run      # 서버 하나로 화면과 API 를 띄우고 브라우저를 연다
    python scripts/frame_demo.py smoke    # 설치 확인: 화면·API·구조화·키 입력 경로 자동 점검

래퍼: Windows 는 scripts\\setup.cmd · scripts\\run.cmd, macOS·Linux 는 sh scripts/setup.sh · sh scripts/run.sh

의존성을 설치하기 전에도 실행되므로 frame 의 다른 모듈(pydantic·fastapi 사용)을 import 하지 않는다.
시연 상태(DB·산출물·키 파일)는 저장소의 .demo/ 에 두며 git 에서 제외된다.
"""

from __future__ import annotations

import argparse
import contextlib
import getpass
import http.client
import json
import os
import platform as pyplatform
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path, PurePath

MIN_PYTHON = (3, 11)
MIN_NODE = 18
DEFAULT_PORT = 8000
LAN_RATE_LIMIT_PER_MINUTE = "30"
LOOPBACK = "127.0.0.1"
DEMO_DIR = ".demo"
KEY_FILE_NAME = "openai.key"
_KEY_TOKEN = re.compile(r"sk-[A-Za-z0-9_\-]{17,400}")

INSTALL_HINTS = {
    "python": {
        "win32": "winget install Python.Python.3.12  (또는 https://www.python.org/downloads/)",
        "darwin": "brew install python@3.12  (또는 https://www.python.org/downloads/)",
        "linux": "sudo apt install python3.12 python3.12-venv  (배포판에 맞는 3.11 이상)",
    },
    "node": {
        "win32": "winget install OpenJS.NodeJS.LTS",
        "darwin": "brew install node",
        "linux": "nvm 또는 https://github.com/nodesource/distributions 로 Node.js 20 LTS",
    },
}


# ---- 순수 함수 (단위 테스트 대상) ---------------------------------------------------------


def platform_key(platform: str = sys.platform) -> str:
    """sys.platform 을 win32 · darwin · linux 중 하나로 줄인다 (그 밖의 POSIX 는 linux 로 본다)."""
    if platform.startswith("win"):
        return "win32"
    if platform == "darwin":
        return "darwin"
    return "linux"


def venv_python(root: Path, platform: str = sys.platform) -> Path:
    if platform_key(platform) == "win32":
        return root / ".venv" / "Scripts" / "python.exe"
    return root / ".venv" / "bin" / "python"


def foreign_venv(root: Path, platform: str = sys.platform) -> bool:
    """다른 OS 에서 만든 .venv 인지 (Windows 와 WSL 이 한 폴더를 같이 쓸 때). 지우지 않고 멈추기 위해 쓴다."""
    venv = root / ".venv"
    own, other = ("Scripts", "bin") if platform_key(platform) == "win32" else ("bin", "Scripts")
    return (venv / other).is_dir() and not (venv / own).is_dir()


def sqlite_url(path: PurePath) -> str:
    """절대 경로를 SQLAlchemy SQLite URL 로 바꾼다 (Windows: ///C:/..., POSIX: ////home/...)."""
    return f"sqlite+pysqlite:///{path.as_posix()}"


def python_supported(version_info: Sequence[int]) -> bool:
    return tuple(version_info[:2]) >= MIN_PYTHON


def parse_node_major(text: str) -> int | None:
    match = re.search(r"v?(\d+)\.\d+", text or "")
    return int(match.group(1)) if match else None


def extract_key(text: str) -> str | None:
    """키 파일 내용에서 sk- 키를 찾는다 (키만 있는 줄과 OPENAI_API_KEY=... 줄 모두)."""
    match = _KEY_TOKEN.search(text or "")
    return match.group(0) if match else None


def npm_install_command(frontend: Path) -> list[str]:
    if (frontend / "package-lock.json").is_file():
        return ["npm", "ci", "--no-audit", "--no-fund"]
    return ["npm", "install", "--no-audit", "--no-fund"]


def demo_env(
    root: Path,
    *,
    offline: bool,
    base_env: Mapping[str, str],
    key_file: Path | None,
    lan: bool = False,
) -> dict[str, str]:
    """시연 서버용 환경 변수. offline 이면 규칙 기반(DSL) 구조화로 인터넷·키 없이 동작한다."""
    demo = root / DEMO_DIR
    env = dict(base_env)
    env.update(
        {
            "FRAME_STRUCTURER": "rule_based" if offline else "llm",
            "FRAME_JOBS": "inline",  # 화면에 자동 새로고침이 없어 요청 안에서 처리를 끝낸다
            "FRAME_PERSISTENCE": "sqlalchemy",
            "FRAME_DB_URL": sqlite_url(demo / "frame.db"),
            "FRAME_ARTIFACT_STORE": "local_fs",
            "FRAME_ARTIFACT_DIR": str(demo / "artifacts"),
            "FRAME_FRONTEND_DIST": str(root / "frontend" / "dist"),
            "PYTHONUTF8": "1",
        }
    )
    if not offline:
        env["FRAME_LLM_RUNTIME_KEY"] = "true"  # 키가 없으면 화면에서 입력 (서버와 같은 기기에서만)
        if key_file is not None:
            env["FRAME_LLM_KEY_FILE"] = str(key_file)
    if lan:  # 인증 없는 시연 서버를 네트워크에 열 때는 요청 수라도 제한한다
        env.setdefault("FRAME_RATE_LIMIT_PER_MINUTE", LAN_RATE_LIMIT_PER_MINUTE)
    return env


def uvicorn_command(python: Path, host: str, port: int) -> list[str]:
    return [
        str(python),
        "-m",
        "uvicorn",
        "--factory",
        "frame.presentation.main:create_default_app",
        "--host",
        host,
        "--port",
        str(port),
    ]


def can_open_browser(platform: str = sys.platform, env: Mapping[str, str] | None = None) -> bool:
    """Linux 는 화면(X11·Wayland)이 있을 때만 연다. 없으면 콘솔 브라우저가 터미널을 차지할 수 있다."""
    if platform_key(platform) != "linux":
        return True
    env = os.environ if env is None else env
    return bool(env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"))


def runner_hint(platform: str = sys.platform) -> str:
    return r"scripts\run.cmd" if platform_key(platform) == "win32" else "sh scripts/run.sh"


def setup_hint(platform: str = sys.platform) -> str:
    return r"scripts\setup.cmd" if platform_key(platform) == "win32" else "sh scripts/setup.sh"


def save_key(demo_dir: Path, key: str) -> Path:
    """키를 .demo/openai.key 에 저장한다. POSIX 는 처음부터 소유자만 읽고 쓰게 만든다."""
    demo_dir.mkdir(parents=True, exist_ok=True)
    path = demo_dir / KEY_FILE_NAME
    content = key.strip() + "\n"
    if os.name == "nt":  # 사용자 폴더의 접근 권한(ACL)을 그대로 따른다
        path.write_text(content, encoding="utf-8")
        return path
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)
    path.chmod(0o600)  # 이미 있던 파일이면 권한을 다시 맞춘다
    return path


def reset_state(demo_dir: Path) -> None:
    """시연 DB 와 산출물을 지운다. 키 파일은 남긴다."""
    for name in ("frame.db", "frame.db-journal", "frame.db-wal", "frame.db-shm"):
        (demo_dir / name).unlink(missing_ok=True)
    shutil.rmtree(demo_dir / "artifacts", ignore_errors=True)


def key_file_path(root: Path) -> Path:
    return root / DEMO_DIR / KEY_FILE_NAME


# ---- 출력 · 실행 도우미 ------------------------------------------------------------------


def _say(message: str) -> None:
    print(f"[frame-demo] {message}", flush=True)


def _fail(message: str) -> int:
    print(f"[frame-demo] 오류: {message}", file=sys.stderr, flush=True)
    return 1


def _hint(tool: str) -> str:
    return INSTALL_HINTS[tool][platform_key()]


def _utf8_output() -> None:
    """Windows 에서 출력이 파이프로 갈 때(CI·Git Bash 등) 한글 때문에 실패하지 않게 UTF-8 로 맞춘다."""
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError, OSError):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _run(command: Sequence[str], cwd: Path) -> int:
    """명령을 실행하고 종료 코드를 돌려준다. Windows 의 npm.cmd 같은 래퍼도 경로를 찾아 실행한다."""
    _say(f"$ {' '.join(command)}")
    executable = shutil.which(command[0]) or command[0]
    try:
        return subprocess.run([executable, *command[1:]], cwd=cwd).returncode
    except OSError as exc:
        _say(f"실행할 수 없습니다: {exc}")
        return 127


def _quiet_ok(command: Sequence[str]) -> bool:
    try:
        return subprocess.run(command, capture_output=True).returncode == 0
    except OSError:
        return False


def _venv_ready(python: Path) -> bool:
    """가상환경 파이썬이 3.11 이상이고 pip 가 있는지. Debian 계열의 반쪽 .venv(venv 패키지 없음)를 거른다."""
    check = "import sys, pip; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
    return python.is_file() and _quiet_ok([str(python), "-c", check])


def _node_major() -> int | None:
    node = shutil.which("node")
    if node is None:
        return None
    try:
        result = subprocess.run([node, "--version"], capture_output=True, text=True)
    except OSError:
        return None
    return parse_node_major(result.stdout)


def _port_free(port: int, host: str) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        if os.name != "nt":  # Windows 의 SO_REUSEADDR 는 사용 중인 포트에도 bind 를 허용해 검사가 무의미하다
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((LOOPBACK, 0))
        return int(sock.getsockname()[1])


def _lan_ip() -> str:
    with contextlib.suppress(OSError), socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect(("192.0.2.1", 80))  # UDP connect 는 패킷 없이 나갈 인터페이스만 정한다
        return str(sock.getsockname()[0])
    return socket.gethostname()


# 로컬 서버 확인은 시스템 프록시(회사망 등)를 거치지 않는다
_LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _http(method: str, url: str, body: object | None = None, timeout: float = 30) -> tuple[int, bytes]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with _LOCAL_OPENER.open(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _json(method: str, url: str, body: object | None = None) -> tuple[int, dict]:
    status, raw = _http(method, url, body)
    try:
        data = json.loads(raw or b"{}")
    except ValueError:
        data = {}
    return status, data if isinstance(data, dict) else {}


def _start_server(root: Path, env: Mapping[str, str], host: str, port: int) -> subprocess.Popen:
    process = subprocess.Popen(uvicorn_command(venv_python(root), host, port), cwd=root, env=dict(env))
    _tie_to_launcher(process)
    return process


_JOB_HANDLES: list[int] = []  # 런처가 끝나면 OS 가 이 핸들을 닫고, 그때 묶인 서버도 함께 끝난다


def _tie_to_launcher(process: subprocess.Popen) -> None:
    """Windows: 런처가 강제 종료돼도 서버가 남아 포트를 붙잡지 않게 작업 개체(Job Object)로 묶는다.

    Ctrl+C·창 닫기는 원래 서버까지 끝나지만, 작업 관리자·도구가 런처만 끝내면 서버가 남는다.
    실패해도 기능에는 영향이 없다.
    """
    if os.name != "nt":
        return
    with contextlib.suppress(Exception):
        _JOB_HANDLES.append(kill_on_close_job(process.pid))


def kill_on_close_job(pid: int) -> int:
    """pid 를 'JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE' 작업 개체에 넣고 그 핸들을 돌려준다 (Windows 전용)."""
    import ctypes
    from ctypes import wintypes

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in ("r", "w", "o", "rb", "wb", "ob")]

    class BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimits),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD
    ]
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise OSError(ctypes.get_last_error(), "CreateJobObject")  # type: ignore[attr-defined]
    info = ExtendedLimits()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    process = kernel32.OpenProcess(0x0100 | 0x0001, False, pid)  # PROCESS_SET_QUOTA | PROCESS_TERMINATE
    try:
        ok = (
            process
            and kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
            and kernel32.AssignProcessToJobObject(job, process)
        )
        if not ok:  # 9 = JobObjectExtendedLimitInformation
            kernel32.CloseHandle(job)
            raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject")  # type: ignore[attr-defined]
    finally:
        if process:
            kernel32.CloseHandle(process)
    return int(job)


def _stop_on_termination_signals() -> None:
    """SIGTERM·SIGHUP(창 닫기)·SIGBREAK 를 Ctrl+C 처럼 처리해 finally 에서 서버를 끄게 한다 (기본 동작은 즉시 종료)."""

    def interrupt(signum: int, frame: object) -> None:
        raise KeyboardInterrupt

    for name in ("SIGTERM", "SIGHUP", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is not None:
            with contextlib.suppress(ValueError, OSError):  # 메인 스레드가 아니면 설치할 수 없다
                signal.signal(sig, interrupt)


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _wait_for_health(base: str, process: subprocess.Popen, timeout: float = 90) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        with contextlib.suppress(OSError, http.client.HTTPException):
            if _http("GET", f"{base}/health", timeout=2)[0] == 200:
                return True
        time.sleep(0.5)
    return False


@contextlib.contextmanager
def _server(root: Path, env: Mapping[str, str], port: int) -> Iterator[str]:
    process = _start_server(root, env, LOOPBACK, port)
    try:
        base = f"http://{LOOPBACK}:{port}"
        if not _wait_for_health(base, process):
            raise RuntimeError("서버가 시작되지 않았습니다. 위의 서버 로그를 확인하세요")
        yield base
    finally:
        _stop(process)


def _ready_to_run(root: Path) -> str | None:
    if not _venv_ready(venv_python(root)):
        return f"설치가 필요합니다. 먼저 {setup_hint()} 를 실행하세요"
    if not (root / "frontend" / "dist" / "index.html").is_file():
        return f"화면 빌드가 없습니다. 먼저 {setup_hint()} 를 실행하세요"
    return None


# ---- 명령 ---------------------------------------------------------------------------------


def cmd_doctor(root: Path) -> int:
    _say(f"운영체제: {pyplatform.platform()} ({platform_key()})")
    version = pyplatform.python_version()
    python_ok = python_supported(sys.version_info)
    _say(f"Python {version}: {'사용 가능' if python_ok else '3.11 이상 필요. 설치: ' + _hint('python')}")
    venv = venv_python(root)
    if _venv_ready(venv):
        _say("가상환경(.venv): 준비됨")
    elif foreign_venv(root):
        _say("가상환경(.venv): 다른 운영체제용입니다. 이 OS 에서는 저장소를 따로 clone 하세요")
    else:
        _say(f"가상환경(.venv): {'다시 만들어야 함' if venv.exists() else '없음'} (setup 이 처리)")
    node = _node_major()
    if node is None:
        _say(f"Node.js: 없음. 화면 빌드(setup)에만 필요합니다. 설치: {_hint('node')}")
    else:
        node_ok = node >= MIN_NODE
        _say(f"Node.js {node}: {'사용 가능' if node_ok else f'{MIN_NODE} 이상 필요. 설치: ' + _hint('node')}")
    dist = root / "frontend" / "dist" / "index.html"
    _say(f"화면 빌드(frontend/dist): {'있음' if dist.is_file() else '없음 (setup 이 처리)'}")
    if key_file_path(root).is_file():
        _say("OpenAI 키: .demo/openai.key 있음")
    elif os.environ.get("OPENAI_API_KEY"):
        _say("OpenAI 키: OPENAI_API_KEY 환경 변수 있음")
    else:
        _say("OpenAI 키: 없음. 실행 후 화면에서 넣거나, run --offline 으로 키 없이 시연할 수 있습니다")
    port_state = "사용 가능" if _port_free(DEFAULT_PORT, LOOPBACK) else "사용 중 (run --port 로 변경)"
    _say(f"포트 {DEFAULT_PORT}: {port_state}")
    problem = _ready_to_run(root)
    _say(f"판정: {problem}" if problem else f"판정: 실행 준비 완료 → {runner_hint()}")
    return 0 if python_ok and problem is None else 1


def cmd_setup(root: Path, *, key_file: str | None, no_key: bool, skip_frontend: bool) -> int:
    venv = venv_python(root)
    if foreign_venv(root):
        return _fail(
            ".venv 가 다른 운영체제(Windows ↔ WSL·Linux)에서 만들어졌습니다. 지우지 않고 멈춥니다. "
            "이 OS 에서는 저장소를 따로 clone 하세요 (WSL 은 ~/ 아래 권장)"
        )
    if not _venv_ready(venv):
        command = [sys.executable, "-m", "venv", ".venv"]
        if (root / ".venv").exists():
            _say("쓸 수 없는 .venv 를 다시 만듭니다 (pip 가 없거나 Python 3.11 미만)")
            command.insert(3, "--clear")
        else:
            _say("가상환경(.venv)을 만듭니다")
        if _run(command, root) != 0 or not _venv_ready(venv):
            minor = f"{sys.version_info.major}.{sys.version_info.minor}"
            return _fail(
                "가상환경을 만들지 못했습니다. Ubuntu·Debian 은 venv 패키지가 필요합니다: "
                f"sudo apt install python{minor}-venv"
            )
    _say("백엔드 의존성을 설치합니다 (처음에는 몇 분 걸릴 수 있습니다)")
    pip = [str(venv), "-m", "pip", "install", "--disable-pip-version-check", "-e", ".[llm]"]
    if _run(pip, root) != 0:
        return _fail("백엔드 의존성 설치에 실패했습니다. 인터넷 연결과 위의 pip 메시지를 확인하세요")

    frontend = root / "frontend"
    dist_index = frontend / "dist" / "index.html"
    if skip_frontend:
        _say("화면 빌드를 건너뜁니다")
    elif not (frontend / "package.json").is_file():
        return _fail("frontend/ 폴더가 없습니다. 화면이 포함된 브랜치(dev)에서 실행하세요")
    else:
        node = _node_major()
        if node is None or node < MIN_NODE or shutil.which("npm") is None:
            if not dist_index.is_file():
                return _fail(f"화면 빌드에 Node.js {MIN_NODE} 이상이 필요합니다. 설치: {_hint('node')}")
            _say(f"Node.js {MIN_NODE} 이상이 없어 기존 화면 빌드를 그대로 씁니다")
        else:
            _say(f"화면 의존성을 설치하고 빌드합니다 (Node.js {node})")
            if _run(npm_install_command(frontend), frontend) or _run(["npm", "run", "build"], frontend):
                return _fail("화면 빌드에 실패했습니다. frontend/node_modules 를 지우고 다시 실행해 보세요")

    status = _store_key(root, key_file=key_file, no_key=no_key)
    if status:
        return status
    _say(f"준비 완료. 실행: {runner_hint()}   (키 없이 오프라인 시연: {runner_hint()} --offline)")
    return 0


def _store_key(root: Path, *, key_file: str | None, no_key: bool) -> int:
    demo = root / DEMO_DIR
    demo.mkdir(exist_ok=True)
    if key_file:
        source = Path(key_file).expanduser()
        if not source.is_file():
            return _fail(f"키 파일이 없습니다: {source}")
        key = extract_key(source.read_text(encoding="utf-8", errors="ignore"))
        if key is None:
            return _fail(f"키 파일에서 sk- 로 시작하는 키를 찾지 못했습니다: {source}")
        save_key(demo, key)
        _say("OpenAI 키를 .demo/openai.key 에 저장했습니다 (git 에서 제외됨)")
        return 0
    if key_file_path(root).is_file():
        _say("기존 .demo/openai.key 를 그대로 씁니다")
        return 0
    if no_key or not sys.stdin.isatty():
        _say("키는 실행 후 화면의 'OpenAI API 키' 칸에 넣을 수 있습니다")
        return 0
    try:
        entered = getpass.getpass(
            "[frame-demo] OpenAI API 키 (입력은 화면에 보이지 않습니다. 건너뛰려면 Enter): "
        )
    except (EOFError, KeyboardInterrupt):
        entered = ""
    if not entered.strip():
        _say("키 입력을 건너뛰었습니다. 실행 후 화면의 'OpenAI API 키' 칸에 넣으면 됩니다")
        return 0
    key = extract_key(entered)
    if key is None:
        return _fail("sk- 로 시작하는 키가 아닙니다. setup 을 다시 실행하거나 화면에서 입력하세요")
    save_key(demo, key)
    _say("OpenAI 키를 .demo/openai.key 에 저장했습니다 (git 에서 제외됨)")
    return 0


def cmd_run(
    root: Path, *, port: int, offline: bool, lan: bool, open_browser: bool, reset: bool
) -> int:
    problem = _ready_to_run(root)
    if problem:
        return _fail(problem)
    demo = root / DEMO_DIR
    if reset:
        reset_state(demo)
        _say("시연 DB 와 산출물을 지웠습니다 (키 파일은 그대로)")
    host = "0.0.0.0" if lan else LOOPBACK
    if not _port_free(port, host):
        return _fail(f"포트 {port} 를 이미 쓰고 있습니다. --port 로 바꾸세요 (예: --port 8010)")
    demo.mkdir(exist_ok=True)
    _stop_on_termination_signals()
    key = key_file_path(root)
    env = demo_env(
        root, offline=offline, lan=lan, base_env=os.environ, key_file=key if key.is_file() else None
    )
    process = _start_server(root, env, host, port)
    base = f"http://{LOOPBACK}:{port}"
    try:
        if not _wait_for_health(base, process):
            return _fail("서버가 시작되지 않았습니다. 위의 서버 로그를 확인하세요")
        _announce(base, port, offline=offline, lan=lan)
        if open_browser and can_open_browser():
            webbrowser.open(base)
        while process.poll() is None:  # Windows 에서도 Ctrl+C 로 바로 끝낼 수 있게 짧게 기다린다
            time.sleep(0.5)
        return process.returncode
    except KeyboardInterrupt:
        _say("종료합니다")
        return 0
    finally:
        _stop(process)


def _announce(base: str, port: int, *, offline: bool, lan: bool) -> None:
    mode = "오프라인 (DSL 프리셋, 키 불필요)" if offline else "LLM 자연어 구조화"
    _say(f"실행 중: {base}   [{mode}]   끝내려면 Ctrl+C")
    if not offline:
        key = _json("GET", f"{base}/health")[1].get("llm_key") or {}
        if key.get("configured"):
            _say(f"OpenAI 키: 준비됨 ({key.get('hint')})")
        else:
            _say("OpenAI 키: 없음 → 화면 '01 · 새 프로젝트 생성'의 'OpenAI API 키' 칸에 넣으세요 (이 기기에서만)")
    if lan:
        _say(f"같은 네트워크의 다른 기기(iPhone·iPad·다른 PC): http://{_lan_ip()}:{port}")
        _say(
            "주의: 인증이 없는 시연용 서버입니다. 믿을 수 있는 네트워크에서만 쓰세요. "
            "다른 기기도 이 서버의 키로 분석을 요청할 수 있고, 키 입력·삭제만 이 기기로 제한됩니다"
        )


def cmd_smoke(root: Path, *, port: int) -> int:
    """오프라인 흐름(구조화→생성→결과)과 LLM 모드의 키 입력 경로를 외부 호출 없이 점검한다."""
    problem = _ready_to_run(root)
    if problem:
        return _fail(problem)
    failures: list[str] = []

    def check(ok: bool, label: str) -> bool:
        _say(f"{'통과' if ok else '실패'}: {label}")
        if not ok:
            failures.append(label)
        return ok

    # 개발자 환경의 FRAME_* · OPENAI_API_KEY 가 결과를 바꾸지 않게 지운다
    base_env = {
        k: v for k, v in os.environ.items() if not k.startswith("FRAME_") and k != "OPENAI_API_KEY"
    }
    with tempfile.TemporaryDirectory(prefix="frame-smoke-", ignore_cleanup_errors=True) as tmp:
        state = {
            "FRAME_DB_URL": sqlite_url(Path(tmp) / "smoke.db"),
            "FRAME_ARTIFACT_DIR": str(Path(tmp) / "artifacts"),
        }
        offline_env = {**demo_env(root, offline=True, base_env=base_env, key_file=None), **state}
        online_env = {**demo_env(root, offline=False, base_env=base_env, key_file=None), **state}
        try:
            with _server(root, offline_env, port or _free_port()) as base:
                _smoke_offline(base, check)
            with _server(root, online_env, port + 1 if port else _free_port()) as base:
                _smoke_online(base, check)
        except RuntimeError as exc:
            return _fail(str(exc))
    if failures:
        return _fail(f"점검 실패 {len(failures)}건: {', '.join(failures)}")
    _say("모든 점검을 통과했습니다")
    return 0


def _smoke_offline(base: str, check: Callable[[bool, str], bool]) -> None:
    status, health = _json("GET", f"{base}/health")
    check(status == 200 and health.get("structuring_input") == "dsl", "상태 확인 (오프라인 = DSL 입력)")
    status, page = _http("GET", f"{base}/")
    check(status == 200 and b"FRAME" in page, "화면을 같은 주소에서 제공")
    status, data = _json("GET", f"{base}/presets.json")
    preset = next((p for p in data.get("presets", []) if p.get("mode") == "dsl"), None)
    if not check(status == 200 and preset is not None, "시연 프리셋 제공"):
        return
    body = {
        "title": preset["title"],
        "prompt": preset["prompt"],
        "target_duration_ms": int(preset["duration_s"] * 1000),
    }
    status, created = _json("POST", f"{base}/api/projects", body)
    version_url = f"{base}/api/versions/{created.get('version_id')}"
    drafted = status == 202 and _json("GET", version_url)[1].get("state") == "DRAFT"
    if not check(drafted, "프리셋 구조화 (DRAFT)"):
        return
    status, _ = _json("POST", f"{version_url}/generations")
    version = _json("GET", version_url)[1]
    if not check(status == 202 and version.get("state") == "COMPLETED", "생성·추적·검수 (COMPLETED)"):
        return
    check(_http("GET", f"{base}{version.get('video_url')}")[0] == 200, "결과 영상 주소")
    check(_json("GET", f"{version_url}/checks")[0] == 200, "검수 결과 조회")


def _smoke_online(base: str, check: Callable[[bool, str], bool]) -> None:
    health = _json("GET", f"{base}/health")[1]
    key = health.get("llm_key") or {}
    started = (
        health.get("structuring_input") == "natural_language"
        and key.get("runtime_entry") is True
        and key.get("configured") is False
    )
    check(started, "LLM 모드: 키 없이 기동, 같은 기기에서 키 입력 가능")
    project = {"title": "smoke", "prompt": "바리스타가 빨간 컵을 들어 보인다", "target_duration_ms": 8000}
    created = _json("POST", f"{base}/api/projects", project)[1]
    version = _json("GET", f"{base}/api/versions/{created.get('version_id')}")[1]
    guided = version.get("state") == "FAILED" and version.get("error_code") == "LLM_UNAVAILABLE"
    check(guided, "키가 없을 때 안내 (LLM_UNAVAILABLE)")
    status, data = _json("PUT", f"{base}/api/settings/llm-key", {"api_key": "sk-smoke-" + "x" * 24})
    check(status == 200 and data.get("source") == "runtime", "화면 키 입력 (메모리)")
    status, data = _json("DELETE", f"{base}/api/settings/llm-key")
    check(status == 200 and data.get("configured") is False, "입력 키 지우기")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="frame_demo", description="FRAME 시연 준비·실행 (Windows / macOS / Linux)"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="실행 환경 점검")
    setup = sub.add_parser("setup", help="가상환경·의존성 설치, 화면 빌드, API 키 저장(선택)")
    setup.add_argument("--key-file", help="OpenAI 키가 든 파일 (내용을 .demo/openai.key 로 복사)")
    setup.add_argument("--no-key", action="store_true", help="키를 묻지 않음 (실행 후 화면에서 입력)")
    setup.add_argument("--skip-frontend", action="store_true", help="화면 빌드 생략")
    run = sub.add_parser("run", help="화면+API 서버 실행")
    run.add_argument("--port", type=int, default=DEFAULT_PORT)
    run.add_argument("--offline", action="store_true", help="인터넷·키 없이 규칙 기반(DSL)으로 시연")
    run.add_argument("--lan", action="store_true", help="같은 네트워크의 다른 기기에서 보기 허용")
    run.add_argument("--no-browser", action="store_true", help="브라우저를 자동으로 열지 않음")
    run.add_argument("--reset", action="store_true", help="시연 DB·산출물을 지우고 시작 (키 유지)")
    smoke = sub.add_parser("smoke", help="설치 확인용 자동 점검")
    smoke.add_argument("--port", type=int, default=0, help="0 이면 빈 포트를 자동 선택")
    return parser


def main(argv: Sequence[str] | None = None, *, root: Path) -> int:
    _utf8_output()
    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        return cmd_doctor(root)
    if not python_supported(sys.version_info):
        current = pyplatform.python_version()
        return _fail(f"Python 3.11 이상이 필요합니다 (현재 {current}). 설치: {_hint('python')}")
    if args.command == "setup":
        return cmd_setup(
            root, key_file=args.key_file, no_key=args.no_key, skip_frontend=args.skip_frontend
        )
    if args.command == "run":
        return cmd_run(
            root,
            port=args.port,
            offline=args.offline,
            lan=args.lan,
            open_browser=not args.no_browser,
            reset=args.reset,
        )
    return cmd_smoke(root, port=args.port)
