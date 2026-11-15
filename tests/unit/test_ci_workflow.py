"""CI 워크플로(ci.yml)의 잡이 지켜야 할 것. 파일을 글로 읽어 확인한다 (GitHub 에서 돌리지 않는다).

- 실패해도 되는 잡(continue-on-error)은 결과만 보기로 정한 잡뿐이다. 그 밖의 잡이 실패하면 워크플로가 실패한다.
- 화면 문구 합성 잡(overlay-font)은 막는 검사다 (보고서 §12.3 결정 11). 글꼴이나 글자를 그리는 ffmpeg 가 없을 때
  건너뛴 채 통과하지 않는다.
- test · compat 잡은 번들 ffmpeg 로 돈다. 그래서 화면 문구 합성을 그 잡에 합치지 않는다.

docker 잡의 안쪽(이미지 안의 점검)은 tests/unit/test_docker_files.py 가 본다.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

# 실패해도 워크플로를 실패시키지 않는 잡 (결과만 본다). 여기에 없는 잡은 모두 막는 검사다
# - audit: 의존성의 알려진 취약점. 러너 환경의 것이 남아 있다 (보고서 §12.3 결정 10)
# - docker: 이미지 안의 점검. 막는 검사로 할지는 아직 정하지 않았다 (보고서 §12.3 결정 12 의 남은 결정)
MAY_FAIL = {"audit", "docker"}


def _jobs() -> dict[str, str]:
    """잡 이름 -> 그 잡의 글 (다음 잡의 머리 전까지)."""
    body = WORKFLOW.split("\njobs:\n", 1)[1]
    heads = list(re.finditer(r"^  ([a-z][a-z-]*):\n", body, flags=re.MULTILINE))
    ends = [head.start() for head in heads[1:]] + [len(body)]
    return {head.group(1): body[head.end() : end] for head, end in zip(heads, ends, strict=True)}


def _settings(job: str) -> list[str]:
    """주석을 뗀 줄 (앞뒤 빈칸도 뗀다)."""
    lines = (line.split("#", 1)[0].strip() for line in job.splitlines())
    return [line for line in lines if line]


def test_only_the_jobs_we_chose_to_watch_may_fail():
    """continue-on-error 가 붙은 잡은 실패해도 워크플로가 통과로 끝난다. 결과만 보기로 정한 잡에만 붙인다."""
    jobs = _jobs()
    may_fail = {
        name
        for name, job in jobs.items()
        if any(line.startswith("continue-on-error") for line in _settings(job))
    }

    assert {"test", "compat", "frontend"} <= set(jobs)  # 잡을 제대로 읽었다
    assert may_fail == MAY_FAIL


def test_the_overlay_font_job_blocks_and_cannot_pass_by_skipping():
    """화면 문구 합성의 ffmpeg 테스트는 이 잡에서만 돈다 (다른 잡의 번들 ffmpeg 에는 drawtext 가 없다 - A-43).

    그래서 이 잡이 실패하면 워크플로가 실패해야 한다 (결정 11: 통과를 확인한 뒤 막는 검사로). 글꼴이나 글자를 그리는
    ffmpeg 를 찾지 못했을 때 건너뛴 채 통과해서도 안 된다.
    """
    settings = _settings(_jobs()["overlay-font"])

    assert not any(line.startswith("continue-on-error") for line in settings)
    for switch in ("FRAME_REQUIRE_FFMPEG", "FRAME_REQUIRE_KOREAN_FONT", "FRAME_REQUIRE_DRAWTEXT"):
        assert f'{switch}: "1"' in settings, switch
    (install,) = [line for line in settings if "apt-get install" in line]
    assert {"fonts-noto-cjk", "ffmpeg"} <= set(install.split())
    assert any("pytest tests/contract/test_overlay_contract.py" in line for line in settings)


def test_the_test_jobs_keep_running_on_the_bundled_ffmpeg():
    """test · compat 잡은 video extra 가 가져오는 번들 ffmpeg 로 돈다 (pip 로 설치한 사용자가 받는 것 - A-42).

    시스템 ffmpeg 를 깔면 PATH 의 것이 번들보다 먼저 쓰여 그 잡의 모든 영상 테스트가 러너의 판으로 옮겨 간다.
    그래서 화면 문구 합성은 이 잡들에 합치지 않고 따로 둔다 (overlay-font).
    """
    jobs = _jobs()

    for name in ("test", "compat"):
        assert "apt-get" not in jobs[name], name
        assert '".[dev,llm,video]"' in jobs[name], name
