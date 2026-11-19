"""시연 실행기의 API 키 도우미: 키 파일 · 상태 · 온라인 확인(demo_keys)과 대화형 설정 · keys 명령(demo_key_guide).

네트워크는 부르지 않는다: 온라인 확인에는 정해진 응답을 돌려주는 가짜 전송(HttpTransport)을 넣는다.
서버가 쓸 주소도 정해진 값을 돌려주는 함수로 넣는다 (실제로는 설치된 서버 코드에 묻는다 — demo.service_addresses).
"""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from frame.adapters.generator.openrouter import KEY_ENV as OPENROUTER_KEY_ENV
from frame.adapters.llm.keys import KEY_VARIABLE as OPENAI_KEY_ENV
from frame.adapters.llm.openai_client import OPENAI_BASE_URL
from frame.adapters.shared.http import HttpResponse, HttpTransport, TransportError, UrllibTransport
from frame.config import Settings
from frame.tools import demo_key_guide, demo_keys
from frame.tools.demo_keys import OPENAI, OPENROUTER

OPENAI_KEY = "sk-proj-" + "A" * 24 + "1234"
ROUTER_KEY = "sk-or-v1-" + "b" * 24 + "cdef"
HINTS = demo_key_guide.Hints(run="run.cmd", keys="keys.cmd", prepare_video="ffmpeg 준비")
ADDRESSES = {"openai": "https://llm.example/v1", "openrouter": "https://video.example/api/v1"}
SRC = Path(__file__).resolve().parents[2] / "src"
INVALID_KEY = {"error": {"message": "Incorrect API key provided", "code": "invalid_api_key"}}


