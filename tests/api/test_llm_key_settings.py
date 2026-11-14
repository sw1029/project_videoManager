"""화면 API 키 입력 (FRAME_LLM_RUNTIME_KEY): 같은 기기에서만, 메모리에만, 끝 4자리만 보인다."""

import pytest
from fastapi.testclient import TestClient

from frame.config import Settings
from frame.presentation.app import create_app
from tests.support.factories import dispatcher_of, make_container

pytest.importorskip("openai")

LOOPBACK = ("127.0.0.1", 50000)
REMOTE = ("203.0.113.7", 50000)
KEY = "sk-test-" + "a" * 24 + "WXYZ"


@pytest.fixture(autouse=True)
def _no_env_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def _client(settings: Settings, client=LOOPBACK):
    container = make_container(settings)
    return TestClient(create_app(container), client=client), container


def _runtime(tmp_path, **kwargs) -> Settings:
    return Settings(
        structurer="llm",
        llm_runtime_key=True,
        llm_key_file=str(tmp_path / "none.txt"),
        jobs="recording",
        persistence="memory",
        **kwargs,
    )


def test_server_starts_without_a_key_and_reports_status(tmp_path):
    client, _ = _client(_runtime(tmp_path))
    assert client.get("/health").json()["llm_key"] == {
        "configured": False,
        "source": None,
        "hint": None,
        "runtime_entry": True,
        "runtime_entry_enabled": True,
    }


def test_structuring_without_a_key_fails_with_guidance(tmp_path):
    client, container = _client(_runtime(tmp_path))
    created = client.post(
        "/api/projects", json={"title": "t", "prompt": "카페 숏폼", "target_duration_ms": 8000}
    ).json()
    dispatcher_of(container).run_all()
    version = client.get(f"/api/versions/{created['version_id']}").json()
    assert version["state"] == "FAILED"
    assert version["error_code"] == "LLM_UNAVAILABLE"
    assert "키" in version["error_message"]


def test_key_can_be_set_and_cleared_from_the_same_machine(tmp_path):
    client, _ = _client(_runtime(tmp_path))
    response = client.put("/api/settings/llm-key", json={"api_key": KEY})
    assert response.status_code == 200
    assert response.json() == {
        "configured": True,
        "source": "runtime",
        "hint": "sk-...WXYZ",
        "runtime_entry": True,
        "runtime_entry_enabled": True,
    }
    assert KEY not in response.text
    assert client.get("/health").json()["llm_key"]["source"] == "runtime"
    cleared = client.delete("/api/settings/llm-key")
    assert cleared.status_code == 200 and cleared.json()["configured"] is False


def test_remote_clients_can_read_status_but_not_set_keys(tmp_path):
    client, _ = _client(_runtime(tmp_path), client=REMOTE)
    response = client.put("/api/settings/llm-key", json={"api_key": KEY})
    assert response.status_code == 403 and response.json()["error_code"] == "FORBIDDEN"
    assert client.delete("/api/settings/llm-key").status_code == 403
    assert client.get("/api/settings/llm-key").json()["configured"] is False


def test_status_tells_remote_devices_to_enter_the_key_on_the_server(tmp_path):
    """다른 기기(iPad 등)에서 볼 때는 입력란 대신 '서버 기기에서 입력' 안내를 띄울 수 있어야 한다."""
    client, _ = _client(_runtime(tmp_path), client=REMOTE)
    for status in (
        client.get("/health").json()["llm_key"],
        client.get("/api/settings/llm-key").json(),
    ):
        assert status["runtime_entry"] is False
        assert status["runtime_entry_enabled"] is True


def test_remote_entry_can_be_allowed_explicitly(tmp_path):
    client, _ = _client(_runtime(tmp_path, llm_runtime_key_allow_remote=True), client=REMOTE)
    assert client.get("/health").json()["llm_key"]["runtime_entry"] is True
    assert client.put("/api/settings/llm-key", json={"api_key": KEY}).status_code == 200


@pytest.mark.parametrize("bad", ["not-a-key", "sk-short", "sk-" + "a" * 24 + " b"])
def test_invalid_key_format_is_rejected_without_echo(tmp_path, bad):
    client, _ = _client(_runtime(tmp_path))
    response = client.put("/api/settings/llm-key", json={"api_key": bad})
    assert response.status_code == 422
    assert response.json()["error_code"] == "VALIDATION_ERROR"
    assert bad not in response.text


def test_runtime_entry_is_off_by_default(tmp_path):
    key_file = tmp_path / "key.txt"
    key_file.write_text("sk-file-" + "b" * 24 + "1234", encoding="utf-8")
    client, _ = _client(
        Settings(
            structurer="llm", llm_key_file=str(key_file), jobs="recording", persistence="memory"
        )
    )
    assert client.get("/api/settings/llm-key").json() == {
        "configured": True,
        "source": "file",
        "hint": "sk-...1234",
        "runtime_entry": False,
        "runtime_entry_enabled": False,
    }
    assert client.put("/api/settings/llm-key", json={"api_key": KEY}).status_code == 403


def test_settings_endpoint_is_absent_without_llm_structuring():
    client, _ = _client(Settings(jobs="recording", persistence="memory"))
    assert client.get("/health").json()["llm_key"] is None
    assert client.get("/api/settings/llm-key").status_code == 404
