"""시연 런처(frame.tools.demo)의 OS 별 분기와 환경 구성. 실제 설치·서버 기동은 CI 의 demo-smoke 가 맡는다."""

import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

from frame.config import Settings
from frame.tools import demo

KEY = "sk-test-" + "a" * 24


def _make_bundle(root: Path) -> Path:
    """압축을 푼 번들 모양: 표식 파일 + 동봉 python + 빌드된 화면."""
    info = {"label": "windows-x64", "python": "3.12.16", "frame_version": "0.1.0"}
    (root / demo.BUNDLE_MARKER).write_text(json.dumps(info), encoding="utf-8")
    python = demo.bundle_python(root)
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("", encoding="utf-8")
    (root / "frontend" / "dist").mkdir(parents=True)
    (root / "frontend" / "dist" / "index.html").write_text("<title>FRAME</title>", encoding="utf-8")
    return python


def test_bundle_python_layout_per_os(tmp_path):
    assert demo.bundle_python(tmp_path, "win32") == tmp_path / "python" / "python.exe"
    assert demo.bundle_python(tmp_path, "darwin") == tmp_path / "python" / "bin" / "python3"
    assert demo.bundle_python(tmp_path, "linux") == tmp_path / "python" / "bin" / "python3"


def test_runtime_python_prefers_the_bundled_runtime(tmp_path):
    assert not demo.is_bundle(tmp_path)
    assert demo.runtime_python(tmp_path) == demo.venv_python(tmp_path)
    python = _make_bundle(tmp_path)
    assert demo.is_bundle(tmp_path)
    assert demo.runtime_python(tmp_path) == python


def test_bundle_is_ready_without_venv_or_node(tmp_path):
    _make_bundle(tmp_path)
    assert demo._ready_to_run(tmp_path) is None


def test_broken_bundle_asks_to_extract_again(tmp_path):
    (tmp_path / demo.BUNDLE_MARKER).write_text("{}", encoding="utf-8")
    assert "압축" in demo._ready_to_run(tmp_path)


def test_setup_in_a_bundle_only_stores_the_key(tmp_path, monkeypatch, capsys):
    _make_bundle(tmp_path)
    monkeypatch.setattr(
        demo, "_run", lambda *a, **k: pytest.fail("번들에서는 설치 명령을 실행하지 않는다")
    )
    source = tmp_path / "key.txt"
    source.write_text(KEY, encoding="utf-8")
    assert demo.cmd_setup(tmp_path, key_file=str(source), no_key=False, skip_frontend=False) == 0
    assert demo.key_file_path(tmp_path).read_text(encoding="utf-8") == KEY + "\n"
    out = capsys.readouterr().out
    assert "설치가 필요 없습니다" in out and ("run.cmd" in out or "run.sh" in out)


def test_hints_point_at_bundle_wrappers():
    assert demo.runner_hint("win32", bundle=True) == "run.cmd"
    assert demo.runner_hint("linux", bundle=True) == "sh run.sh"
    assert demo.setup_hint("darwin", bundle=True) == "sh setup.sh"
    assert demo.setup_hint("win32", bundle=True) == "setup.cmd"


def test_root_option_runs_doctor_for_a_bundle(tmp_path, capsys):
    _make_bundle(tmp_path)
    assert demo.main(["--root", str(tmp_path), "doctor"]) == 0
    out = capsys.readouterr().out
    assert "번들" in out and "windows-x64" in out and "3.12.16" in out


@pytest.mark.parametrize(
    ("platform", "expected"),
    [("win32", "win32"), ("darwin", "darwin"), ("linux", "linux"), ("freebsd14", "linux")],
)
def test_platform_key(platform, expected):
    assert demo.platform_key(platform) == expected


