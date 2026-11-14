"""OpenAPI 명세 내보내기와 계약 비교 (프론트 타입 생성·API 변경 검토용).

    python -m frame.tools.export_openapi                 # docs/openapi.json 갱신
    python -m frame.tools.export_openapi out.json

tests/api/test_openapi_contract.py 가 저장된 명세와 현재 앱의 '필드 구조'를 비교한다.
필드가 추가·삭제·이름 변경되면 테스트가 실패하므로, 명세를 다시 내보내고 PR 에서 프론트 담당자에게 알린다.
(pydantic 버전에 따른 표기 차이에는 흔들리지 않도록 경로·메서드·상태 코드·필드 이름만 비교한다.)
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from frame.config import Settings, build_container
from frame.presentation.app import create_app

DEFAULT_OUTPUT = Path("docs/openapi.json")


def build_spec() -> dict[str, Any]:
    return create_app(build_container(Settings())).openapi()


def render(spec: dict[str, Any]) -> str:
    return json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _resolve(schema: dict[str, Any], components: dict[str, Any]) -> dict[str, Any]:
    while True:
        if "$ref" in schema:
            schema = components[schema["$ref"].rsplit("/", 1)[-1]]
        elif "anyOf" in schema:
            options = [s for s in schema["anyOf"] if s.get("type") != "null"]
            schema = options[0] if options else {}
        elif "allOf" in schema and len(schema["allOf"]) == 1:
            schema = schema["allOf"][0]
        else:
            return schema


def _fields(
    schema: dict[str, Any], components: dict[str, Any], prefix: str, depth: int = 0
) -> set[str]:
    node = _resolve(schema, components)
    if depth > 8:
        return set()
    if node.get("type") == "array" and "items" in node:
        return _fields(node["items"], components, f"{prefix}[]", depth + 1) or {f"{prefix}[]"}
    props = node.get("properties", {})
    if not props:
        return {prefix} if prefix else set()
    out: set[str] = set()
    for name, sub in props.items():
        path = f"{prefix}.{name}" if prefix else name
        out.add(path)
        out |= _fields(sub, components, path, depth + 1)
    return out


def contract_signature(spec: dict[str, Any]) -> set[str]:
    """경로·메서드·파라미터·요청/응답 필드 이름의 집합."""
    components = spec.get("components", {}).get("schemas", {})
    signature: set[str] = set()
    for path, operations in spec.get("paths", {}).items():
        for method, op in operations.items():
            head = f"{method.upper()} {path}"
            signature.add(head)
            for param in op.get("parameters", []):
                signature.add(f"{head} param:{param['name']}")
            content = op.get("requestBody", {}).get("content", {})
            for media_type in (
                "application/json",
                "multipart/form-data",
            ):  # 업로드는 multipart (#12)
                body = content.get(media_type, {})
                if "schema" in body:
                    signature |= {
                        f"{head} body:{f}" for f in _fields(body["schema"], components, "")
                    }
            for status, response in op.get("responses", {}).items():
                signature.add(f"{head} -> {status}")
                content = response.get("content", {}).get("application/json", {})
                if "schema" in content:
                    signature |= {
                        f"{head} -> {status}:{f}"
                        for f in _fields(content["schema"], components, "")
                    }
    return signature


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FRAME OpenAPI 명세를 파일로 내보낸다")
    parser.add_argument("output", nargs="?", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args(argv)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(build_spec()), encoding="utf-8")
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
