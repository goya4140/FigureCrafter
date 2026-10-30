"""Run a seeded holdout pair without touching its frozen upstream snapshot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from decomposer.asset_policy import decide
from workspace_paths import resolve_path
from seed_representation_experiment import digest, verify


def run_pair(name: str, root: Path, baseline_config: Path, candidate_config: Path,
             candidate_name: str) -> None:
    pair = root / name
    baseline = pair / "baseline"
    candidate = pair / candidate_name
    for directory in (baseline, candidate):
        verify(directory)
    baseline_hashes = json.loads((baseline / "frozen-upstream.json").read_text())["files"]
    candidate_hashes = json.loads((candidate / "frozen-upstream.json").read_text())["files"]
    if baseline_hashes != candidate_hashes:
        raise RuntimeError(f"holdout pair has different upstream hashes: {name}")
    for variant, config in (("baseline", baseline_config), (candidate_name, candidate_config)):
        directory = pair / variant
        verify(directory)
        input_record = json.loads((directory / "input/input.json").read_text())
        source = resolve_path(input_record["source"])
        if digest(source) != input_record["source_sha256"]:
            raise RuntimeError(f"source image changed: {source}")
        recognition = json.loads((directory / "recognition/recognition.json").read_text())
        evidence = json.loads((directory / "observations/evidence-ir.json").read_text())
        asset_decisions = json.loads((directory / "representation/asset-decisions.json").read_text())
        if decide(recognition, evidence, json.loads(config.read_text())) != asset_decisions:
            raise RuntimeError(f"asset policy would change frozen decisions: {directory}")
        config_snapshot = directory / "experiment-config.json"
        config_bytes = config.read_bytes()
        if config_snapshot.exists() and config_snapshot.read_bytes() != config_bytes:
            raise RuntimeError(f"experiment config changed: {directory}")
        config_snapshot.write_bytes(config_bytes)
        command = [
            sys.executable, "run.py", "--input", str(source),
            "--config", str(config), "--out", str(directory), "--resume",
        ]
        log = pair / f"{variant}.log"
        with log.open("w") as stream:
            result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, check=False)
        verify(directory)
        if result.returncode:
            raise RuntimeError(f"{name}/{variant} exited {result.returncode}; see {log}")
    comparison = pair / f"compare-baseline-{candidate_name}.json"
    subprocess.run([
        sys.executable, "tools/compare_representation_experiments.py",
        str(pair / "baseline"), str(pair / candidate_name),
        "--out", str(comparison),
    ], check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("names", nargs="+")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2] / "experiments/new_runs/holdouts")
    parser.add_argument("--baseline-config", type=Path, default=Path(__file__).resolve().parents[1] / "configs/representation_optimization/config-baseline.json")
    parser.add_argument("--candidate-config", type=Path, default=Path(__file__).resolve().parents[1] / "configs/representation_optimization/config-shards4.json")
    parser.add_argument("--candidate-name", default="shards4")
    args = parser.parse_args()
    for name in args.names:
        print(f"Running {name}", flush=True)
        run_pair(name, args.root, args.baseline_config, args.candidate_config,
                 args.candidate_name)
        print(f"Completed {name}", flush=True)