def test_venv_python_layout_per_os(tmp_path):
    assert demo.venv_python(tmp_path, "win32") == tmp_path / ".venv" / "Scripts" / "python.exe"
    assert demo.venv_python(tmp_path, "darwin") == tmp_path / ".venv" / "bin" / "python"
    assert demo.venv_python(tmp_path, "linux") == tmp_path / ".venv" / "bin" / "python"


def test_foreign_venv_is_detected_so_setup_never_wipes_it(tmp_path):
    assert not demo.foreign_venv(tmp_path, "linux") and not demo.foreign_venv(tmp_path, "win32")
    (tmp_path / ".venv" / "Scripts").mkdir(parents=True)  # Windows 에서 만든 .venv
    assert demo.foreign_venv(tmp_path, "linux") and demo.foreign_venv(tmp_path, "darwin")
    assert not demo.foreign_venv(tmp_path, "win32")
    (tmp_path / ".venv" / "bin").mkdir()
    assert not demo.foreign_venv(tmp_path, "linux")


def test_setup_stops_on_a_foreign_venv_without_touching_it(tmp_path, monkeypatch, capsys):
    other = "linux" if demo.platform_key() == "win32" else "win32"
    (demo.venv_python(tmp_path, other).parent).mkdir(parents=True)
    marker = demo.venv_python(tmp_path, other).parent / "keep.txt"
    marker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(demo, "_run", lambda *a, **k: pytest.fail("명령을 실행하면 안 된다"))
    assert demo.cmd_setup(tmp_path, key_file=None, no_key=True, skip_frontend=True) == 1
    assert "다른 운영체제" in capsys.readouterr().err
    assert marker.is_file()


def test_sqlite_url_for_windows_and_posix_paths():
    windows = PureWindowsPath(r"C:\Users\me\FRAME\.demo\frame.db")
    posix = PurePosixPath("/home/me/FRAME/.demo/frame.db")
    assert demo.sqlite_url(windows) == "sqlite+pysqlite:///C:/Users/me/FRAME/.demo/frame.db"
    assert demo.sqlite_url(posix) == "sqlite+pysqlite:////home/me/FRAME/.demo/frame.db"


def test_sqlite_url_opens_a_file_database_on_this_os(tmp_path):
    from sqlalchemy import create_engine, text

    folder = tmp_path / "시연 폴더"  # 한글·공백이 든 사용자 경로 (예: C:\Users\홍길동\...)
    folder.mkdir()
    engine = create_engine(demo.sqlite_url(folder / "frame.db"))
    with engine.connect() as connection:
        assert connection.execute(text("select 1")).scalar() == 1
    engine.dispose()
    assert (folder / "frame.db").is_file()


def test_version_checks():
    assert demo.python_supported((3, 11, 0)) and demo.python_supported((3, 13, 2))
    assert not demo.python_supported((3, 10, 12))
    assert demo.parse_node_major("v20.11.1\n") == 20
    assert demo.parse_node_major("18.19.0") == 18
    assert demo.parse_node_major("") is None


def test_extract_key_from_bare_or_assignment_lines():
    assert demo.extract_key(KEY + "\n") == KEY
    assert demo.extract_key(f"OPENAI_API_KEY={KEY}\r\n") == KEY
    assert demo.extract_key("sk-short") is None
    assert demo.extract_key("") is None


def test_extract_key_never_takes_another_services_key():
    """키 파일에 다른 서비스의 키가 함께 있어도 OpenAI 키만 집는다 (감사 A-25)."""
    other = "sk-or-v1-" + "f00d" * 12

    assert demo.extract_key(f"OPENROUTER_API_KEY={other}\nOPENAI_API_KEY={KEY}\n") == KEY
    assert demo.extract_key(f"OPENROUTER_API_KEY={other}\n") is None
    assert demo.extract_key(other) is None  # OpenRouter 키는 OpenAI 키 자리에 쓸 수 없다
    assert demo.extract_key("OPENAI_API_KEY=sk-short\n") is None
    assert demo.extract_key(f"DB_PASSWORD=hunter2\n# {KEY}\n") is None


