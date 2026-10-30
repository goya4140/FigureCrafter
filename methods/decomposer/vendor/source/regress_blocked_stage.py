#!/usr/bin/env python3
"""Cumulative regression from immutable terminal upstream artifacts of a blocked stage.

Recognition, Representation, and Layout are reused only when their current
validators accept them. Assembly, PPTX compilation, refinement, every
post-render repair, validation, and independent audit run under current code.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import time

from audit_first100 import audit_run
from decomposer import VERSION
from decomposer.common import sha256, write_json
from decomposer.evolution import PROTOCOL_VERSION, freeze_checkpoint, stage_spec, write_observation
from decomposer.pipeline import execute


UPSTREAM_DIRS = ("input", "observations", "recognition", "representation", "layout", "calls")
TERMINAL_PROCESSED = {"render_success", "structural_success", "validation_failed"}


def identity_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-stage", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--stop-after", type=int, help="optional bounded smoke; identity still covers all 25 samples")
    args = parser.parse_args()
    source, root = args.source_stage.resolve(), args.out.resolve()
    if root.exists() and not args.resume:
        raise FileExistsError(root)
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        checkpoint_path = source / "checkpoint.json"
        checkpoint = json.loads(checkpoint_path.read_text())
        spec = stage_spec(1)
        if (not checkpoint.get("frozen") or checkpoint.get("stage") != spec
                or checkpoint.get("sample_count") != 25):
            raise ValueError("source must be a frozen complete Stage 1 checkpoint, even if quality-blocked")
        for item in checkpoint["artifacts"]:
            path = source / item["path"]
            if not path.is_file() or sha256(path) != item["sha256"]:
                raise ValueError(f"source checkpoint artifact hash mismatch: {item['path']}")
        for item in checkpoint.get("observation_reports", []) + checkpoint.get("knowledge_snapshot", {}).get("files", []):
            path = source / item["path"]
            if not path.is_file() or sha256(path) != item["sha256"]:
                raise ValueError(f"source checkpoint evidence hash mismatch: {item['path']}")
        for name, key in (("training-identity.json", "training_identity_sha256"),
                          ("frozen-config.json", "frozen_config_sha256"),
                          ("progress.json", "progress_sha256")):
            if checkpoint.get(key) and sha256(source / name) != checkpoint[key]:
                raise ValueError(f"source checkpoint identity hash mismatch: {name}")
        manifest = json.loads(args.manifest.read_text())
        if len(manifest["samples"]) < 25:
            raise ValueError("manifest has fewer than 25 samples")
        source_progress = json.loads((source / "progress.json").read_text())
        source_rows = {item["sample_index"]: item for item in source_progress["samples"]}
        for index in range(1, 26):
            sample = manifest["samples"][index - 1]
            if source_rows[index]["sample_id"] != sample["sample_id"]:
                raise ValueError(f"source/manifest sample identity mismatch at {index}")
            if source_rows[index]["status"] not in TERMINAL_PROCESSED:
                raise ValueError(f"source sample {index} is not terminal processed")
        knowledge = root / "knowledge"
        if not knowledge.exists():
            source_knowledge = source / "knowledge"
            if source_knowledge.is_dir():
                shutil.copytree(source_knowledge, knowledge)
            else:
                knowledge.mkdir()
        config = json.loads(args.config.read_text())
        config.setdefault("knowledge", {})["path"] = str(knowledge)
        config.setdefault("refinement", {})["knowledge_path"] = str(knowledge)
        frozen_config_path = root / "frozen-config.json"
        if frozen_config_path.exists() and json.loads(frozen_config_path.read_text()) != config:
            raise ValueError("frozen cumulative-regression config drift")
        write_json(frozen_config_path, config)
        code_root = Path(__file__).parent
        source_files = ([code_root / "regress_blocked_stage.py", code_root / "audit_first100.py"]
                        + sorted((code_root / "decomposer").glob("*.py"))
                        + sorted((code_root / "prompts").glob("*.md"))
                        + [code_root / "backend" / "compile.mjs"])
        identity = {
            "schema_version": "decomposer.blocked-stage-regression.v1",
            "protocol_version": PROTOCOL_VERSION, "version": VERSION,
            "source_checkpoint": str(checkpoint_path), "source_checkpoint_sha256": sha256(checkpoint_path),
            "manifest": str(args.manifest.resolve()), "manifest_sha256": sha256(args.manifest),
            "config_sha256": identity_hash(config), "sample_indices": list(range(1, 26)),
            "source_hashes": {str(path.relative_to(code_root)): sha256(path) for path in source_files},
        }
        identity_path = root / "training-identity.json"
        if identity_path.exists() and json.loads(identity_path.read_text()) != identity:
            raise ValueError("regression identity drift; use a new output directory")
        write_json(identity_path, identity)
        progress_path = root / "progress.json"
        progress = json.loads(progress_path.read_text()) if args.resume and progress_path.exists() else {
            "status": "running", "protocol_version": PROTOCOL_VERSION, "stage": spec,
            "regression_source_checkpoint": str(checkpoint_path), "samples": [], "checkpoint_blockers": [],
        }
        complete_ids = {row["sample_index"] for row in progress["samples"]}
        processed_now = 0
        for index in range(1, 26):
            if index in complete_ids:
                continue
            sample_id = manifest["samples"][index - 1]["sample_id"]
            original = source / "runs" / sample_id
            image_record = json.loads((original / "input" / "input.json").read_text())
            image = Path(image_record["source"])
            if (not image.is_file() or sha256(image) != image_record["source_sha256"]
                    or (manifest["samples"][index - 1].get("sha256")
                        and sha256(image) != manifest["samples"][index - 1]["sha256"])):
                raise ValueError(f"input identity drift at sample {index}")
            run = root / "runs" / sample_id
            if run.exists():
                raise ValueError(f"nonterminal regression run already exists; inspect before retry: {run}")
            run.mkdir(parents=True)
            for name in UPSTREAM_DIRS:
                if (original / name).is_dir():
                    shutil.copytree(original / name, run / name)
            started = time.monotonic()
            try:
                status = execute(image, run, config, render_output=True, resume=True)
                reuse = json.loads((run / "resume-report.json").read_text())
                audit = audit_run(run)
                write_json(root / f"sample-{index:03d}-audit.json", {**audit, "sample_index": index})
                passed = status["status"] in {"render_success", "structural_success"} and audit["passed"]
                row = {
                    "sample_index": index, "sample_id": sample_id, "version": VERSION,
                    "status": status["status"], "degraded": status.get("degraded"),
                    "refinement": status.get("refinement"), "validation": status.get("validation"),
                    "audit_passed": audit["passed"], "passed": passed,
                    "resume_phases": reuse["phases"], "seconds": round(time.monotonic() - started, 3),
                }
                if not passed:
                    progress["checkpoint_blockers"].append({
                        "reason": "cumulative_regression_failure", "sample_index": index,
                        "sample_id": sample_id, "failures": audit["failures"],
                    })
            except Exception as exc:
                progress.update(status="interrupted", stop_event={
                    "reason": "execution_failure", "sample_index": index,
                    "sample_id": sample_id, "error": f"{type(exc).__name__}: {exc}",
                })
                write_json(progress_path, progress)
                raise
            progress["samples"].append(row)
            progress["completed"] = len(progress["samples"])
            write_json(progress_path, progress)
            if index % 5 == 0:
                write_observation(root, progress, spec, index)
            processed_now += 1
            if args.stop_after is not None and processed_now >= args.stop_after:
                break
        if len(progress["samples"]) == 25:
            progress["status"] = "complete_checkpoint_blocked" if progress["checkpoint_blockers"] else "complete"
            progress["requested"] = 25
            write_json(progress_path, progress)
            checkpoint_result = freeze_checkpoint(root, progress, spec)
            gate = {
                "schema_version": "decomposer.blocked-stage-regression-gate.v1",
                "version": VERSION, "requested": 25, "processed": 25,
                "passed": sum(row["passed"] for row in progress["samples"]),
                "gate_passed": checkpoint_result["checkpoint_eligible"],
                "checkpoint_sha256": sha256(root / "checkpoint.json"),
                "source_checkpoint_sha256": sha256(checkpoint_path),
            }
            write_json(root / "regression-gate.json", gate)
            print(json.dumps(gate, ensure_ascii=False))
        else:
            print(json.dumps({"version": VERSION, "processed": len(progress["samples"]),
                              "checkpoint_blockers": len(progress["checkpoint_blockers"]),
                              "status": progress["status"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
