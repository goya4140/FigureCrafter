#!/usr/bin/env python3
"""Isolated, source-ink-guided native text style search; never adopt output."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from decomposer.common import read_json, sha256, write_json
from decomposer.native_text_style_probe import search_owner


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", required=True, type=Path)
    parser.add_argument("--scene", required=True, type=Path)
    parser.add_argument("--before-preview", required=True, type=Path)
    parser.add_argument("--owner-id", required=True)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    run, scene_path, before_preview = (args.source_run.resolve(), args.scene.resolve(),
                                       args.before_preview.resolve())
    output = args.out.resolve()
    scene = read_json(scene_path)
    reference = run / "input" / "normalized.png"
    config = read_json(args.config)
    baseline_report = read_json(scene_path.parent / "report.json")
    result = search_owner(scene, reference, before_preview, args.owner_id,
                          config, output, baseline_report)
    result["source_run"] = str(run)
    result["source_scene_sha256"] = sha256(scene_path)
    write_json(output / "search-report.json", result)
    best = next((item for item in result["attempts"]
                 if item.get("candidate_id") == result["best_candidate_id"]), None)
    print({"owner_id": args.owner_id, "attempts": len(result["attempts"]),
           "qualified": sum(bool(item.get("qualified")) for item in result["attempts"]),
           "best": result["best_candidate_id"],
           "visual": best["assessment"]["after_global"] if best else None})


if __name__ == "__main__":
    main()
