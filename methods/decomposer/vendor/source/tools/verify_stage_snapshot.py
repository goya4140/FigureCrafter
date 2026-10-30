#!/usr/bin/env python3
"""Read-only stage identity/checkpoint verifier; never reprocesses samples.

This verifies the frozen ledger and recorded file hashes, not visual quality.
Use an independent run audit as a separate quality gate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


TERMINAL = {"render_success", "structural_success", "validation_failed", "runtime_failed", "failed", "missing_input", "hash_mismatch"}
PRODUCTION_SUCCESS = {"render_success", "structural_success"}


def read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def digest(path: Path) -> str:
    block_hash = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            block_hash.update(block)
    return block_hash.hexdigest()


def verify(batch: Path) -> dict:
    batch = batch.resolve()
    errors: list[str] = []
    identity_path, progress_path, checkpoint_path = (
        batch / "training-identity.json", batch / "progress.json", batch / "checkpoint.json"
    )
    identity, progress = read_json(identity_path), read_json(progress_path)
    if "stage" not in identity:
        return {
            "batch": str(batch), "identity_and_snapshot_consistent": False,
            "errors": ["not a stage-training identity; cumulative regression requires its own verifier"],
        }
    spec = identity["stage"]
    indices = list(range(int(spec["start"]), int(spec["end"]) + 1))
    if identity.get("sample_indices") != indices or identity.get("requested_samples") != len(indices):
        errors.append("training identity does not cover the exact stage range")
    if progress.get("stage") != spec:
        errors.append("progress stage identity differs from training identity")
    manifest_path = Path(identity["manifest"])
    if not manifest_path.is_file() or digest(manifest_path) != identity.get("manifest_sha256"):
        errors.append("manifest missing or hash mismatch")
        manifest_samples = []
    else:
        manifest_samples = read_json(manifest_path)["samples"]
    rows = progress.get("samples", [])
    seen: set[int] = set()
    status_counts: dict[str, int] = {}
    audit_count = audit_passed = 0
    for row in rows:
        index = row.get("sample_index")
        if not isinstance(index, int) or index not in indices or index in seen:
            errors.append(f"duplicate or out-of-range progress row: {index}")
            continue
        seen.add(index)
        status = row.get("status", "missing")
        status_counts[status] = status_counts.get(status, 0) + 1
        if index <= len(manifest_samples) and row.get("sample_id") != manifest_samples[index - 1].get("sample_id"):
            errors.append(f"sample {index}: manifest ID mismatch")
        run_status = batch / "runs" / str(row.get("sample_id")) / "status.json"
        if status in PRODUCTION_SUCCESS or status == "validation_failed":
            if not run_status.is_file():
                errors.append(f"sample {index}: terminal run status missing")
            elif read_json(run_status).get("status") != status:
                errors.append(f"sample {index}: run/progress status mismatch")
        if status in PRODUCTION_SUCCESS:
            for relative in ("output/scene.json", "output/editable.pptx",
                             "output/page-manifest.json", "output/validation.json"):
                if not (run_status.parent / relative).is_file():
                    errors.append(f"sample {index}: successful run artifact missing: {relative}")
        if status in PRODUCTION_SUCCESS or status == "validation_failed":
            audit_path = batch / f"sample-{index:03d}-audit.json"
            if audit_path.is_file():
                audit_count += 1
                audit_passed += bool(read_json(audit_path).get("passed"))
            else:
                errors.append(f"sample {index}: frozen independent audit missing")
    if progress.get("completed") != len(rows):
        errors.append("progress.completed differs from row count")
    complete_coverage = seen == set(indices)
    if progress.get("status", "").startswith("complete") and not complete_coverage:
        errors.append("progress claims completion without full stage coverage")
    checkpoint = read_json(checkpoint_path) if checkpoint_path.is_file() else None
    checked_hashes = 0
    if checkpoint is not None:
        if checkpoint.get("frozen") is not True:
            errors.append("checkpoint is not marked frozen")
        if not complete_coverage or any(row.get("status") not in TERMINAL for row in rows):
            errors.append("checkpoint exists without all samples terminal")
        if checkpoint.get("stage") != spec or checkpoint.get("sample_count") != len(indices):
            errors.append("checkpoint stage/count mismatch")
        all_terminal_success = all(row.get("status") in PRODUCTION_SUCCESS for row in rows)
        if checkpoint.get("all_terminal_success") != all_terminal_success:
            errors.append("checkpoint all_terminal_success differs from progress")
        if checkpoint.get("checkpoint_blockers") != progress.get("checkpoint_blockers", []):
            errors.append("checkpoint blockers differ from progress")
        for file_name, key in (("training-identity.json", "training_identity_sha256"),
                               ("frozen-config.json", "frozen_config_sha256"),
                               ("progress.json", "progress_sha256")):
            expected = checkpoint.get(key)
            path = batch / file_name
            if expected:
                checked_hashes += 1
                if not path.is_file() or digest(path) != expected:
                    errors.append(f"checkpoint hash mismatch: {file_name}")
        references = (checkpoint.get("artifacts", []) + checkpoint.get("observation_reports", [])
                      + checkpoint.get("knowledge_snapshot", {}).get("files", []))
        recorded_artifact_paths = {item["path"] for item in checkpoint.get("artifacts", [])}
        for row in rows:
            if row.get("status") in PRODUCTION_SUCCESS:
                run_prefix = f"runs/{row['sample_id']}/"
                for relative in ("status.json", "output/editable.pptx",
                                 "output/page-manifest.json", "output/validation.json"):
                    if run_prefix + relative not in recorded_artifact_paths:
                        errors.append(f"checkpoint omits successful artifact: {run_prefix + relative}")
        for item in references:
            path = batch / item["path"]
            checked_hashes += 1
            if not path.is_file() or digest(path) != item["sha256"]:
                errors.append(f"checkpoint hash mismatch: {item['path']}")
        knowledge_files = checkpoint.get("knowledge_snapshot", {}).get("files", [])
        if checkpoint.get("knowledge_snapshot", {}).get("file_count") != len(knowledge_files):
            errors.append("checkpoint knowledge file count mismatch")
        tree_hash = hashlib.sha256(json.dumps(knowledge_files, sort_keys=True, ensure_ascii=False,
                                            separators=(",", ":")).encode()).hexdigest()
        if checkpoint.get("knowledge_snapshot", {}).get("tree_sha256") != tree_hash:
            errors.append("checkpoint knowledge tree hash mismatch")
        claimed_eligible = bool(checkpoint.get("checkpoint_eligible"))
        expected_eligible = all_terminal_success and not progress.get("checkpoint_blockers")
        if claimed_eligible != expected_eligible or checkpoint.get("checkpoint_status") != ("eligible" if expected_eligible else "blocked"):
            errors.append("checkpoint eligibility metadata inconsistent with progress")
    return {
        "batch": str(batch), "stage": spec, "expected": len(indices), "recorded": len(rows),
        "complete_coverage": complete_coverage, "status_counts": status_counts,
        "frozen_audits_present": audit_count, "frozen_audits_passed": audit_passed,
        "checkpoint_present": checkpoint is not None,
        "checkpoint_eligible_claim": checkpoint.get("checkpoint_eligible") if checkpoint else None,
        "checkpoint_hashes_checked": checked_hashes,
        "identity_and_snapshot_consistent": not errors, "errors": errors,
        "independent_candidate_quality_eligible": None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.batch), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