def test_the_launcher_runs_on_the_standard_library_alone():
    """의존성을 설치하기 전에도 실행된다. site-packages 없이 import 되는지 본다 (다른 모듈을 끌어오면 깨진다)."""
    src = Path(demo.__file__).resolve().parents[2]
    code = f"import sys; sys.path.insert(0, {str(src)!r}); import frame.tools.demo"

    done = subprocess.run(
        [sys.executable, "-S", "-c", code], capture_output=True, text=True, check=False
    )

    assert done.returncode == 0, done.stderr[-400:]


def test_npm_install_prefers_ci_with_lockfile(tmp_path):
    assert demo.npm_install_command(tmp_path)[:2] == ["npm", "install"]
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")
    assert demo.npm_install_command(tmp_path)[:2] == ["npm", "ci"]


def test_llm_demo_env_enables_local_key_entry_and_keeps_state_in_repo(tmp_path):
    key_file = tmp_path / ".demo" / "openai.key"
    env = demo.demo_env(
        tmp_path,
        offline=False,
        base_env={"PATH": "x", "FRAME_LLM_MODEL": "gpt-x"},
        key_file=key_file,
    )
    assert env["FRAME_STRUCTURER"] == "llm"
    assert env["FRAME_LLM_RUNTIME_KEY"] == "true"
    assert "FRAME_LLM_RUNTIME_KEY_ALLOW_REMOTE" not in env  # 다른 기기의 키 입력은 절대 켜지 않는다
    assert env["FRAME_LLM_KEY_FILE"] == str(key_file)
    assert env["FRAME_JOBS"] == "inline"
    assert env["FRAME_DB_URL"] == demo.sqlite_url(tmp_path / ".demo" / "frame.db")
    assert env["FRAME_ARTIFACT_DIR"] == str(tmp_path / ".demo" / "artifacts")
    assert env["FRAME_FRONTEND_DIST"] == str(tmp_path / "frontend" / "dist")
    assert env["PATH"] == "x" and env["FRAME_LLM_MODEL"] == "gpt-x"  # 사용자 설정은 그대로 둔다


def test_offline_demo_env_needs_no_key(tmp_path):
    env = demo.demo_env(tmp_path, offline=True, base_env={}, key_file=tmp_path / "k")
    assert env["FRAME_STRUCTURER"] == "rule_based"
    assert "FRAME_LLM_RUNTIME_KEY" not in env and "FRAME_LLM_KEY_FILE" not in env


def test_lan_mode_adds_rate_limit_unless_user_set_one(tmp_path):
    def env(lan, base):
        return demo.demo_env(tmp_path, offline=True, lan=lan, base_env=base, key_file=None)

    assert env(True, {})["FRAME_RATE_LIMIT_PER_MINUTE"] == demo.LAN_RATE_LIMIT_PER_MINUTE
    assert env(True, {"FRAME_RATE_LIMIT_PER_MINUTE": "5"})["FRAME_RATE_LIMIT_PER_MINUTE"] == "5"
    assert "FRAME_RATE_LIMIT_PER_MINUTE" not in env(False, {})


@pytest.mark.parametrize("inherited", ["0", "", "  ", "-3", "off"])
def test_lan_mode_never_opens_with_the_rate_limit_switched_off(tmp_path, inherited):
    # 셸에 남아 있던 값(0 은 "꺼짐")이 인증 없는 서버를 제한 없이 네트워크에 열지 못한다
    env = demo.demo_env(
        tmp_path,
        offline=True,
        lan=True,
        base_env={"FRAME_RATE_LIMIT_PER_MINUTE": inherited},
        key_file=None,
    )
    assert env["FRAME_RATE_LIMIT_PER_MINUTE"] == demo.LAN_RATE_LIMIT_PER_MINUTE
    assert Settings.from_env(env).rate_limit_per_minute == int(demo.LAN_RATE_LIMIT_PER_MINUTE)