class ScriptedConsole:
    """정해진 답을 차례로 돌려주는 입출력. 답이 떨어지면 입력이 끝난 것(EOF)으로 본다."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.lines: list[str] = []
        self.prompts: list[str] = []

    def _next(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)

    @property
    def console(self) -> demo_key_guide.Console:
        return demo_key_guide.Console(
            say=self.lines.append,
            error=lambda message: self.lines.append(f"오류: {message}"),
            ask=self._next,
            ask_secret=self._next,
        )

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


class FakeTransport(HttpTransport):
    """가짜 전송: 받은 요청을 calls 에 남기고 정해진 응답(또는 예외)을 돌려준다."""

    def __init__(
        self, status: int = 200, body: object = None, *, error: BaseException | None = None
    ) -> None:
        self.status = status
        self.body = body
        self.error = error
        self.calls: list[tuple[str, dict[str, str], float]] = []

    def request(self, method, url, *, headers=None, body=None, timeout=60.0) -> HttpResponse:
        self.calls.append((url, dict(headers or {}), timeout))
        if self.error is not None:
            raise self.error
        payload = json.dumps({} if self.body is None else self.body).encode("utf-8")
        return HttpResponse(self.status, payload)


def _online(
    transport: FakeTransport, addresses: dict[str, str] | None = ADDRESSES
) -> demo_keys.Online:
    return demo_keys.Online(addresses=lambda: addresses, transport=transport)


# ---- 키 파일 ------------------------------------------------------------------------------


def test_each_key_slot_takes_only_its_own_services_key():
    assert demo_keys.extract(OPENAI, OPENAI_KEY + "\n") == OPENAI_KEY
    assert demo_keys.extract(OPENROUTER, ROUTER_KEY + "\n") == ROUTER_KEY
    assert demo_keys.extract(OPENROUTER, f"OPENROUTER_API_KEY={ROUTER_KEY}\r\n") == ROUTER_KEY
    # 서로의 자리에 넣으면 받지 않는다: OpenRouter 키를 OpenAI 로 보내거나 그 반대가 되지 않게
    assert demo_keys.extract(OPENAI, ROUTER_KEY) is None
    assert demo_keys.extract(OPENROUTER, OPENAI_KEY) is None
    both = f"OPENAI_API_KEY={OPENAI_KEY}\nOPENROUTER_API_KEY={ROUTER_KEY}\n"
    assert demo_keys.extract(OPENAI, both) == OPENAI_KEY
    assert demo_keys.extract(OPENROUTER, both) == ROUTER_KEY
    assert demo_keys.extract(OPENROUTER, "sk-or-short") is None
    # 잘못 넣었을 때 어디에 넣을지 알릴 수 있다
    assert demo_keys.other_kind(OPENAI, ROUTER_KEY) is OPENROUTER
    assert demo_keys.other_kind(OPENROUTER, OPENAI_KEY) is OPENAI
    assert demo_keys.other_kind(OPENAI, "nothing") is None


def test_an_openai_key_is_found_even_when_an_openrouter_key_is_written_first():
    """메모 파일에 OpenRouter 키가 먼저 적혀 있어도 그 뒤의 OpenAI 키를 찾는다 (이름 없는 줄 둘)."""
    note = f"openrouter: {ROUTER_KEY}\nopenai: {OPENAI_KEY}\n"

    assert demo_keys.extract(OPENAI, note) == OPENAI_KEY
    assert demo_keys.extract(OPENROUTER, note) == ROUTER_KEY
    # 다른 키의 한가운데에 든 'sk-' 는 키의 시작이 아니다
    assert demo_keys.extract(OPENAI, "token=xsk-" + "c" * 30) is None


def test_saving_writes_the_trimmed_key_owner_only_and_removing_deletes_the_file(tmp_path):
    path = demo_keys.save(tmp_path / ".demo", OPENROUTER, f"  {ROUTER_KEY}\n")

    assert path == demo_keys.key_path(tmp_path, OPENROUTER) == tmp_path / ".demo" / "openrouter.key"
    assert path.read_text(encoding="utf-8") == ROUTER_KEY + "\n"
    if os.name != "nt":  # Windows 는 사용자 폴더의 권한(ACL)을 따른다
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert demo_keys.remove(tmp_path, OPENROUTER) is True
    assert not path.exists()
    assert demo_keys.remove(tmp_path, OPENROUTER) is False


def test_the_status_follows_the_servers_order_and_shows_only_the_last_four_characters(tmp_path):
    demo_keys.save(tmp_path / ".demo", OPENROUTER, ROUTER_KEY)
    from_file = demo_keys.status(tmp_path, OPENROUTER, {})
    other = "sk-or-v1-" + "z" * 24 + "9999"
    from_env = demo_keys.status(tmp_path, OPENROUTER, {"OPENROUTER_API_KEY": other})

    assert from_file.active == ROUTER_KEY
    assert from_file.describe() == ".demo/openrouter.key 있음 (sk-or-...cdef)"
    # 서버는 환경 변수를 키 파일보다 먼저 본다: 도우미도 그 순서로 알린다
    assert from_env.active == other
    assert "환경 변수 OPENROUTER_API_KEY 있음 (sk-or-...9999)" in from_env.describe()
    assert ".demo/openrouter.key 보다 먼저 쓰입니다" in from_env.describe()
    for status in (from_file, from_env):
        assert ROUTER_KEY not in status.describe() and other not in status.describe()


def test_the_status_sees_the_key_file_set_in_the_shell_like_the_server(tmp_path):
    """.demo 에 쓸 키가 없으면 실행기는 셸의 FRAME_VIDEO_KEY_FILE 을 서버에 그대로 넘긴다 — 그 키도 키다."""
    elsewhere = tmp_path / "keys" / "router.env"
    elsewhere.parent.mkdir()
    elsewhere.write_text(f"OPENROUTER_API_KEY={ROUTER_KEY}\n", encoding="utf-8")

    absolute = demo_keys.status(tmp_path, OPENROUTER, {"FRAME_VIDEO_KEY_FILE": str(elsewhere)})
    relative = demo_keys.status(tmp_path, OPENROUTER, {"FRAME_VIDEO_KEY_FILE": "keys/router.env"})

    assert absolute.active == relative.active == ROUTER_KEY  # 상대 경로는 서버가 뜨는 root 기준
    assert absolute.describe() == "FRAME_VIDEO_KEY_FILE 의 파일 있음 (sk-or-...cdef)"
    # .demo 의 파일이 키가 아니면 실행기는 그것을 넘기지 않는다 (셸의 설정을 덮지 않는다)
    (tmp_path / ".demo").mkdir()
    (tmp_path / ".demo" / "openrouter.key").write_text("paste here\n", encoding="utf-8")
    broken = demo_keys.status(tmp_path, OPENROUTER, {"FRAME_VIDEO_KEY_FILE": str(elsewhere)})
    assert broken.active == ROUTER_KEY and "openrouter.key 에는 sk-or- 로" in broken.describe()
    assert demo_keys.usable_key_file(tmp_path, OPENROUTER) is None
    empty = demo_keys.status(tmp_path, OPENAI, {"FRAME_LLM_KEY_FILE": str(elsewhere)})
    assert empty.active is None and "FRAME_LLM_KEY_FILE 의 파일에" in empty.describe()


def test_a_key_file_without_a_key_is_reported_as_such(tmp_path):
    (tmp_path / ".demo").mkdir()
    (tmp_path / ".demo" / "openai.key").write_text("paste here\n", encoding="utf-8")

    status = demo_keys.status(tmp_path, OPENAI, {})

    assert status.active is None and status.file_present
    assert "sk- 로 시작하는 키가 없습니다" in status.describe()
    assert demo_keys.status(tmp_path, OPENROUTER, {}).describe() == "없음"
    assert demo_keys.usable_key_file(tmp_path, OPENAI) is None
    demo_keys.save(tmp_path / ".demo", OPENAI, OPENAI_KEY)
    assert demo_keys.usable_key_file(tmp_path, OPENAI) == tmp_path / ".demo" / "openai.key"


@pytest.mark.parametrize(
    ("variable", "key", "shown"),
    [
        ("FRAME_VIDEO_KEY_FILE", ROUTER_KEY, "sk-or-...cdef"),
        ("FRAME_LLM_KEY_FILE", OPENAI_KEY, "sk-...1234"),
    ],
)
def test_a_key_put_where_a_key_file_path_belongs_is_never_shown_in_full(
    tmp_path, variable, key, shown
):
    """FRAME_*_KEY_FILE 에 경로 대신 키를 넣는 실수 — 상태 · 안내는 그 값을 그대로 내지 않는다 (끝 네 글자만)."""
    kind = OPENROUTER if variable == "FRAME_VIDEO_KEY_FILE" else OPENAI
    status = demo_keys.status(tmp_path, kind, {variable: key})
    script = ScriptedConsole()

    demo_key_guide.keys_command(tmp_path, {variable: key}, script.console, HINTS, interactive=False)

    assert status.active is None
    assert f"{variable} 에 경로 대신 키가 들어 있습니다 ({shown})" in status.describe()
    assert key not in status.describe() and key not in script.text
    assert key not in " ".join(
        demo_keys.status(tmp_path, k, {variable: key}).describe() for k in demo_keys.KINDS
    )


def test_a_key_given_instead_of_a_key_file_path_is_not_echoed(tmp_path):
    script = ScriptedConsole()

    ok = demo_key_guide.import_file(tmp_path, OPENROUTER, ROUTER_KEY, script.console)

    assert ok is False and ROUTER_KEY not in script.text
    assert "경로 대신 키를 넣었습니다 (sk-or-...cdef)" in script.text


def test_a_missing_key_file_path_is_reported_as_missing_not_as_a_file_without_a_key(tmp_path):
    status = demo_keys.status(tmp_path, OPENROUTER, {"FRAME_VIDEO_KEY_FILE": "~/keys/or.key"})

    assert status.active is None
    assert status.describe().startswith("FRAME_VIDEO_KEY_FILE 의 파일이 없습니다: ~/keys/or.key")


def test_key_files_the_server_reads_are_read_with_the_servers_rule(tmp_path):
    """메모에 OpenRouter 키가 먼저 적힌 FRAME_LLM_KEY_FILE.

    서버는 첫 sk- 토큰(sk-or-)을 읽는다 — 상태도 그 키를 알린다.
    """
    from frame.adapters.generator.openrouter import resolve_openrouter_key
    from frame.adapters.llm.key_store import LLMKeyStore

    memo = tmp_path / "memo.txt"
    memo.write_text(f"openrouter {ROUTER_KEY}\nopenai {OPENAI_KEY}\n", encoding="utf-8")
    env = {"FRAME_LLM_KEY_FILE": str(memo), "FRAME_VIDEO_KEY_FILE": str(memo)}

    openai = demo_keys.status(tmp_path, OPENAI, env)
    router = demo_keys.status(tmp_path, OPENROUTER, env)

    assert openai.active == LLMKeyStore(key_file=memo, env={}).resolve() == ROUTER_KEY
    assert "OpenRouter 키라서 OpenAI 주소로는 쓰이지 않습니다" in openai.describe()
    assert router.active == resolve_openrouter_key(env={}, key_file=memo) == ROUTER_KEY
    # 입력받을 때는 엄하다: 같은 메모를 OpenAI 자리로 가져오면 OpenAI 키를 집는다
    assert demo_keys.extract(OPENAI, memo.read_text(encoding="utf-8")) == OPENAI_KEY


def test_a_key_file_in_a_folder_that_cannot_be_read_does_not_crash_the_status(
    tmp_path, monkeypatch
):
    """POSIX 에서 읽을 수 없는 폴더 안의 경로는 is_file() 이 PermissionError 를 낸다 — 상태 보기는 끝까지 간다."""
    locked = tmp_path / "locked" / "or.key"
    real_is_file = Path.is_file

    def is_file(self: Path) -> bool:
        if self == locked:
            raise PermissionError(13, "Permission denied")
        return real_is_file(self)

    monkeypatch.setattr(Path, "is_file", is_file)

    status = demo_keys.status(tmp_path, OPENROUTER, {"FRAME_VIDEO_KEY_FILE": str(locked)})

    assert status.active is None and "파일이 없습니다" in status.describe()


def test_the_launcher_uses_the_same_key_variables_as_the_server():
    # 런처는 의존성 설치 전에도 돌아야 해서 서버 모듈을 쓰지 못하고 이름을 따로 적어 둔다
    assert OPENAI.env == OPENAI_KEY_ENV and OPENROUTER.env == OPENROUTER_KEY_ENV
    files = Settings.from_env({OPENAI.file_env: "a.key", OPENROUTER.file_env: "b.key"})
    assert files.llm_key_file == "a.key" and files.video_key_file == "b.key"
    assert demo_keys.kind_named("openrouter") is OPENROUTER


def test_the_address_probe_returns_what_the_installed_server_would_use():
    """주소는 런처에 적지 않고 서버 코드에 묻는다: 기본값도, 셸에서 바꾼 주소도 서버의 것이다."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("FRAME_")}
    env["PYTHONPATH"] = str(SRC)

    def probe(**extra: str) -> dict[str, str]:
        done = subprocess.run(
            [sys.executable, "-X", "utf8", "-c", demo_keys.ADDRESS_PROBE],
            capture_output=True,
            text=True,
            env={**env, **extra},
            check=True,
        )
        return json.loads(done.stdout.strip().splitlines()[-1])

    assert probe() == {
        "openai": OPENAI_BASE_URL,
        "openrouter": Settings.from_env({}).video_base_url,
    }
    custom = probe(
        FRAME_LLM_BASE_URL="http://localhost:8001/v1", FRAME_VIDEO_BASE_URL="https://p/v1"
    )
    assert custom == {"openai": "http://localhost:8001/v1", "openrouter": "https://p/v1"}


