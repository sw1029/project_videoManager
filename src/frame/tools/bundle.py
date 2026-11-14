"""OS별 시연 번들을 만든다: 휴대용 Python + FRAME·의존성 + 빌드된 화면 + 실행 스크립트 (표준 라이브러리만 사용).

    python scripts/frame_bundle.py build                # 지금 OS·CPU 용 번들 → dist/frame-demo-<대상>.zip|tar.gz
    python scripts/frame_bundle.py verify dist          # 임시 폴더에 풀어 저장소·다른 Python 없이 실행되는지 점검
    python scripts/frame_bundle.py targets              # 지원 대상 목록

번들은 그 OS·CPU 에서 만들어야 한다. 동봉 Python 으로 pip 를 돌려 그 플랫폼용 휠을 설치하기 때문이다
(CI 의 bundle 작업이 대상마다 맞는 러너에서 만든다). 휴대용 Python 은 astral-sh/python-build-standalone 의
install_only_stripped 배포본이고, 버전과 SHA-256 을 이 파일에 고정한다.
받는 사람은 Python·Node.js 를 설치할 필요가 없다.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import platform as pyplatform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from frame.tools import demo as launcher

APP_NAME = "frame-demo"
PYTHON_VERSION = "3.12.16"
# python-build-standalone 릴리스 태그. 바꿀 때는 아래 SHA-256 도 그 릴리스의 SHA256SUMS 값으로 바꾼다
PBS_RELEASE = "20261026"
PBS_DOWNLOAD = "https://github.com/astral-sh/python-build-standalone/releases/download"
DOCS_URL = "https://github.com/sw1029/project_videoManager/blob/dev/docs/DEMO.md"


class BundleError(Exception):
    """번들을 만들거나 확인할 수 없음 (사용자에게 그대로 보여 줄 메시지)."""


@dataclass(frozen=True)
class Target:
    triple: str  # python-build-standalone 대상 이름
    label: str  # 번들 파일 이름에 쓰는 짧은 이름
    title: str  # 사람이 읽는 이름
    sha256: str  # install_only_stripped 아카이브의 SHA-256

    @property
    def windows(self) -> bool:
        return "windows" in self.triple

    @property
    def macos(self) -> bool:
        return "apple-darwin" in self.triple

    @property
    def archive_ext(self) -> str:
        return "zip" if self.windows else "tar.gz"


TARGETS = {
    t.triple: t
    for t in (
        Target(
            "x86_64-pc-windows-msvc",
            "windows-x64",
            "Windows (x64)",
            "1f2ab53880fc67ea323398c6388e05af28addc99a8e389209f08a51fdff4b149",
        ),
        Target(
            "aarch64-apple-darwin",
            "macos-arm64",
            "macOS (Apple Silicon)",
            "353d5f1b4fd2715e7053f12f068435d5bbad16b91e057006b5d01e0925709a7e",
        ),
        Target(
            "x86_64-apple-darwin",
            "macos-x64",
            "macOS (Intel)",
            "37c38fda76fec1fbf310adcbafb7f0d96af13b5511bf8ea954c3aa8eec185734",
        ),
        Target(
            "x86_64-unknown-linux-gnu",
            "linux-x64",
            "Linux (x86_64)",
            "ce4f6d0a6cfdb3bed6e44c2f9affd85943c76a42e25ad3a259002f31d6fb8a20",
        ),
        Target(
            "aarch64-unknown-linux-gnu",
            "linux-arm64",
            "Linux (ARM64)",
            "bb4b6392670c5d3f93876996fcaa19858a4a413a0c7720144c18b0d6f1f0db2d",
        ),
    )
}

_say = launcher._say


# ---- 이름·대상 ----------------------------------------------------------------------------


def detect_target(platform: str = sys.platform, machine: str | None = None) -> Target:
    arch = {
        "amd64": "x86_64",
        "x86_64": "x86_64",
        "x64": "x86_64",
        "arm64": "aarch64",
        "aarch64": "aarch64",
    }
    cpu = arch.get((machine or pyplatform.machine()).lower())
    system = (
        "win32"
        if platform == "win32"
        else "darwin"
        if platform == "darwin"
        else ("linux" if platform.startswith("linux") else platform)
    )
    triple = {
        ("win32", "x86_64"): "x86_64-pc-windows-msvc",
        ("darwin", "aarch64"): "aarch64-apple-darwin",
        ("darwin", "x86_64"): "x86_64-apple-darwin",
        ("linux", "x86_64"): "x86_64-unknown-linux-gnu",
        ("linux", "aarch64"): "aarch64-unknown-linux-gnu",
    }.get((system, cpu or ""))
    if triple is None:
        raise BundleError(
            f"번들을 지원하지 않는 플랫폼입니다: {platform} / {machine or pyplatform.machine()}"
        )
    return TARGETS[triple]


def asset_name(target: Target) -> str:
    return f"cpython-{PYTHON_VERSION}+{PBS_RELEASE}-{target.triple}-install_only_stripped.tar.gz"


def asset_url(target: Target) -> str:
    return f"{PBS_DOWNLOAD}/{PBS_RELEASE}/{urllib.parse.quote(asset_name(target))}"


def top_dir(target: Target) -> str:
    return f"{APP_NAME}-{target.label}"


def archive_name(target: Target) -> str:
    return f"{top_dir(target)}.{target.archive_ext}"


def bundled_python(root: Path, target: Target) -> Path:
    return launcher.bundle_python(root, "win32" if target.windows else "linux")


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---- 만들기 -------------------------------------------------------------------------------


def fetch_python(target: Target, cache_dir: Path, local_archive: Path | None = None) -> Path:
    """휴대용 Python 아카이브를 (캐시에서) 가져오고 고정한 SHA-256 과 맞는지 확인한다."""
    path = Path(local_archive) if local_archive else cache_dir / asset_name(target)
    if not path.is_file():
        if local_archive:
            raise BundleError(f"휴대용 Python 아카이브가 없습니다: {path}")
        cache_dir.mkdir(parents=True, exist_ok=True)
        url = asset_url(target)
        _say(f"휴대용 Python {PYTHON_VERSION} 을 받습니다: {url}")
        partial = path.with_name(path.name + ".part")
        request = urllib.request.Request(url, headers={"User-Agent": "frame-bundle"})
        with urllib.request.urlopen(request, timeout=300) as response, partial.open("wb") as out:
            shutil.copyfileobj(response, out, 1 << 20)
        partial.replace(path)
    actual = sha256_of(path)
    if actual != target.sha256:
        if not local_archive:
            path.unlink(missing_ok=True)  # 다음 실행에서 다시 받게 한다
        raise BundleError(
            f"SHA-256 이 고정값과 다릅니다 ({path.name}: {actual}). 파일이 손상됐거나 바뀌었습니다"
        )
    return path


def extract_python(archive: Path, dest: Path) -> Path:
    """아카이브의 python/ 폴더를 dest 아래에 푼다. 경로 이탈·바깥 링크는 tarfile 'data' 필터가 막는다."""
    _require_safe_tar()
    dest.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(dest, filter="data")
    except tarfile.FilterError as exc:
        raise BundleError(f"아카이브에 안전하지 않은 항목이 있습니다: {exc}") from exc
    python_dir = dest / "python"
    if not python_dir.is_dir():
        raise BundleError(f"아카이브에 python/ 폴더가 없습니다: {archive.name}")
    return python_dir


def _require_safe_tar() -> None:
    if not hasattr(tarfile, "data_filter"):
        raise BundleError(
            "번들을 만들거나 확인하려면 Python 3.11.4 이상이 필요합니다 (tarfile 안전 필터)"
        )


def _check(command: Sequence[str], cwd: Path) -> None:
    if launcher._run(command, cwd) != 0:
        raise BundleError(f"명령이 실패했습니다: {' '.join(command)}")


def build_frontend(repo: Path, *, skip: bool) -> None:
    frontend = repo / "frontend"
    if skip:
        if not (frontend / "dist" / "index.html").is_file():
            raise BundleError(
                "frontend/dist 가 없습니다. --skip-frontend-build 없이 다시 실행하세요"
            )
        return
    if (launcher._node_major() or 0) < launcher.MIN_NODE or shutil.which("npm") is None:
        raise BundleError(
            f"화면 빌드에 Node.js {launcher.MIN_NODE} 이상이 필요합니다 (번들을 만드는 기기에만)"
        )
    _check(launcher.npm_install_command(frontend), frontend)
    _check(["npm", "run", "build"], frontend)


def install_app(python: Path, repo: Path) -> None:
    """동봉 Python 에 FRAME 과 의존성([llm] 포함)을 일반 설치한다 (편집 모드가 아니므로 저장소 없이 동작)."""
    try:
        has_pip = (
            subprocess.run([str(python), "-m", "pip", "--version"], capture_output=True).returncode
            == 0
        )
    except OSError as exc:
        raise BundleError(
            f"동봉 Python 을 이 기기에서 실행할 수 없습니다 ({exc}). 대상 OS·CPU 에서 만드세요"
        ) from exc
    if not has_pip:
        _check([str(python), "-m", "ensurepip", "--upgrade"], repo)
    pip = [
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-warn-script-location",
        "--no-input",
    ]
    _check([str(python), "-I", *pip, "--no-compile", ".[llm]"], repo)


def prune(root: Path, target: Target) -> None:
    """옮기면 깨지는 실행 스크립트(pip·uvicorn 등, 빌드 경로가 박힘)·C 헤더·__pycache__ 를 뺀다.

    __pycache__ 는 첫 실행 때 다시 만들어진다. 빼면 파일 수가 약 40% 줄어 압축 풀기가 빨라지고, 가장 긴 경로가
    짧아져 Windows 경로 길이 제한(MAX_PATH 260자)에 덜 걸린다. python 실행 파일은 남긴다.
    """
    python_dir = root / "python"
    _rmtree(python_dir / "include")
    for cache in sorted(python_dir.rglob("__pycache__"), reverse=True):
        _rmtree(cache)
    if target.windows:
        _rmtree(python_dir / "Scripts")
        return
    bin_dir = python_dir / "bin"
    if bin_dir.is_dir():
        for entry in bin_dir.iterdir():
            if not entry.name.startswith("python"):
                entry.unlink()


# 번들 안 가장 긴 경로(최상위 폴더 포함)의 상한. Windows 는 전체 경로가 260자를 넘으면 압축 풀기·import 가 실패하므로
# 압축을 풀 위치(예: C:\Users\이름\Downloads\폴더\)에 약 100자를 남긴다. 의존성이 더 깊어지면 빌드가 멈춰 알린다.
WINDOWS_PATH_BUDGET = 160


def longest_path(stage: Path) -> str:
    top = stage.name
    return max(
        (f"{top}/{p.relative_to(stage).as_posix()}" for p in stage.rglob("*")), key=len, default=top
    )


def check_path_budget(stage: Path, target: Target, budget: int = WINDOWS_PATH_BUDGET) -> int:
    longest = longest_path(stage)
    _say(f"가장 긴 경로 {len(longest)}자: {longest}")
    if target.windows and len(longest) > budget:
        raise BundleError(
            f"번들 안 경로가 {len(longest)}자로 상한 {budget}자를 넘습니다 (Windows MAX_PATH 위험): {longest}"
        )
    return len(longest)


_CMD_TEMPLATE = [
    "@echo off",
    "rem FRAME demo portable bundle (Python included, nothing to install). @PURPOSE@",
    "setlocal",
    'cd /d "%~dp0"',
    r'"%~dp0python\python.exe" -I -m frame.tools.demo --root . @COMMAND@ %*',
    "exit /b %errorlevel%",
]

_SH_TEMPLATE = r"""#!/bin/sh
# FRAME 시연 번들 (Python 동봉, 설치 불필요). @PURPOSE@
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
PY="$DIR/python/bin/python3"
if [ "$(uname -s)" = "Darwin" ] && xattr -p com.apple.quarantine "$PY" >/dev/null 2>&1; then
  echo "[frame-demo] 인터넷에서 받은 파일이라 macOS 가 이 폴더를 격리했습니다."
  echo "[frame-demo] 동봉 Python 은 Apple 공증을 받지 않아 격리 속성을 지워야 실행됩니다. 직접 하려면:"
  printf '    xattr -dr com.apple.quarantine "%s"\n' "$DIR"
  printf '[frame-demo] 이 폴더에서만 지울까요? [y/N] '
  read -r answer || answer=""
  case "$answer" in
    y|Y|yes|YES) xattr -dr com.apple.quarantine "$DIR" ;;
    *) echo "[frame-demo] 취소했습니다. 위 명령을 실행한 뒤 다시 시도하세요."; exit 1 ;;
  esac