def test_lan_mode_keeps_key_entry_on_this_machine_whatever_the_shell_says(tmp_path):
    inherited = {"FRAME_LLM_RUNTIME_KEY_ALLOW_REMOTE": "true"}

    def settings(lan):
        env = demo.demo_env(tmp_path, offline=False, lan=lan, base_env=inherited, key_file=None)
        return Settings.from_env(env)

    assert not settings(True).llm_runtime_key_allow_remote
    # 이 기기에만 열 때는 다른 기기가 닿지 못하므로 셸의 값을 건드리지 않는다
    assert settings(False).llm_runtime_key_allow_remote


def test_lan_notes_say_what_the_server_is_opened_with(tmp_path):
    def notes(base):
        env = demo.demo_env(tmp_path, offline=True, lan=True, base_env=base, key_file=None)
        return "\n".join(demo.lan_notes(env))

    plain = notes({})
    assert "분당 30회" in plain and "이 기기에서만" in plain
    assert "FRAME_GENERATOR" not in plain
    assert "분당 5회" in notes({"FRAME_RATE_LIMIT_PER_MINUTE": "5"})
    # 셸에서 물려받은 실제 생성기는 다른 기기의 요청으로도 돈다 - 여는 사람이 보게 한다
    paid = notes({"FRAME_GENERATOR": "external_api"})
    assert "external_api" in paid and "FRAME_GENERATOR" in paid
    assert "FRAME_GENERATOR" not in notes({"FRAME_GENERATOR": "fake"})


def test_the_launchers_copy_of_the_default_generator_matches_settings():
    # 런처는 의존성 설치 전에도 돌아야 해서 frame.config 를 쓰지 못하고 기본값을 따로 적어 둔다
    assert Settings.from_env({}).generator == demo.DEFAULT_GENERATOR


def test_demo_env_is_understood_by_frame_settings(tmp_path):
    env = demo.demo_env(tmp_path, offline=False, lan=True, base_env={}, key_file=None)
    settings = Settings.from_env(env)
    assert settings.structurer == "llm" and settings.llm_provider == "openai"
    assert settings.llm_runtime_key and not settings.llm_runtime_key_allow_remote
    assert settings.persistence == "sqlalchemy" and settings.artifact_store == "local_fs"
    assert settings.jobs == "inline" and settings.rate_limit_per_minute == 30
    assert settings.frontend_dist == str(tmp_path / "frontend" / "dist")


def test_uvicorn_command_uses_the_app_factory_in_an_isolated_runtime(tmp_path):
    command = demo.uvicorn_command(tmp_path / "python", "127.0.0.1", 8000)
    # -I: 다른 Python 의 PYTHONHOME·PYTHONPATH 가 동봉 런타임을 깨지 않게, -X utf8: 한글 출력
    assert command[1:7] == ["-I", "-X", "utf8", "-m", "uvicorn", "--factory"]
    assert "frame.presentation.main:create_default_app" in command
    assert command[-4:] == ["--host", "127.0.0.1", "--port", "8000"]


def test_browser_opens_on_linux_only_with_a_display():
    assert demo.can_open_browser("win32", {}) and demo.can_open_browser("darwin", {})
    assert not demo.can_open_browser("linux", {})
    assert demo.can_open_browser("linux", {"DISPLAY": ":0"})
    assert demo.can_open_browser("linux", {"WAYLAND_DISPLAY": "wayland-0"})


def test_wrapper_hints_per_os():
    assert demo.runner_hint("win32") == r"scripts\run.cmd"
    assert demo.setup_hint("win32") == r"scripts\setup.cmd"
    assert demo.runner_hint("darwin") == demo.runner_hint("linux") == "sh scripts/run.sh"
    assert demo.setup_hint("linux") == "sh scripts/setup.sh"