# ---- 온라인 확인 --------------------------------------------------------------------------


def test_the_check_sends_the_key_only_to_the_address_the_server_would_use():
    transport = FakeTransport(body={"data": []})

    result = demo_keys.check(OPENAI, OPENAI_KEY, "https://llm.example/v1/", transport=transport)

    assert result.ok is True
    ((url, headers, timeout),) = transport.calls
    assert url == "https://llm.example/v1/models"
    assert headers["Authorization"] == f"Bearer {OPENAI_KEY}"
    assert timeout == demo_keys.CHECK_TIMEOUT_S
    local = FakeTransport()
    demo_keys.check(OPENAI, OPENAI_KEY, "http://localhost:8001/v1", transport=local)
    assert (
        local.calls[0][0] == "http://localhost:8001/v1/models"
    )  # 이 기기의 로컬 서버는 http 도 된다


def test_the_default_transport_is_the_shared_one_that_keeps_keys_off_redirects():
    """키 헤더를 리다이렉트로 넘기지 않고 헤더에 쓸 수 없는 값을 문구에 싣지 않는 공용 전송을 쓴다."""
    transport = demo_keys.default_transport()

    assert isinstance(transport, UrllibTransport)
    # 확인 응답은 작다: 공용 기본값(장면 클립용 512MB · 시간 제한의 10배)이 아니라 작게 묶는다
    assert transport.max_response_bytes == demo_keys.CHECK_MAX_BYTES
    assert transport.total_timeout_factor < UrllibTransport().total_timeout_factor