fi
cd "$DIR"
exec "$PY" -I -m frame.tools.demo --root "$DIR" @COMMAND@ "$@"
"""

_PURPOSE = {
    "run": "Options: --offline --lan --port N --reset --no-browser",
    "setup": "Optional: saves an OpenAI key to .demo/openai.key (--key-file PATH)",
}


def write_wrappers(root: Path, target: Target) -> None:
    for command, purpose in _PURPOSE.items():
        if target.windows:  # cmd.exe 는 콘솔 코드 페이지로 읽으므로 ASCII + CRLF
            lines = [
                line.replace("@COMMAND@", command).replace("@PURPOSE@", purpose)
                for line in _CMD_TEMPLATE
            ]
            (root / f"{command}.cmd").write_bytes(("\r\n".join(lines) + "\r\n").encode("ascii"))
        else:
            text = _SH_TEMPLATE.replace("@COMMAND@", command).replace("@PURPOSE@", purpose)
            path = root / f"{command}.sh"
            path.write_bytes(text.encode("utf-8"))
            path.chmod(0o755)


def write_readme(root: Path, target: Target) -> None:
    if target.windows:
        run, setup, key_path = "run.cmd", "setup.cmd", r".demo\openai.key"
        first_run = [
            "처음 실행할 때",
            '- "게시자를 확인할 수 없습니다" 경고가 나오면 [실행] 을 누릅니다.',
            "  받은 zip 파일 속성에서 '차단 해제' 를 체크한 뒤 압축을 풀면 경고가 나오지 않습니다.",
        ]
        start = "run.cmd 를 더블클릭합니다 (또는 명령 프롬프트에서 run.cmd)."
        doctor = r"python\python.exe -I -m frame.tools.demo --root . doctor"
    else:
        run, setup, key_path = "sh run.sh", "sh setup.sh", ".demo/openai.key (소유자만 읽기 가능)"
        start = "터미널에서 이 폴더로 이동해  sh run.sh"
        doctor = "python/bin/python3 -I -m frame.tools.demo --root . doctor"
        first_run = ["처음 실행할 때"]
        if target.macos:
            first_run += [
                "- 브라우저로 받은 파일에는 격리 속성이 붙어 동봉 Python 이 막힙니다 (Apple 공증 없음).",
                "  run.sh 가 이 폴더의 격리 속성을 지울지 묻습니다. y 를 누르면 됩니다.",
                "  직접 하려면:  xattr -dr com.apple.quarantine <이 폴더>",
                "  터미널에서 curl 로 받으면 격리 속성이 붙지 않습니다.",
            ]
        else:
            first_run += [
                "- glibc 기반 배포판용입니다 (Ubuntu 22.04 이상 등). Alpine(musl)은 지원하지 않습니다.",
            ]
    lines = [
        f"FRAME 시연 번들 · {target.title} · Python {PYTHON_VERSION} 동봉 (설치 불필요)",
        "",
        f"1. 실행: {start}",
        "   브라우저가 http://127.0.0.1:8000 으로 열립니다. 끝내려면 터미널(검은 창)에서 Ctrl+C.",
        "2. OpenAI 키: 화면 '01 · 새 프로젝트 생성'의 'OpenAI API 키' 칸에 넣습니다.",
        "   키는 브라우저에 저장하지 않고 서버 메모리에만 두며, 다시 시작하면 다시 넣어야 합니다.",
        f"   키를 저장해 두려면 {setup} 를 실행합니다 → {key_path}",
        f"3. 키·인터넷 없이 시연: {run} --offline   (DSL 프리셋)",
        "",
        *first_run,
        "- 압축은 바탕화면처럼 쓰기 가능한 폴더에 풉니다. DB·결과는 이 폴더의 .demo 에 저장됩니다.",
        *(
            [
                "- '파일 이름이 너무 깁니다' 오류가 나면 C:\\frame 처럼 짧은 경로에 푸세요 (Windows 경로 길이 제한)."
            ]
            if target.windows
            else []
        ),
        "",
        "옵션: --offline  --lan (같은 네트워크 기기에서 보기, 인증 없음)  --port 8010  --reset  --no-browser",
        f"환경 점검: {doctor}",
        f"자세한 안내: {DOCS_URL}",
        "",
    ]
    text = "\n".join(lines)
    (root / "README.txt").write_text(
        text.replace("\n", "\r\n") if target.windows else text, encoding="utf-8"
    )


def _frame_version(repo: Path) -> str:
    match = re.search(
        r'__version__\s*=\s*"([^"]+)"', (repo / "src" / "frame" / "__init__.py").read_text("utf-8")
    )
    return match.group(1) if match else "unknown"


def _git_sha(repo: Path) -> str:
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=repo,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return f"{sha}+dirty" if dirty else sha


def write_metadata(root: Path, target: Target, repo: Path) -> None:
    info = {
        "name": APP_NAME,
        "label": target.label,
        "target": target.triple,
        "frame_version": _frame_version(repo),
        "git_sha": _git_sha(repo),
        "python": PYTHON_VERSION,
        "python_build": f"python-build-standalone {PBS_RELEASE}",
        "built_at": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    (root / launcher.BUNDLE_MARKER).write_text(
        json.dumps(info, ensure_ascii=False, indent=2) + "\n", "utf-8"
    )


def make_archive(stage: Path, out_dir: Path, target: Target) -> Path:
    """stage 폴더를 한 개의 최상위 폴더로 묶는다 (Windows: zip, macOS·Linux: 실행 권한·링크를 지키는 tar.gz)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / archive_name(target)
    archive.unlink(missing_ok=True)
    top = stage.name
    if target.windows:
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for path in sorted(stage.rglob("*")):
                if path.is_file():
                    zf.write(path, f"{top}/{path.relative_to(stage).as_posix()}")
    else:

        def normalize(info: tarfile.TarInfo) -> tarfile.TarInfo:
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            return info

        with tarfile.open(archive, "w:gz", compresslevel=6) as tar:
            tar.add(stage, arcname=top, filter=normalize)
    (out_dir / f"{archive.name}.sha256").write_text(
        f"{sha256_of(archive)}  {archive.name}\n", "utf-8"
    )
    return archive


