#!/usr/bin/env python3
"""Isolated full-chain replay of a historical run, never a formal trainer resume."""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from decomposer.common import read_json, sha256, write_json
from decomposer.pipeline import execute


def _verified_source(baseline: Path) -> tuple[Path, str]:
    """Reject a historical replay if the original input file has drifted."""
    record = read_json(baseline / "input" / "input.json")
    source = Path(record["source"]).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    expected = str(record["source_sha256"])
    if sha256(source) != expected:
        raise ValueError("baseline source image hash mismatch")
    return source, expected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-run", required=True, type=Path)
    parser.add_argument("--baseline-config", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    baseline = args.baseline_run.resolve()
    out = args.out.resolve()
    if out.exists():
        raise FileExistsError(out)
    source, source_hash = _verified_source(baseline)
    original_config = read_json(args.baseline_config)
    old_knowledge = Path(original_config["knowledge"]["path"]).resolve()
    if not old_knowledge.is_dir():
        raise FileNotFoundError(old_knowledge)
    out.mkdir(parents=True)
    knowledge = out / "knowledge-snapshot"
    shutil.copytree(old_knowledge, knowledge)
    config = {
        **original_config,
        "knowledge": {**original_config["knowledge"], "path": str(knowledge)},
        "refinement": {**original_config.get("refinement", {}), "knowledge_path": str(knowledge)},
    }
    write_json(out / "replay-config.json", config)
    write_json(out / "replay-identity.json", {
        "scope": "isolated-full-chain-diagnostic-not-formal-training",
        "baseline_run": str(baseline),
        "baseline_config_sha256": sha256(args.baseline_config),
        "source_sha256": source_hash,
        "baseline_normalized_sha256": sha256(baseline / "input" / "normalized.png"),
        "knowledge_snapshot_source": str(old_knowledge),
        "knowledge_caveat": "Final baseline knowledge snapshot may include later samples; not an exact historical point-in-time replay.",
    })
    run = out / "run"
    status = execute(source, run, config)
    baseline_status = read_json(baseline / "status.json")
    baseline_thresholds = baseline_status.get("validation", {}).get("visual", {}).get("thresholds")
    replay_thresholds = status.get("validation", {}).get("visual", {}).get("thresholds")
    with Image.open(run / "input" / "normalized.png") as image:
        replay_pixels = np.asarray(image.convert("RGBA"))
    with Image.open(baseline / "input" / "normalized.png") as image:
        baseline_pixels = np.asarray(image.convert("RGBA"))
    same_pixels = bool(np.array_equal(replay_pixels, baseline_pixels))
    same_thresholds = baseline_thresholds is not None and baseline_thresholds == replay_thresholds
    write_json(out / "replay-summary.json", {
        "status": status["status"],
        "baseline_status": baseline_status["status"],
        "same_visual_thresholds": same_thresholds,
        "baseline_visual_thresholds": baseline_thresholds,
        "replay_visual_thresholds": replay_thresholds,
        "same_normalized_input_bytes": sha256(run / "input" / "normalized.png")
        == sha256(baseline / "input" / "normalized.png"),
        "same_normalized_input_pixels": same_pixels,
        "regression_comparable": same_pixels and same_thresholds,
        "run": str(run),
    })
    print(read_json(out / "replay-summary.json"))


if __name__ == "__main__":
    main()