@pytest.mark.parametrize(
    "address", ["http://example.com/api/v1", "ftp://openrouter.ai", "https://"]
)
def test_the_key_is_never_sent_to_an_address_that_is_not_https(address):
    transport = FakeTransport()

    result = demo_keys.check(OPENROUTER, ROUTER_KEY, address, transport=transport)

    assert result.ok is None and "https" in result.message
    assert transport.calls == []


@pytest.mark.parametrize(
    ("kind", "key", "address", "says"),
    [
        (OPENAI, ROUTER_KEY, "https://api.openai.com/v1", "OpenAI 주소로 가는 OpenRouter 키"),
        (OPENROUTER, OPENAI_KEY, "https://openrouter.ai/api/v1", "OpenRouter 자리에 OpenAI 키"),
    ],
    ids=["openrouter-key-to-openai", "openai-key-to-openrouter"],
)
def test_a_key_of_the_other_service_is_never_sent(kind, key, address, says):
    """서버도 OpenAI 주소로 sk-or- 키를 보내지 않는다. OpenAI 키를 OpenRouter 에 흘리지도 않는다."""
    transport = FakeTransport()

    result = demo_keys.check(kind, key, address, transport=transport)

    assert result.ok is False and says in result.message
    assert transport.calls == [] and key not in result.message


