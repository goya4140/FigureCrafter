#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from decomposer.pipeline import execute


def merge(base: dict, override: dict) -> dict:
    """Recursively overlay a model/runtime profile without mutating the base config."""
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = value
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Decompose a scientific figure into JSON stages and compile an editable PPTX.")
    parser.add_argument("--input", required=True, help="Source figure image")
    parser.add_argument("--config", required=True, help="Runtime/model JSON config")
    parser.add_argument("--model-profile", help="Optional model-only JSON profile overlaid on --config")
    parser.add_argument("--out", required=True, help="Output run directory")
    parser.add_argument("--fixture-dir", help="Optional recognition/representation/layout JSON proposals for offline runs")
    parser.add_argument("--no-render", action="store_true", help="Compile and inspect PPTX without LibreOffice rendering")
    parser.add_argument("--resume", action="store_true", help="Reuse validated completed canonical stages in the output directory")
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    if args.model_profile:
        config = merge(config, json.loads(Path(args.model_profile).read_text()))
    status = execute(args.input, args.out, config, fixture_dir=args.fixture_dir, render_output=not args.no_render, resume=args.resume)
    print(json.dumps(status, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