def test_save_key_writes_trimmed_key_owner_only(tmp_path):
    path = demo.save_key(tmp_path / ".demo", f"  {KEY}\n")
    assert path == tmp_path / ".demo" / "openai.key"
    assert path.read_text(encoding="utf-8") == KEY + "\n"
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_reset_state_removes_demo_data_but_keeps_the_key(tmp_path):
    demo_dir = tmp_path / ".demo"
    (demo_dir / "artifacts" / "v1").mkdir(parents=True)
    (demo_dir / "artifacts" / "v1" / "video.json").write_text("{}", encoding="utf-8")
    (demo_dir / "frame.db").write_text("", encoding="utf-8")
    key = demo.save_key(demo_dir, KEY)
    demo.reset_state(demo_dir)
    assert not (demo_dir / "frame.db").exists() and not (demo_dir / "artifacts").exists()
    assert key.is_file()


def test_setup_copies_a_key_file_without_printing_it(tmp_path, capsys):
    source = tmp_path / "key.txt"
    source.write_text(f"OPENAI_API_KEY={KEY}\n", encoding="utf-8")
    assert demo._store_key(tmp_path, key_file=str(source), no_key=False) == 0
    assert demo.key_file_path(tmp_path).read_text(encoding="utf-8") == KEY + "\n"
    out = capsys.readouterr()
    assert KEY not in out.out + out.err


@pytest.mark.parametrize("bundled", [False, True], ids=["repository", "bundle"])
def test_storing_a_key_says_it_stays_in_the_folder_and_travels_with_it(tmp_path, capsys, bundled):
    # 키는 폴더 안에 평문으로 남는다. 준비해 둔 폴더를 USB·드라이브로 넘기면 키가 따라간다
    if bundled:
        _make_bundle(tmp_path)
    source = tmp_path / "key.txt"
    source.write_text(KEY, encoding="utf-8")
    assert demo._store_key(tmp_path, key_file=str(source), no_key=False) == 0
    out = capsys.readouterr().out
    assert "넘기기 전에" in out and ".demo 폴더를 지우세요" in out
    assert ("git" in out) is not bundled  # 번들에는 git 이 없다


def test_doctor_reminds_that_a_stored_key_travels_with_the_folder(tmp_path, capsys):
    demo._report_key_and_port(tmp_path)
    assert "넘기기 전에" not in capsys.readouterr().out  # 저장한 키가 없으면 말하지 않는다
    demo.save_key(tmp_path / ".demo", KEY)
    demo._report_key_and_port(tmp_path)
    out = capsys.readouterr().out
    assert ".demo/openai.key 있음" in out and "넘기기 전에" in out


def test_setup_rejects_a_key_file_without_a_key(tmp_path, capsys):
    source = tmp_path / "key.txt"
    source.write_text("nothing here", encoding="utf-8")
    assert demo._store_key(tmp_path, key_file=str(source), no_key=False) == 1
    assert not demo.key_file_path(tmp_path).exists()


@pytest.mark.parametrize("command", [["run", "--no-browser"], ["smoke"]])
def test_run_and_smoke_ask_for_setup_first(tmp_path, capsys, command):
    assert demo.main(command, root=tmp_path) == 1
    assert "setup" in capsys.readouterr().err


@pytest.mark.skipif(os.name != "nt", reason="Windows 작업 개체")
def test_server_dies_with_the_launcher_on_windows():
    """런처가 강제 종료되면(작업 개체 핸들이 닫히면) 묶어 둔 서버 프로세스도 끝나야 포트가 남지 않는다."""
    import ctypes

    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        job = demo.kill_on_close_job(child.pid)
        assert child.poll() is None
        ctypes.WinDLL("kernel32").CloseHandle(ctypes.c_void_p(job))  # 런처 종료와 같은 효과
        deadline = time.monotonic() + 10
        while child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        assert child.poll() is not None
    finally:
        if child.poll() is None:
            child.kill()