def test_an_openrouter_key_for_llm_calls_routed_to_openrouter_is_checked_there():
    """FRAME_LLM_BASE_URL 을 OpenRouter 로 돌렸으면 서버는 OpenAI 자리의 sk-or- 키를 쓴다 — 확인도 거기로 보낸다."""
    transport = FakeTransport(body={"data": []})

    result = demo_keys.check(
        OPENAI, ROUTER_KEY, "https://openrouter.ai/api/v1", transport=transport
    )

    assert result.ok is True
    assert [call[0] for call in transport.calls] == ["https://openrouter.ai/api/v1/models"]


@pytest.mark.parametrize(
    "address",
    [
        "https://api.openai.com/v1",
        "https://openai.com/v1",
        "https://eu.api.openai.com/v1",
        "https://openrouter.ai/api/v1",
        "https://notopenai.com/v1",
        "http://localhost:8001/v1",
    ],
)
def test_the_openai_address_rule_is_the_servers(address):
    from frame.adapters.llm.openai_client import _goes_to_openai

    assert demo_keys.goes_to_openai(address) is _goes_to_openai(address)


def test_a_key_with_a_line_break_is_not_sent_and_not_printed():
    transport = FakeTransport()
    key = OPENAI_KEY + "\nsk-proj-" + "Z" * 24

    result = demo_keys.check(OPENAI, key, ADDRESSES["openai"], transport=transport)

    assert result.ok is False and "줄바꿈" in result.message
    assert transport.calls == [] and OPENAI_KEY not in result.message


@pytest.mark.parametrize(
    ("status", "body", "error", "ok", "says"),
    [
        (401, INVALID_KEY, None, False, "거절"),
        (403, {}, None, False, "거절"),
        (429, {}, None, None, "429"),
        (500, {}, None, None, "HTTP 500"),
        (200, {}, TransportError("URLError: no route"), None, "연결하지 못했습니다"),
    ],
)
def test_the_check_tells_a_rejected_key_from_one_it_could_not_check(status, body, error, ok, says):
    transport = FakeTransport(status, body, error=error)

    result = demo_keys.check(OPENROUTER, ROUTER_KEY, ADDRESSES["openrouter"], transport=transport)

    assert result.ok is ok and says in result.message
    assert ROUTER_KEY not in result.message


def test_a_restricted_openai_key_is_not_called_rejected():
    """모델 목록 권한을 뺀(권한을 줄인) 키도 분석에는 쓸 수 있다: 거절이 아니라 '확인 못 함' 이다."""
    scope = {
        "error": {
            "message": "You have insufficient permissions for this operation. Missing scopes: api.model.read"
        }
    }

    restricted = demo_keys.check(
        OPENAI, OPENAI_KEY, ADDRESSES["openai"], transport=FakeTransport(401, scope)
    )
    invalid = demo_keys.check(
        OPENAI, OPENAI_KEY, ADDRESSES["openai"], transport=FakeTransport(401, INVALID_KEY)
    )

    assert restricted.ok is None and "권한" in restricted.message
    assert invalid.ok is False


