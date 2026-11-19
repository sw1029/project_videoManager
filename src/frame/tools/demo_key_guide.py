"""시연 실행기의 API 키 도우미: 사람과 주고받는 부분 — 대화형 메뉴와 `keys` 명령 (표준 라이브러리만, ADR-0037).

키 파일 · 상태 · 온라인 확인 자체는 demo_keys 에 있다. 여기는 그것을 묻고 알리는 순서만 정한다.
- 터미널에서 옵션 없이 부르면 메뉴: 1 OpenAI 키 · 2 OpenRouter 키 · c 확인 · d 지우기 · q 끝내기.
- 터미널이 아니면(CI · 파이프) 묻지 않고 상태만 보인다 — 멈추지 않는다.
- 키는 보이지 않게(getpass) 받고, 화면에는 끝 네 글자만 보인다.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from frame.tools.demo_keys import (
    DEMO_DIR,
    KEY_STAYS_IN_FOLDER,
    KINDS,
    OPENAI,
    OPENROUTER,
    CheckResult,
    KeyKind,
    Online,
    any_key_file,
    ask_addresses,
    check_key,
    extract,
    key_path,
    key_shaped,
    kind_named,
    mask,
    other_kind,
    read_text,
    remove,
    save,
    status,
)

MAX_ROUNDS = 50  # 대화형 도우미가 메뉴를 다시 보이는 횟수의 상한 (입력이 끝없이 들어와도 멈춘다)


@dataclass(frozen=True)
class Console:
    """도우미가 쓰는 입출력. 테스트는 정해진 답을 돌려주는 함수를 넣는다."""

    say: Callable[[str], None]
    error: Callable[[str], None]
    ask: Callable[[str], str]  # 보이는 입력
    ask_secret: Callable[[str], str]  # 보이지 않는 입력 (getpass)

    def answer(self, prompt: str, *, secret: bool = False) -> str | None:
        """입력 한 줄. 입력이 끝났거나(EOF) Ctrl+C 면 None."""
        try:
            return (self.ask_secret if secret else self.ask)(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            return None

    def agrees(self, prompt: str) -> bool:
        return (self.answer(prompt) or "").lower() in ("y", "yes", "예", "네")


@dataclass(frozen=True)
class Hints:
    """다음에 할 일 안내에 쓰는 명령 (저장소는 scripts\\run.cmd, 번들은 run.cmd 처럼 OS·형태마다 다르다)."""

    run: str
    keys: str
    prepare_video: str  # 실제 영상 모드의 준비 (저장소: setup --video, 번들: ffmpeg 설치)


def import_file(root: Path, kind: KeyKind, source: str, console: Console) -> bool:
    """키 파일에서 키를 찾아 .demo 에 저장한다. 키는 출력하지 않는다 (경로 자리에 키를 줘도 가린다)."""
    shaped = key_shaped(source)
    if shaped is not None:
        shaped_kind, given = shaped
        console.error(
            f"키 파일의 경로 대신 키를 넣었습니다 ({mask(shaped_kind, given)}). 키가 든 파일의 경로를 주거나, "
            "옵션 없이 keys 를 실행해 메뉴에서 붙여 넣으세요"
        )
        return False
    path = Path(source).expanduser()
    if not path.is_file():
        console.error(f"키 파일이 없습니다: {path}")
        return False
    text = read_text(path)
    key = extract(kind, text)
    if key is None:
        other = other_kind(kind, text)
        found = f" (이 파일에는 {other.title}가 들어 있습니다)" if other else ""
        console.error(
            f"키 파일에서 {kind.prefix} 로 시작하는 {kind.title}를 찾지 못했습니다: {path}{found}"
        )
        return False
    save(root / DEMO_DIR, kind, key)
    console.say(f"{kind.title}를 .demo/{kind.file_name} 에 저장했습니다 ({mask(kind, key)})")
    return True


def enter_key(
    root: Path,
    kind: KeyKind,
    env: Mapping[str, str],
    console: Console,
    *,
    offer_check: bool,
    online: Online | None = None,
) -> bool:
    """키를 보이지 않게 입력받아 저장한다. 저장했으면 True (건너뛰거나 형식이 틀리면 False)."""
    console.say(f"{kind.title} — {kind.purpose}")
    console.say(f"키는 {kind.issue_url} 에서 만듭니다 ({kind.prefix} 로 시작)")
    entered = console.answer(
        "키를 붙여 넣고 Enter (화면에 보이지 않습니다 · 그냥 Enter 는 건너뛰기): ", secret=True
    )
    if not entered:
        console.say("건너뛰었습니다")
        return False
    key = extract(kind, entered)
    if key is None:
        other = other_kind(kind, entered)
        if other is not None:
            console.error(
                f"넣은 것은 {other.title}입니다 ({other.prefix}...). 그 키의 자리에 넣으세요"
            )
        else:
            console.error(f"{kind.prefix} 로 시작하는 {kind.title}가 아닙니다. 다시 확인하세요")
        return False
    save(root / DEMO_DIR, kind, key)
    console.say(f"저장했습니다: .demo/{kind.file_name} ({mask(kind, key)})")
    if (env.get(kind.env) or "").strip():
        console.say(f"주의: 환경 변수 {kind.env} 가 있어 서버는 그 값을 먼저 씁니다")
    if offer_check and console.agrees("지금 통하는지 확인할까요? 돈이 들지 않는 조회 1회 [y/N]: "):
        _report(console, kind, check_key(kind, key, ask_addresses(online), online))
    return True


def _report(console: Console, kind: KeyKind, result: CheckResult) -> None:
    label = {True: "통함", False: "거절됨", None: "확인 못 함"}[result.ok]
    (console.error if result.ok is False else console.say)(
        f"{kind.title} [{label}] {result.message}"
    )


def check_all(
    root: Path, env: Mapping[str, str], console: Console, *, online: Online | None = None
) -> bool:
    """있는 키를 모두 확인한다. 거절된 키가 하나라도 있으면 False (확인하지 못한 것은 거절이 아니다)."""
    keys = [(kind, status(root, kind, env).active) for kind in KINDS]
    # 주소는 서버 Python 을 띄워 묻는다: 확인 한 번에 한 번만 (확인할 키가 없으면 묻지 않는다)
    addresses = ask_addresses(online) if any(key for _, key in keys) else None
    rejected = False
    for kind, key in keys:
        if key is None:
            console.say(f"{kind.title} [없음] 확인하지 않았습니다")
            continue
        result = check_key(kind, key, addresses, online)
        _report(console, kind, result)
        rejected = rejected or result.ok is False
    return not rejected


def show_status(root: Path, env: Mapping[str, str], console: Console) -> None:
    for kind in KINDS:
        console.say(f"{kind.title}: {status(root, kind, env).describe()}")
        console.say(f"    쓰는 곳: {kind.purpose}")


def next_steps(root: Path, env: Mapping[str, str], console: Console, hints: Hints) -> None:
    console.say("다음에 할 일")
    console.say(f"  실행: {hints.run}   (자연어 분석 · 영상은 시연용 가짜 생성기, 요금 없음)")
    if status(root, OPENROUTER, env).active:
        console.say(
            f"  실제 영상: {hints.run} --video   (OpenRouter 요금이 듭니다 · {hints.prepare_video})"
        )
    else:
        console.say(
            f"  실제 영상을 만들려면: {hints.keys} 로 OpenRouter 키를 넣고, {hints.prepare_video}"
        )
    if not status(root, OPENAI, env).active:
        console.say(
            "  OpenAI 키가 없으면 실행 후 화면의 'OpenAI API 키' 칸에 넣어도 됩니다 (서버 메모리에만)"
        )
    if any_key_file(root):
        console.say(f"  {KEY_STAYS_IN_FOLDER}")


def _remove_one(root: Path, env: Mapping[str, str], console: Console) -> None:
    answer = console.answer("지울 키 [1=OpenAI · 2=OpenRouter · Enter 취소]: ")
    if answer not in ("1", "2"):
        console.say("취소했습니다")
        return
    kind = KINDS[int(answer) - 1]
    if not key_path(root, kind).is_file():
        where = f" (환경 변수 {kind.env} 는 여기서 지울 수 없습니다)" if env.get(kind.env) else ""
        console.say(f".demo/{kind.file_name} 가 없습니다{where}")
        return
    if console.agrees(f".demo/{kind.file_name} 를 지울까요? 되돌릴 수 없습니다 [y/N]: "):
        remove(root, kind)
        console.say(f"지웠습니다: .demo/{kind.file_name}")
    else:
        console.say("지우지 않았습니다")


def guide(
    root: Path,
    env: Mapping[str, str],
    console: Console,
    hints: Hints,
    *,
    online: Online | None = None,
) -> int:
    """대화형 도우미: 상태를 보이고, 키를 넣고 · 확인하고 · 지운다. q·Enter 로 끝낸다."""
    console.say(
        "API 키 설정 도우미 — 키는 이 폴더의 .demo 에 저장되고, 화면에는 끝 네 글자만 보입니다"
    )
    for _ in range(MAX_ROUNDS):
        console.say("")
        for number, kind in enumerate(KINDS, 1):
            console.say(f"  {number}) {kind.title}: {status(root, kind, env).describe()}")
            console.say(f"       {kind.purpose}")
        console.say("  c) 저장한 키가 통하는지 확인 (인터넷 · 돈이 들지 않는 조회)")
        console.say("  d) 저장한 키 지우기")
        console.say("  q) 끝내기")
        choice = (console.answer("고르세요 [1/2/c/d/q · Enter 는 끝내기]: ") or "q").lower()
        if choice in ("q", "quit", "exit"):
            break
        try:
            _act(root, env, console, choice, online)
        except KeyboardInterrupt:  # 확인을 기다리다 Ctrl+C: 도우미를 끝내지 않고 메뉴로 돌아간다
            console.say("멈췄습니다 — 메뉴로 돌아갑니다")
    console.say("")
    next_steps(root, env, console, hints)
    return 0


def _act(
    root: Path, env: Mapping[str, str], console: Console, choice: str, online: Online | None
) -> None:
    if choice in ("1", "2"):
        kind = KINDS[int(choice) - 1]
        enter_key(root, kind, env, console, offer_check=True, online=online)
    elif choice == "c":
        check_all(root, env, console, online=online)
    elif choice == "d":
        _remove_one(root, env, console)
    else:
        console.say("1 · 2 · c · d · q 가운데 하나를 고르세요")


def keys_command(
    root: Path,
    env: Mapping[str, str],
    console: Console,
    hints: Hints,
    *,
    interactive: bool,
    show_only: bool = False,
    run_check: bool = False,
    files: Mapping[KeyKind, str] | None = None,
    remove_names: tuple[str, ...] = (),
    online: Online | None = None,
) -> int:
    """`keys` 명령. 옵션이 없고 사람이 입력할 수 있으면(터미널) 도우미를, 아니면 상태만 보인다."""
    acted = bool(files) or bool(remove_names) or run_check or show_only
    for kind, source in (files or {}).items():
        if not import_file(root, kind, source, console):
            return 1
    for name in remove_names:
        kind = kind_named(name)
        removed = remove(root, kind)
        console.say(f"{'지웠습니다' if removed else '없습니다'}: .demo/{kind.file_name}")
    if not acted and interactive:
        return guide(root, env, console, hints, online=online)
    show_status(root, env, console)
    if run_check and not check_all(root, env, console, online=online):
        return 1
    if not acted:
        console.say(f"키를 넣으려면 터미널에서 {hints.keys} 를 실행하세요 (대화형 도우미)")
    next_steps(root, env, console, hints)
    return 0