def build(
    repo: Path,
    target: Target,
    *,
    out_dir: Path,
    work_dir: Path,
    python_archive: Path | None = None,
    skip_frontend_build: bool = False,
) -> Path:
    runtime = fetch_python(target, work_dir / "cache", local_archive=python_archive)
    build_frontend(repo, skip=skip_frontend_build)
    stage = work_dir / "stage" / top_dir(target)
    _rmtree(stage)
    extract_python(runtime, stage)
    _say(f"FRAME 과 의존성을 동봉 Python 에 설치합니다 ({target.title})")
    install_app(bundled_python(stage, target), repo)
    shutil.copytree(repo / "frontend" / "dist", stage / "frontend" / "dist")
    prune(stage, target)
    write_wrappers(stage, target)
    write_readme(stage, target)
    write_metadata(stage, target, repo)
    check_path_budget(stage, target)
    archive = make_archive(stage, out_dir, target)
    _say(f"완성: {archive} ({archive.stat().st_size / 1e6:.1f} MB)")
    return archive


# ---- 확인 ---------------------------------------------------------------------------------


def minimal_env() -> dict[str, str]:
    """Python·Node 가 설치되지 않은 기기처럼: PATH 는 시스템 폴더만 두고 PYTHON*·가상환경 설정은 뺀다."""
    if os.name == "nt":
        keep = {
            "SYSTEMROOT",
            "WINDIR",
            "SYSTEMDRIVE",
            "COMSPEC",
            "PATHEXT",
            "TEMP",
            "TMP",
            "USERPROFILE",
            "LOCALAPPDATA",
            "APPDATA",
            "PROGRAMDATA",
            "HOMEDRIVE",
            "HOMEPATH",
            "USERNAME",
        }
        env = {k: v for k, v in os.environ.items() if k.upper() in keep}
        system_root = os.environ.get("SystemRoot", "C:\\Windows")
        env["PATH"] = f"{system_root}\\System32;{system_root};{system_root}\\System32\\Wbem"
        return env
    env = {k: v for k, v in os.environ.items() if k in {"HOME", "USER", "TMPDIR", "LANG", "LC_ALL"}}
    env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
    return env