def test_an_openrouter_check_shows_what_the_key_has_spent_and_may_still_spend():
    spent = {"data": {"usage": 1.234, "limit": 10, "limit_remaining": 8.766}}
    unlimited = {"data": {"usage": 0, "limit": None, "limit_remaining": None}}
    base = ADDRESSES["openrouter"]

    first = demo_keys.check(OPENROUTER, ROUTER_KEY, base, transport=FakeTransport(body=spent))
    second = demo_keys.check(OPENROUTER, ROUTER_KEY, base, transport=FakeTransport(body=unlimited))

    assert first.message == "통합니다 · 이 키로 쓴 금액 $1.23, 키의 남은 한도 $8.77"
    assert "키 자체의 한도 없음" in second.message
    assert (
        demo_keys.check(OPENROUTER, ROUTER_KEY, base, transport=FakeTransport(body=[])).ok is True
    )


def test_before_installation_the_check_says_so_and_sends_nothing(tmp_path):
    demo_keys.save(tmp_path / ".demo", OPENROUTER, ROUTER_KEY)
    transport = FakeTransport()
    script = ScriptedConsole()

    assert demo_key_guide.check_all(tmp_path, {}, script.console, online=_online(transport, None))

    assert transport.calls == []
    assert "[확인 못 함]" in script.text and "설치(setup) 뒤에" in script.text


# ---- 대화형 도우미 --------------------------------------------------------------------------


def test_the_guide_saves_a_pasted_key_and_never_prints_it(tmp_path):
    script = ScriptedConsole("2", ROUTER_KEY, "n", "q")

    assert demo_key_guide.guide(tmp_path, {}, script.console, HINTS) == 0

    assert demo_keys.key_path(tmp_path, OPENROUTER).read_text(encoding="utf-8") == ROUTER_KEY + "\n"
    assert ROUTER_KEY not in script.text and "sk-or-...cdef" in script.text
    assert "https://openrouter.ai/keys" in script.text  # 키를 만드는 곳을 알린다
    assert "run.cmd --video" in script.text  # 다음에 할 일: 이제 실제 영상 모드를 쓸 수 있다
    assert demo_keys.KEY_STAYS_IN_FOLDER in script.text


def test_the_guide_can_check_a_key_right_after_saving_it(tmp_path):
    script = ScriptedConsole("1", OPENAI_KEY, "y", "q")
    transport = FakeTransport(401, INVALID_KEY)

    demo_key_guide.guide(tmp_path, {}, script.console, HINTS, online=_online(transport))

    assert [call[0] for call in transport.calls] == ["https://llm.example/v1/models"]
    assert "[거절됨]" in script.text


def test_ctrl_c_while_checking_returns_to_the_menu(tmp_path):
    demo_keys.save(tmp_path / ".demo", OPENAI, OPENAI_KEY)
    script = ScriptedConsole("c", "q")
    online = _online(FakeTransport(error=KeyboardInterrupt()))

    assert demo_key_guide.guide(tmp_path, {}, script.console, HINTS, online=online) == 0

    assert "멈췄습니다" in script.text and "다음에 할 일" in script.text


def test_the_guide_points_a_key_put_in_the_wrong_slot_to_the_right_one(tmp_path):
    script = ScriptedConsole("1", ROUTER_KEY, "q")

    demo_key_guide.guide(tmp_path, {}, script.console, HINTS)

    assert not demo_keys.key_path(tmp_path, OPENAI).exists()
    assert "넣은 것은 OpenRouter API 키입니다" in script.text


def test_the_guide_removes_a_key_only_after_it_is_confirmed(tmp_path):
    demo_keys.save(tmp_path / ".demo", OPENROUTER, ROUTER_KEY)
    path = demo_keys.key_path(tmp_path, OPENROUTER)

    demo_key_guide.guide(tmp_path, {}, ScriptedConsole("d", "2", "n", "q").console, HINTS)
    assert path.exists()
    demo_key_guide.guide(tmp_path, {}, ScriptedConsole("d", "2", "y", "q").console, HINTS)
    assert not path.exists()


