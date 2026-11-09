"""브리프 → IR 호출 테스트 CLI.

    python -m frame.tools.structure_cli --brief "카페 숏폼 ..." --duration-ms 12000 [--json]
    python -m frame.tools.structure_cli --file brief.txt

어댑터는 환경 변수로 고른다 (.env.example 참고):
    FRAME_STRUCTURER=llm FRAME_LLM_PROVIDER=openai FRAME_LLM_KEY_FILE=F:/key.txt FRAME_LLM_MODEL=gpt-4.1-mini
로컬 3090 (vLLM 등 OpenAI 호환 서버):
    FRAME_LLM_BASE_URL=http://localhost:8001/v1 FRAME_LLM_MODEL=<모델명> FRAME_LLM_STRICT_SCHEMA=false
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from frame.application.errors import AdapterError
from frame.application.ports.structurer import StructuringRequest
from frame.config import Settings, build_container


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="브리프를 IR 로 구조화해 본다")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--brief", help="브리프 텍스트")
    source.add_argument("--file", help="브리프 파일 경로 (UTF-8)")
    parser.add_argument("--duration-ms", type=int, default=None, help="목표 길이 (ms)")
    parser.add_argument("--json", action="store_true", help="IR 을 JSON 으로 출력")
    args = parser.parse_args(argv)

    brief = args.brief if args.brief else Path(args.file).read_text(encoding="utf-8")
    settings = replace(Settings.from_env(), jobs="recording", persistence="memory")
    try:
        container = build_container(settings)
    except AdapterError as exc:
        print(f"설정 오류 {exc.code}: {exc}", file=sys.stderr)
        return 2
    structurer = container.structurer
    print(
        f"[structure] structurer={settings.structurer} provider={settings.llm_provider} "
        f"model={settings.llm_model} base_url={settings.llm_base_url or '-'}",
        file=sys.stderr,
    )

    try:
        ir = structurer.structure(StructuringRequest(brief, target_duration_ms=args.duration_ms))
    except AdapterError as exc:
        print(f"FAILED {exc.code}: {exc}", file=sys.stderr)
        return 1

    attempts = getattr(structurer, "attempts", None)
    completion = getattr(structurer, "last_completion", None)
    if attempts is not None:
        tokens = ""
        if completion is not None and completion.input_tokens is not None:
            tokens = f", tokens in/out={completion.input_tokens}/{completion.output_tokens}"
        print(f"[structure] attempts={attempts}{tokens}", file=sys.stderr)

    if args.json:
        print(ir.to_json())
        return 0
    print(f"entities ({len(ir.entities)}):")
    for entity in ir.entities:
        print(f"  - {entity.entity_key} [{entity.kind}] {entity.description}")
    print(f"scenes ({len(ir.scenes)}), total {ir.total_duration_ms} ms:")
    for scene in ir.scenes:
        print(
            f"  - {scene.scene_key} {scene.planned_start_ms}-{scene.planned_end_ms} ms "
            f"{list(scene.entity_keys)}: {scene.description}"
        )
        for req in scene.requirements:
            target = req.entity_key or "(scene)"
            relation = f" -> {req.related_entity_key}" if req.related_entity_key else ""
            print(f"      {target}.{req.predicate} = {req.expected_value}{relation}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