def _extract_bundle(archive: Path, dest: Path) -> None:
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(dest)
    else:
        _require_safe_tar()
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(dest, filter="data")


def verify(path: Path, target: Target) -> int:
    """번들을 임시 폴더에 풀고, 저장소·다른 Python·Node 없이 래퍼와 자동 점검(smoke)이 통과하는지 본다."""
    archive = path / archive_name(target) if path.is_dir() else path
    if not archive.is_file():
        raise BundleError(f"번들 파일이 없습니다: {archive}")
    checksum = archive.parent / f"{archive.name}.sha256"
    expected = checksum.read_text("utf-8").split()[0] if checksum.is_file() else None
    if expected != sha256_of(archive):
        raise BundleError(f"SHA-256 이 .sha256 파일과 다릅니다: {archive.name}")
    failures: list[str] = []

    def check(ok: bool, label: str) -> None:
        _say(f"{'통과' if ok else '실패'}: {label}")
        if not ok:
            failures.append(label)

    with tempfile.TemporaryDirectory(
        prefix="frame-bundle-verify-", ignore_cleanup_errors=True
    ) as tmp:
        _extract_bundle(archive, Path(tmp))
        root = (Path(tmp) / top_dir(target)).resolve()
        python = bundled_python(root, target)
        env = minimal_env()
        code = "import frame, platform; print(platform.python_version()); print(frame.__file__)"
        probe = subprocess.run(
            [str(python), "-I", "-c", code], capture_output=True, text=True, env=env, cwd=root
        )
        lines = probe.stdout.splitlines()
        inside = (
            probe.returncode == 0
            and len(lines) == 2
            and Path(lines[1]).resolve().is_relative_to(root)
        )
        check(
            inside and lines[0] == PYTHON_VERSION,
            f"동봉 Python {PYTHON_VERSION} 이 번들 안의 FRAME 을 사용",
        )
        wrapper = (
            [os.environ.get("COMSPEC", "cmd.exe"), "/c", str(root / "run.cmd"), "--help"]
            if target.windows
            else ["sh", str(root / "run.sh"), "--help"]
        )
        check(
            subprocess.run(wrapper, capture_output=True, env=env, cwd=root).returncode == 0,
            "실행 래퍼",
        )
        smoke = [str(python), "-I", "-m", "frame.tools.demo", "--root", str(root), "smoke"]
        check(
            subprocess.run(smoke, env=env, cwd=root).returncode == 0,
            "자동 점검 (화면·API·구조화·키 입력)",
        )
    if failures:
        raise BundleError(f"번들 점검 실패 {len(failures)}건: {', '.join(failures)}")
    _say(f"번들 점검 통과: {archive.name}")
    return 0