def test_the_guide_checks_every_key_it_has_asking_the_server_addresses_once(tmp_path):
    demo_keys.save(tmp_path / ".demo", OPENAI, OPENAI_KEY)
    script = ScriptedConsole("c", "q")
    transport = FakeTransport(body={"data": []})
    asked: list[int] = []

    def addresses() -> dict[str, str]:
        asked.append(1)
        return ADDRESSES

    online = demo_keys.Online(addresses=addresses, transport=transport)
    env = {"OPENROUTER_API_KEY": ROUTER_KEY}
    demo_key_guide.guide(tmp_path, env, script.console, HINTS, online=online)

    assert [call[0] for call in transport.calls] == [
        "https://llm.example/v1/models",
        "https://video.example/api/v1/key",
    ]
    assert script.text.count("[통함]") == 2
    assert asked == [1]  # 주소는 서버 Python 을 띄워 묻는다: 확인 한 번에 한 번만


def test_the_guide_ends_when_the_input_ends_or_enter_is_pressed(tmp_path):
    for script in (ScriptedConsole(), ScriptedConsole(""), ScriptedConsole("x", "q")):
        assert demo_key_guide.guide(tmp_path, {}, script.console, HINTS) == 0
        assert "다음에 할 일" in script.text
    assert "1 · 2 · c · d · q 가운데 하나를 고르세요" in script.text


# ---- keys 명령 ------------------------------------------------------------------------------


def test_without_a_terminal_the_command_only_shows_the_status(tmp_path):
    script = ScriptedConsole("2", ROUTER_KEY)

    assert demo_key_guide.keys_command(tmp_path, {}, script.console, HINTS, interactive=False) == 0

    assert script.prompts == []  # 묻지 않는다 (CI · 파이프에서 멈추지 않게)
    assert "OpenRouter API 키: 없음" in script.text
    assert "터미널에서 keys.cmd 를 실행하세요" in script.text


def test_the_command_imports_and_removes_key_files(tmp_path):
    source = tmp_path / "keys.env"
    source.write_text(
        f"OPENAI_API_KEY={OPENAI_KEY}\nOPENROUTER_API_KEY={ROUTER_KEY}\n", encoding="utf-8"
    )
    script = ScriptedConsole()
    files = {OPENAI: str(source), OPENROUTER: str(source)}

    code = demo_key_guide.keys_command(
        tmp_path, {}, script.console, HINTS, interactive=True, files=files
    )

    assert code == 0
    assert demo_keys.status(tmp_path, OPENAI, {}).active == OPENAI_KEY
    assert demo_keys.status(tmp_path, OPENROUTER, {}).active == ROUTER_KEY
    assert script.prompts == []  # 옵션을 주면 대화형으로 넘어가지 않는다
    assert OPENAI_KEY not in script.text and ROUTER_KEY not in script.text

    removed = ScriptedConsole()
    demo_key_guide.keys_command(
        tmp_path, {}, removed.console, HINTS, interactive=True, remove_names=("openrouter",)
    )
    assert not demo_keys.key_path(tmp_path, OPENROUTER).exists()
    assert demo_keys.key_path(tmp_path, OPENAI).exists()


def test_importing_a_file_without_the_key_fails_and_says_what_the_file_holds(tmp_path):
    source = tmp_path / "router.txt"
    source.write_text(ROUTER_KEY, encoding="utf-8")
    script = ScriptedConsole()

    code = demo_key_guide.keys_command(
        tmp_path, {}, script.console, HINTS, interactive=False, files={OPENAI: str(source)}
    )

    assert code == 1
    assert "이 파일에는 OpenRouter API 키가 들어 있습니다" in script.text
    assert not demo_keys.key_path(tmp_path, OPENAI).exists()


def test_the_check_option_fails_when_a_service_rejects_the_key(tmp_path):
    demo_keys.save(tmp_path / ".demo", OPENROUTER, ROUTER_KEY)
    online = _online(FakeTransport(401, INVALID_KEY))

    code = demo_key_guide.keys_command(
        tmp_path,
        {},
        ScriptedConsole().console,
        HINTS,
        interactive=False,
        run_check=True,
        online=online,
    )

    assert code == 1


def test_the_next_steps_say_how_to_get_real_videos_when_no_openrouter_key_is_set(tmp_path):
    script = ScriptedConsole()

    demo_key_guide.next_steps(tmp_path, {}, script.console, HINTS)

    assert "keys.cmd 로 OpenRouter 키를 넣고, ffmpeg 준비" in script.text
    assert demo_keys.KEY_STAYS_IN_FOLDER not in script.text  # 저장한 키가 없으면 말하지 않는다
