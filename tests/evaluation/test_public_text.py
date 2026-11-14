"""커밋되는 기록 · 결과 파일에 남겨도 되는 글 (감사 B21-D05 · B17-D06, P-20).

지표 1 평가의 결과 파일과 구조화 골드셋의 기록은 저장소에 커밋한다. 예외 문구 같은 자유 문구에 이 컴퓨터의 경로
(사용자 이름이 든다)와 접속 정보가 섞여 들어올 수 있어, 기록하기 전에 가린다.
두 도구는 저마다의 규칙을 갖고 있었고 서로 다른 것을 놓쳤다. 지금은 규칙이 한 벌이다 (frame.evaluation._public).
"""

from __future__ import annotations

import importlib

import pytest

_PUBLIC = importlib.import_module("frame.evaluation._public")
public_message = _PUBLIC.public_message
public_url = _PUBLIC.public_url
public_value = _PUBLIC.public_value


@pytest.mark.parametrize(
    ("raw", "shown"),
    [
        # 따옴표 안의 경로는 닫는 따옴표까지 (repr 로 찍힌 것, 빈칸이 든 이름)
        (
            "Permission denied: 'C:\\\\Users\\\\Some One\\\\AppData\\\\x.mp4'",
            "Permission denied: '<경로>'",
        ),
        ('cannot read "/home/someone/My Videos/a.mp4"', 'cannot read "<경로>"'),
        ("offline: '\\\\\\\\fileserver\\\\team\\\\key.txt'", "offline: '<경로>'"),
        # 따옴표 없는 경로는 빈칸 · 구두점까지. 빈칸 뒤에 구분자가 더 있으면 이어진 경로로 본다
        (r"C:\Users\Some One\AppData\Local\ffmpeg.log 를 보세요", "<경로> 를 보세요"),
        (
            "/home/someone/.cache/frame/parent.mp4 의 앞부분을 읽지 못함",
            "<경로> 의 앞부분을 읽지 못함",
        ),
        ("/srv/media/x.mp4: No such file", "<경로>: No such file"),
        (r"network share \\fileserver\team\key.txt is offline", "network share <경로> is offline"),
        ("키 파일이 없습니다: D:/secrets/key.txt", "키 파일이 없습니다: <경로>"),
        ("키 파일: ~/keys/openai.txt", "키 파일: <경로>"),
        # 홈 · 임시 폴더가 아닌 절대 경로도 가린다 (키 파일은 어디에나 둘 수 있다)
        ("/opt/tools/ffmpeg 가 없습니다", "<경로> 가 없습니다"),
        # 주소 안의 경로
        ("cannot open file:///C:/Users/someone/key.txt", "cannot open file:///<경로>"),
    ],
)
def test_local_paths_are_hidden(raw, shown):
    assert public_message(raw) == shown


@pytest.mark.parametrize(
    "text",
    [
        "local://clips/ver_r/intro.mp4",  # 저장소 키
        "clips/ver_r/intro.mp4 를 읽지 못함",  # 상대 경로
        "Connection error: https://api.openai.com/v1/chat/completions",
        "refused by http://[::1]:8001/v1",
        "비율은 9:16 이고 시각은 12:30/13:00 이다",
        "intro:cup:color 를 판정하지 못함",
        "장면/객체의 수가 다르다 (3 / 4)",
        "'/' 로 나눌 수 없습니다",
        "./.demo/openai.key 를 찾지 못함",
    ],
)
def test_other_text_is_left_alone(text):
    assert public_message(text) == text


@pytest.mark.parametrize(
    ("raw", "kept"),
    [
        ("https://user:pw@proxy.local:8443/v1?key=abc#x", "https://proxy.local:8443/v1"),
        ("http://[::1]:8001/v1?x=1", "http://[::1]:8001/v1"),
        ("https://openrouter.ai/api/v1", "https://openrouter.ai/api/v1"),
        ("mem://References/Mug.jpg", "mem://References/Mug.jpg"),  # 뺄 것이 없으면 글자 그대로
        ("주소가 아니다", "주소가 아니다"),
        (None, None),
        (3, 3),
    ],
)
def test_urls_keep_where_they_point_but_not_the_account_or_query(raw, kept):
    assert public_url(raw) == kept


def test_urls_inside_a_message_lose_their_account_and_query():
    message = "401 from https://user:pw@proxy.local:8443/v1?key=abc#x (다시 시도)"

    assert public_message(message) == "401 from https://proxy.local:8443/v1 (다시 시도)"


@pytest.mark.parametrize(
    "path",
    [r"C:\Users\Jane Doe\keys\k.txt", "C:/Users/Jane Doe/keys/k.txt", r"c:\users\jane doe\k.txt"],
)
def test_the_whole_home_folder_is_hidden_even_with_spaces(monkeypatch, path):
    """사용자 폴더 이름에 빈칸이 있어도 이름의 뒷부분이 새지 않는다."""
    monkeypatch.setattr(_PUBLIC, "_home_folders", lambda: (r"C:\Users\Jane Doe",))

    assert public_message(f"키 파일이 없습니다: {path}. 다시 확인하세요") == (
        "키 파일이 없습니다: <경로> 다시 확인하세요"
    )


def test_a_longer_folder_name_that_starts_with_the_home_folder_is_hidden_whole(monkeypatch):
    """사용자 폴더가 C:\\Users\\Al 일 때 C:\\Users\\Alice 의 뒷부분('ice')이 남지 않는다."""
    monkeypatch.setattr(_PUBLIC, "_home_folders", lambda: (r"C:\Users\Al",))

    assert public_message(r"키 파일이 없습니다: C:\Users\Alice\key.txt") == (
        "키 파일이 없습니다: <경로>"
    )


def test_a_home_folder_that_is_only_a_root_is_not_used_for_masking(monkeypatch):
    """사용자 폴더가 뿌리('/')뿐이면 그것으로 가리지 않는다 (문구의 모든 '/' 가 바뀌지 않게)."""
    monkeypatch.setattr(_PUBLIC.Path, "home", classmethod(lambda cls: cls("/")))

    assert _PUBLIC._home_folders() == ()


def test_messages_are_shortened_only_when_a_limit_is_given():
    long = "x" * 2000

    assert public_message(long) == long
    shortened = public_message(long, limit=300)
    assert len(shortened) == 300 and shortened.endswith("…")


def test_strings_inside_nested_values_are_hidden_too():
    """검수의 detail 처럼 중첩된 값: 문자열만 가리고 키 · 숫자 · None 은 그대로 둔다."""
    detail = {
        "reason": "ffmpeg 가 /home/someone/a.mp4 를 읽지 못했습니다",
        "frames": [{"source": "'C:\\\\Users\\\\someone\\\\a.png'"}],
        "signed": "https://cdn.example/v/1.mp4?token=abc",
        "clip_uri": "local://clips/ver_r/intro.mp4",
        "score": 0.5,
        "judged": None,
    }

    assert public_value(detail) == {
        "reason": "ffmpeg 가 <경로> 를 읽지 못했습니다",
        "frames": [{"source": "'<경로>'"}],
        "signed": "https://cdn.example/v/1.mp4",
        "clip_uri": "local://clips/ver_r/intro.mp4",
        "score": 0.5,
        "judged": None,
    }
