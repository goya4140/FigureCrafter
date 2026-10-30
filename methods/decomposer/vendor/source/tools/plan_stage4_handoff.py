#!/usr/bin/env python3
"""Read-only Stage-3-to-Stage-4 handoff plan; never launches training."""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from audit_first100 import audit_run
from decomposer.common import sha256
from tools.audit_memory_eligibility import audit as audit_memory
from tools.verify_stage_snapshot import verify


def plan(batch: Path) -> dict:
    batch = batch.resolve()
    snapshot = verify(batch)
    if (not snapshot.get("identity_and_snapshot_consistent")
            or not snapshot.get("complete_coverage")
            or not snapshot.get("checkpoint_present")
            or snapshot.get("stage", {}).get("stage") != 3):
        raise ValueError("Stage 3 must have 25 consistent terminal rows and a verified checkpoint")

    progress = json.loads((batch / "progress.json").read_text())
    if progress.get("status") not in {"complete", "complete_checkpoint_blocked"}:
        raise ValueError("Stage 3 has not reached a complete terminal state")
    checkpoint = json.loads((batch / "checkpoint.json").read_text())
    frozen_config = json.loads((batch / "frozen-config.json").read_text())
    knowledge = audit_memory(batch / "knowledge")

    audit_codes: Counter[str] = Counter()
    independent_passed = 0
    sample_quality_ledger = []
    for row in progress["samples"]:
        result = audit_run(batch / "runs" / row["sample_id"])
        independent_passed += bool(result["passed"])
        codes = [failure["code"] for failure in result.get("failures", [])]
        audit_codes.update(codes)
        sample_quality_ledger.append({
            "sample_index": row["sample_index"],
            "sample_id": row["sample_id"],
            "production_status": row["status"],
            "independent_passed": bool(result["passed"]),
            "independent_failure_codes": codes,
        })
    quality_eligible = (checkpoint.get("checkpoint_eligible") is True
                        and independent_passed == 25)

    successor_config = copy.deepcopy(frozen_config)
    successor_code_root = Path(__file__).resolve().parents[1]
    ocr_command = successor_config.get("detectors", {}).get("ocr", {}).get("command")
    if not isinstance(ocr_command, list) or len(ocr_command) < 2 or Path(ocr_command[1]).name != "apple_vision_ocr.py":
        raise ValueError("Cannot safely rebind inherited OCR command to successor code")
    successor_config["detectors"]["ocr"]["command"] = [
        ocr_command[0], str(successor_code_root / "tools" / "apple_vision_ocr.py"), *ocr_command[2:]
    ]
    successor_config.setdefault("knowledge", {}).pop("path", None)
    successor_config["knowledge"]["seed_path"] = str(batch / "knowledge")
    successor_config.setdefault("refinement", {}).pop("knowledge_path", None)
    successor_config["evolution_handoff"] = {
        "source_stage": 3,
        "successor_code_root": str(successor_code_root),
        "source_checkpoint_sha256": sha256(batch / "checkpoint.json"),
        "source_training_identity_sha256": sha256(batch / "training-identity.json"),
        "source_knowledge_tree_sha256": checkpoint["knowledge_snapshot"]["tree_sha256"],
        "quality_status": "eligible" if quality_eligible else "provisional",
        "unresolved_sample_indices": [item["sample_index"] for item in sample_quality_ledger
                                      if not item["independent_passed"]],
    }
    return {
        "schema_version": "figure2pptx.stage4-handoff-plan.v1",
        "source_batch": str(batch),
        "source_stage_status": progress["status"],
        "source_snapshot": snapshot,
        "independent_audits_passed": independent_passed,
        "independent_failure_codes": dict(sorted(audit_codes.items())),
        "sample_quality_ledger": sample_quality_ledger,
        "quality_status": "eligible" if quality_eligible else "provisional",
        "knowledge": {
            "memory_count": knowledge["memory_count"],
            "eligible_count": knowledge["eligible_count"],
            "eligible_memory_ids": knowledge["eligible_memory_ids"],
        },
        "unresolved_checkpoint_blockers": progress.get("checkpoint_blockers", []),
        "stage4_config": successor_config,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage3_batch", type=Path)
    args = parser.parse_args()
    print(json.dumps(plan(args.stage3_batch), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
