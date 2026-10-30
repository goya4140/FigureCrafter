#!/usr/bin/env python3
"""OpenAI-compatible vision adapter for the external-json backend.

The adapter keeps provider credentials out of files and stdout.  Stdout contains
only the stage JSON result; redacted request/usage diagnostics are written to
stderr so the framework can retain them without leaking the API key or image.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request
from typing import Optional


def required_environment(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"required environment variable is unset: {name}")
    return value


def strip_fence(value: str) -> str:
    value = value.strip()
    if value.startswith("```") and value.endswith("```"):
        lines = value.splitlines()
        value = "\n".join(lines[1:-1]).strip()
    return value


def build_body(
    *,
    stage: str,
    model: str,
    prompt: str,
    schema: dict,
    mime: str,
    image_data: str,
    response_mode: str,
    reasoning_effort: Optional[str],
    max_tokens: Optional[int],
    image_detail: str,
) -> dict:
    """Build a request without exposing transport-only values such as the key."""
    schema_text = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    system = (
        "Use only the supplied image and prompt. Use no tools. Return only valid JSON. "
        f"The JSON must match this {stage} JSON Schema exactly: {schema_text}"
    )
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime};base64,{image_data}",
                            "detail": image_detail,
                        },
                    },
                ],
            },
        ],
        "temperature": 0,
    }
    if reasoning_effort:
        body["reasoning_effort"] = reasoning_effort
    if max_tokens:
        body["max_tokens"] = max_tokens
    if response_mode == "json_schema":
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": f"figure2pptx_{stage}", "strict": True, "schema": schema},
        }
    elif response_mode == "json_object":
        body["response_format"] = {"type": "json_object"}
    return body


def response_metadata(payload: dict, attempt: int = 1) -> dict:
    choice = payload.get("choices", [{}])[0] if payload.get("choices") else {}
    return {
        "provider_metadata": {
            "attempt": attempt,
            "id": payload.get("id"),
            "model": payload.get("model"),
            "finish_reason": choice.get("finish_reason"),
            "usage": payload.get("usage"),
            "system_fingerprint": payload.get("system_fingerprint"),
        }
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--base-url-env", required=True)
    parser.add_argument("--api-key-env", required=True)
    parser.add_argument("--model-env", required=True)
    parser.add_argument("--response-mode", choices=("json_schema", "json_object", "prompt_only"), default="json_schema")
    parser.add_argument("--reasoning-effort", choices=("none", "low", "high", "max"))
    parser.add_argument("--max-tokens", type=int)
    parser.add_argument("--image-detail", choices=("low", "high", "original", "auto"), default="original")
    parser.add_argument(
        "--empty-content-retries",
        type=int,
        default=0,
        help="Retry only the documented JSON-mode empty-content failure; other failures remain fail-closed.",
    )
    args = parser.parse_args()

    base_url = required_environment(args.base_url_env).rstrip("/")
    api_key = required_environment(args.api_key_env)
    model = required_environment(args.model_env)
    prompt = sys.stdin.read()
    schema = json.loads(args.schema.read_text(encoding="utf-8"))
    mime = mimetypes.guess_type(args.image.name)[0] or "image/png"
    image_data = base64.b64encode(args.image.read_bytes()).decode("ascii")

    body = build_body(
        stage=args.stage,
        model=model,
        prompt=prompt,
        schema=schema,
        mime=mime,
        image_data=image_data,
        response_mode=args.response_mode,
        reasoning_effort=args.reasoning_effort,
        max_tokens=args.max_tokens,
        image_detail=args.image_detail,
    )

    if args.empty_content_retries < 0:
        raise ValueError("--empty-content-retries must be non-negative")
    parsed = None
    for attempt in range(1, args.empty_content_retries + 2):
        request = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=1700) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:4000]
            raise RuntimeError(f"provider HTTP {exc.code}: {detail}") from exc
        print(json.dumps(response_metadata(payload, attempt), ensure_ascii=False), file=sys.stderr)
        content = payload["choices"][0]["message"].get("content")
        if isinstance(content, list):
            content = "".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
        if content is not None and str(content).strip():
            parsed = json.loads(strip_fence(str(content)))
            break
        print(
            json.dumps({"provider_warning": "empty_content", "attempt": attempt}, ensure_ascii=False),
            file=sys.stderr,
        )
    if parsed is None:
        raise RuntimeError(
            f"provider returned empty content after {args.empty_content_retries + 1} attempt(s)"
        )
    print(json.dumps(parsed, ensure_ascii=False))


if __name__ == "__main__":
    main()