class _StartingServer:
    """런처가 띄운 서버를 흉내 낸다: 가짜 시계로 ready_at 초부터 /health 에 답하고, exits_at 초에 끝난다."""

    def __init__(self, monkeypatch, *, ready_at=None, exits_at=None, exit_code=1):
        self.now = 0.0
        self.ready_at, self.exits_at, self.exit_code = ready_at, exits_at, exit_code
        monkeypatch.setattr(demo.time, "monotonic", lambda: self.now)
        monkeypatch.setattr(demo.time, "sleep", self._sleep)
        monkeypatch.setattr(demo, "_http", self._http)

    def _sleep(self, seconds):
        self.now += seconds

    def _http(self, method, url, body=None, timeout=30):
        if self.ready_at is None or self.now < self.ready_at:
            raise ConnectionRefusedError("아직 뜨지 않았다")
        return 200, b"{}"

    def poll(self):
        return self.exit_code if self.exits_at is not None and self.now >= self.exits_at else None


def test_startup_wait_returns_as_soon_as_the_server_answers(monkeypatch, capsys):
    server = _StartingServer(monkeypatch, ready_at=3)
    assert demo._startup_problem("http://127.0.0.1:8000", server, slow_after=15) is None
    assert server.now < 4
    assert capsys.readouterr().out == ""  # 금방 뜨면 아무 안내도 하지 않는다


def test_a_slow_startup_says_once_why_and_how_to_skip_it_then_keeps_waiting(monkeypatch, capsys):
    # 지난 실행이 남긴 분석을 기동 중에 다시 실행하면 (FRAME_JOBS=inline) LLM 응답을 기다리느라 90초를 넘긴다.
    # 예전에는 90초에 서버를 죽였고, 남은 분석은 다음 실행에서 또 같은 일을 했다
    server = _StartingServer(monkeypatch, ready_at=130)
    problem = demo._startup_problem(
        "http://127.0.0.1:8000", server, timeout=demo.RECOVERY_WAIT_S, slow_after=15
    )
    assert problem is None
    out = capsys.readouterr().out
    assert out.count("아직 준비 중") == 1
    assert "--offline" in out and "--reset" in out and "Ctrl+C" in out


def test_startup_wait_gives_up_with_a_way_out_when_the_server_never_answers(monkeypatch, capsys):
    server = _StartingServer(monkeypatch)
    problem = demo._startup_problem("http://127.0.0.1:8000", server, timeout=300, slow_after=15)
    assert server.now >= 300
    assert "300초" in problem and "--offline" in problem and "--reset" in problem


def test_a_server_that_exits_during_startup_is_reported_with_its_exit_code(monkeypatch, capsys):
    server = _StartingServer(monkeypatch, exits_at=2, exit_code=3)
    problem = demo._startup_problem("http://127.0.0.1:8000", server, timeout=300, slow_after=15)
    assert server.now < 3  # 끝난 서버를 기다리지 않는다
    assert "종료 코드 3" in problem and "서버 로그" in problem
    assert "--reset" not in problem  # 남은 분석 탓이 아니다. DB 를 지우라고 하지 않는다


def test_run_waits_long_enough_for_one_leftover_analysis_at_startup():
    # LLM 호출 하나: 제한 시간 x (SDK 재시도 1회 + 1). 그보다 짧게 기다리면 남은 분석이 실행마다 되풀이된다
    one_call = Settings.from_env({}).llm_timeout_s * 2
    assert one_call < demo.RECOVERY_WAIT_S
    assert demo.SLOW_START_NOTICE_S < demo.HEALTH_WAIT_S < demo.RECOVERY_WAIT_S


def test_a_command_is_required(tmp_path):
    with pytest.raises(SystemExit):
        demo.main([], root=tmp_path)


def test_entry_script_points_at_the_repository_root():
    script = Path(__file__).resolve().parents[2] / "scripts" / "frame_demo.py"
    assert script.is_file()
    assert "parents[1]" in script.read_text(encoding="utf-8")
