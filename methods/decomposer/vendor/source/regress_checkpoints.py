#!/usr/bin/env python3
"""Reprocess every sample in prior checkpoints under the current frozen code."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import time

from audit_first100 import audit_run
from decomposer import VERSION
from decomposer.common import sha256, write_json
from decomposer.knowledge import merge_into
from decomposer.pipeline import execute


def _identity(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", action="append", required=True, type=Path)
    parser.add_argument("--manifest", default="experiments/v6-first100-server-20260917/manifest.json", type=Path)
    parser.add_argument("--images", default="datasets/benchmark_raw/images", type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    root = args.out.resolve(); root.mkdir(parents=True, exist_ok=True)
    with (root / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        checkpoints = []
        sample_indices = []
        for supplied in args.checkpoint:
            path = supplied.resolve()
            checkpoint_path = path if path.name == "checkpoint.json" else path / "checkpoint.json"
            checkpoint = json.loads(checkpoint_path.read_text())
            if not checkpoint.get("frozen") or not checkpoint.get("all_terminal_success"):
                raise ValueError(f"checkpoint is not eligible for cumulative regression: {checkpoint_path}")
            checkpoints.append({"path": str(checkpoint_path), "sha256": sha256(checkpoint_path), "stage": checkpoint["stage"]})
            sample_indices.extend(range(checkpoint["stage"]["start"], checkpoint["stage"]["end"] + 1))
        sample_indices = sorted(set(sample_indices))
        manifest = json.loads(args.manifest.read_text())
        config = json.loads(args.config.read_text())
        knowledge = root / "knowledge"
        seed = config.get("knowledge", {}).get("seed_path") or config.get("refinement", {}).get("knowledge_path")
        if not knowledge.exists():
            merge_into(knowledge, seed) if seed and Path(seed).is_dir() else knowledge.mkdir(parents=True)
        config.setdefault("knowledge", {})["path"] = str(knowledge)
        config.setdefault("refinement", {})["knowledge_path"] = str(knowledge)
        write_json(root / "frozen-config.json", config)
        source_files = (
            [Path(__file__), Path(__file__).parent / "train_first100.py", Path(__file__).parent / "audit_first100.py"]
            + sorted((Path(__file__).parent / "decomposer").glob("*.py"))
            + sorted((Path(__file__).parent / "prompts").glob("*.md"))
            + sorted((Path(__file__).parent / "tools").glob("*.py"))
            + [Path(__file__).parent / "backend" / "compile.mjs"]
        )
        identity = {
            "schema_version": "decomposer.cumulative-regression.v1", "version": VERSION,
            "manifest": str(args.manifest.resolve()), "manifest_sha256": sha256(args.manifest),
            "config_sha256": _identity(config), "checkpoints": checkpoints,
            "sample_indices": sample_indices,
            "source_hashes": {str(path.relative_to(Path(__file__).parent)): sha256(path) for path in source_files},
        }
        identity_path = root / "regression-identity.json"
        if identity_path.exists() and json.loads(identity_path.read_text()) != identity:
            raise ValueError("regression identity changed; use a new output directory")
        write_json(identity_path, identity)
        progress_path = root / "progress.json"
        progress = json.loads(progress_path.read_text()) if args.resume and progress_path.exists() else {"status": "running", "samples": []}
        done = {row["sample_index"] for row in progress["samples"] if row.get("passed") is True}
        for index in sample_indices:
            if index in done:
                continue
            sample = manifest["samples"][index - 1]; sample_id = sample["sample_id"]
            matches = list(args.images.glob(sample_id + ".*"))
            if not matches or (sample.get("sha256") and sha256(matches[0]) != sample["sha256"]):
                row = {"sample_index": index, "sample_id": sample_id, "passed": False, "reason": "missing-or-hash-mismatched-input"}
            else:
                started = time.monotonic(); run = root / "runs" / sample_id
                try:
                    result = execute(matches[0], run, config, render_output=True, resume=args.resume)
                    audit = audit_run(run) if result.get("status") in {"render_success", "structural_success"} else None
                    if audit:
                        write_json(root / f"sample-{index:03d}-audit.json", {**audit, "sample_index": index})
                    row = {
                        "sample_index": index, "sample_id": sample_id, "version": VERSION,
                        "status": result.get("status"), "passed": bool(audit and audit.get("passed")),
                        "seconds": round(time.monotonic() - started, 3),
                        "failures": audit.get("failures", []) if audit else result.get("validation"),
                    }
                except Exception as exc:
                    row = {"sample_index": index, "sample_id": sample_id, "version": VERSION, "passed": False, "reason": f"{type(exc).__name__}: {exc}", "seconds": round(time.monotonic() - started, 3)}
            progress["samples"].append(row); write_json(progress_path, progress)
            if not row["passed"]:
                progress.update(status="failed", failed_sample=row); write_json(progress_path, progress); break
        passed = sum(row.get("passed") is True for row in progress["samples"])
        gate = {
            "schema_version": "decomposer.cumulative-regression-gate.v1",
            "version": VERSION, "requested": len(sample_indices), "processed": len(progress["samples"]),
            "passed": passed, "pass_rate": round(passed / len(sample_indices), 6) if sample_indices else 1.0,
            "gate_passed": passed == len(sample_indices), "checkpoints": checkpoints,
        }
        write_json(root / "regression-gate.json", gate)
        if gate["gate_passed"]:
            progress["status"] = "complete"; write_json(progress_path, progress)
        print(json.dumps(gate, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