# ---- 공통 ---------------------------------------------------------------------------------


def _rmtree(path: Path) -> None:
    """읽기 전용 파일(Windows)도 지운다."""

    def retry(func, target, _exc) -> None:  # noqa: ANN001 - shutil 콜백 형식
        os.chmod(target, stat.S_IWRITE)
        func(target)

    if not path.exists():
        return
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=retry)
    else:  # 3.11: onerror 의 세 번째 인자는 exc_info 튜플이지만 쓰지 않는다
        shutil.rmtree(path, onerror=retry)


def main(argv: Sequence[str] | None = None, *, root: Path) -> int:
    launcher._utf8_output()
    parser = argparse.ArgumentParser(prog="frame_bundle", description="OS별 시연 번들 만들기·확인")
    sub = parser.add_subparsers(dest="command", required=True)
    build_cmd = sub.add_parser("build", help="지금 OS·CPU 용 번들을 만든다")
    build_cmd.add_argument("--target", choices=sorted(TARGETS), help="기본: 지금 OS·CPU")
    build_cmd.add_argument("--out", type=Path, help="번들을 둘 폴더 (기본: 저장소의 dist/)")
    build_cmd.add_argument(
        "--python-archive", type=Path, help="미리 받은 휴대용 Python 아카이브 (SHA-256 은 확인)"
    )
    build_cmd.add_argument(
        "--skip-frontend-build", action="store_true", help="있는 frontend/dist 를 그대로 사용"
    )
    verify_cmd = sub.add_parser(
        "verify", help="만든 번들을 임시 폴더에 풀어 저장소 없이 실행되는지 확인"
    )
    verify_cmd.add_argument("path", type=Path, help="번들 파일 또는 그 파일이 있는 폴더")
    verify_cmd.add_argument("--target", choices=sorted(TARGETS), help="기본: 지금 OS·CPU")
    sub.add_parser("targets", help="지원 대상 목록")
    args = parser.parse_args(argv)
    try:
        if args.command == "targets":
            for target in TARGETS.values():
                print(
                    f"{target.label:12s} {target.title:22s} {target.triple:28s} {archive_name(target)}"
                )
            return 0
        target = TARGETS[args.target] if args.target else detect_target()
        if args.command == "build":
            build(
                root,
                target,
                out_dir=(args.out or root / "dist").resolve(),
                work_dir=root / "build" / "frame-bundle",
                python_archive=args.python_archive,
                skip_frontend_build=args.skip_frontend_build,
            )
            return 0
        return verify(args.path.resolve(), target)
    except BundleError as exc:
        return launcher._fail(str(exc))
