"""Recanonicalize a saved model response, then optionally run normal downstream stages."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from decomposer.common import write_json
from decomposer.representation import canonicalize
from decomposer.representation_compact import decode as decode_compact
from workspace_paths import resolve_path
from seed_representation_experiment import digest, seed, verify


def prepare(source: Path, target: Path, config_path: Path) -> None:
    seed(source, target)
    shutil.copytree(source / "calls/representation", target / "calls/representation")
    shutil.copy2(config_path, target / "experiment-config.json")
    recognition = json.loads((target / "recognition/recognition.json").read_text())
    decisions = json.loads((target / "representation/asset-decisions.json").read_text())
    metadata = json.loads((target / "calls/representation/metadata.json").read_text())
    if metadata.get("status") != "complete" or metadata.get("fallback_used"):
        raise ValueError("source Representation model call did not complete")
    config = json.loads(config_path.read_text())
    if config.get("representation", {}).get("strategy") == "compact_wire":
        proposal = decode_compact(
            json.loads((source / "calls/representation/response.txt").read_text()),
            recognition,
        )
    else:
        proposal = json.loads((source / "representation/model-output.json").read_text())
    write_json(target / "representation/model-output.json", proposal)
    result, report = canonicalize(
        proposal, recognition, target / "input/normalized.png", target / "representation", decisions,
    )
    report["model"] = metadata
    write_json(target / "representation/canonicalization-report.json", report)
    write_json(target / "representation/assets.json", result["assets"])
    write_json(target / "representation/representation.json", result)
    write_json(target / "representation-replay.json", {
        "source_run": str(source.resolve()),
        "source_model_output_sha256": digest(source / "representation/model-output.json"),
        "source_model_response_sha256": digest(source / "calls/representation/response.txt")
        if (source / "calls/representation/response.txt").is_file() else None,
        "model_call_reused": True,
        "downstream_walltime_from_original_call": "not_comparable_after_replay",
    })
    verify(target)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    config = args.config or args.source / "experiment-config.json"
    if not config.is_file():
        parser.error(f"experiment config is missing: {config}")
    prepare(args.source, args.target, config)
    print(f"prepared replay at {args.target}", flush=True)
    if args.execute:
        record = json.loads((args.target / "input/input.json").read_text())
        command = [sys.executable, "run.py", "--input", str(resolve_path(record["source"])),
                   "--config", str(args.target / "experiment-config.json"),
                   "--out", str(args.target), "--resume"]
        subprocess.run(command, check=True)
        verify(args.target)
