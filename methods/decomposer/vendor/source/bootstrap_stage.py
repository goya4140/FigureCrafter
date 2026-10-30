#!/usr/bin/env python3
"""Bootstrap a formal evolution stage from a frozen cumulative regression.

This is an append-only state transition.  It never edits the source regression
or an existing destination.  Imported sample runs are copied byte-for-byte,
independently audited, and described by a full file manifest before the formal
stage runner is allowed to resume at the next sample.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any

from audit_first100 import audit_run
from decomposer.common import sha256, write_json
from decomposer.evolution import PROTOCOL_VERSION, TERMINAL_PROCESSED, TERMINAL_SUCCESS, stage_spec, write_observation


ROOT = Path(__file__).resolve().parent


def _json_identity(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def _tree_manifest(root: Path, paths: list[Path]) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for item in paths:
        if item.is_file():
            files.append({"path": str(item.relative_to(root)), "sha256": sha256(item)})
        elif item.is_dir():
            for path in sorted(candidate for candidate in item.rglob("*") if candidate.is_file()):
                files.append({"path": str(path.relative_to(root)), "sha256": sha256(path)})
        else:
            raise ValueError(f"missing import path: {item}")
    files.sort(key=lambda row: row["path"])
    tree_sha256 = hashlib.sha256(
        json.dumps(files, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    return {"file_count": len(files), "tree_sha256": tree_sha256, "files": files}


def _source_files() -> list[Path]:
    return (
        [ROOT / "train_first100.py"]
        + sorted((ROOT / "decomposer").glob("*.py"))
        + sorted((ROOT / "prompts").glob("*.md"))
        + sorted((ROOT / "tools").glob("*.py"))
        + [ROOT / "backend" / "compile.mjs", ROOT / "audit_first100.py"]
    )


def _normalized_config(config: dict[str, Any]) -> dict[str, Any]:
    value = json.loads(json.dumps(config))
    value.setdefault("knowledge", {}).pop("path", None)
    value.setdefault("refinement", {}).pop("knowledge_path", None)
    return value


def _append_lineage(
    lineage_root: Path,
    destination: Path,
    imported_end: int,
) -> dict[str, Any]:
    """Append a successor without mutating any previously recorded entry."""
    lineage_path = lineage_root / "lineage.json"
    original = {
        "schema_version": "decomposer.evolution-lineage.v1",
        "protocol_version": PROTOCOL_VERSION,
        "preserved_root": str(lineage_root),
        "original_training_identity_sha256": sha256(lineage_root / "training-identity.json"),
        "original_progress_sha256": sha256(lineage_root / "progress.json"),
    }
    if lineage_path.exists():
        lineage = json.loads(lineage_path.read_text())
        for key, expected in original.items():
            if lineage.get(key) != expected:
                raise ValueError(f"lineage immutable root mismatch: {key}")
    else:
        lineage = {**original, "successors": [], "events": []}
    successors = lineage.setdefault("successors", [])
    if any(Path(row.get("path", "")).resolve() == destination.resolve() for row in successors):
        raise ValueError("lineage already contains destination successor")
    events = lineage.setdefault("events", [])
    if successors:
        predecessor = Path(successors[-1]["path"])
        predecessor_progress = predecessor / "progress.json"
        if predecessor_progress.is_file():
            state = json.loads(predecessor_progress.read_text())
            events.append({
                "event": "predecessor-state-observed",
                "path": str(predecessor),
                "status": state.get("status"),
                "stop_event": state.get("stop_event"),
                "progress_sha256": sha256(predecessor_progress),
            })
    successors.append({
        "path": str(destination),
        "bootstrap_import_sha256": sha256(destination / "bootstrap-import.json"),
        "status": "formal-stage-resume-ready",
        "next_sample_index": imported_end + 1,
    })
    return lineage


def _validate_source(
    source: Path,
    imported_end: int,
    *,
    allow_quality_blockers: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    for name in ("training-identity.json", "frozen-config.json", "progress.json", "checkpoint.json"):
        if not (source / name).is_file():
            raise ValueError(f"source is not frozen: missing {name}")
    identity = json.loads((source / "training-identity.json").read_text())
    progress = json.loads((source / "progress.json").read_text())
    checkpoint = json.loads((source / "checkpoint.json").read_text())
    rows = progress.get("samples", [])
    allowed_progress_statuses = {"complete"}
    if allow_quality_blockers:
        allowed_progress_statuses.add("complete_checkpoint_blocked")
    if progress.get("status") not in allowed_progress_statuses or len(rows) != imported_end:
        raise ValueError("source regression must be complete through imported_end")
    if [row.get("sample_index") for row in rows] != list(range(1, imported_end + 1)):
        raise ValueError("source regression must be a contiguous cumulative prefix starting at sample 1")
    allowed_row_statuses = TERMINAL_PROCESSED if allow_quality_blockers else TERMINAL_SUCCESS
    if any(row.get("status") not in allowed_row_statuses for row in rows):
        raise ValueError("every imported sample must have a terminal processed status")
    if checkpoint.get("schema_version") != "decomposer.stage-checkpoint.v2":
        raise ValueError("source requires a v2 frozen checkpoint")
    if not checkpoint.get("frozen"):
        raise ValueError("source checkpoint is not frozen")
    checkpoint_blocked = checkpoint.get("checkpoint_status") == "blocked"
    if checkpoint_blocked and not allow_quality_blockers:
        raise ValueError("source checkpoint is blocked by quality failures")
    if not checkpoint_blocked and not checkpoint.get("all_terminal_success"):
        raise ValueError("source checkpoint is not a successful freeze")
    if checkpoint_blocked and not progress.get("checkpoint_blockers"):
        raise ValueError("blocked source checkpoint has no recorded blockers")
    expected = {
        "training_identity_sha256": sha256(source / "training-identity.json"),
        "frozen_config_sha256": sha256(source / "frozen-config.json"),
        "progress_sha256": sha256(source / "progress.json"),
    }
    for key, value in expected.items():
        if checkpoint.get(key) != value:
            raise ValueError(f"source checkpoint hash mismatch: {key}")
    artifact_hashes = {row["path"]: row["sha256"] for row in checkpoint.get("artifacts", [])}
    for relative, expected_hash in artifact_hashes.items():
        path = source / relative
        if not path.is_file() or sha256(path) != expected_hash:
            raise ValueError(f"source checkpoint artifact mismatch: {relative}")
    for row in rows:
        index = int(row["sample_index"])
        run = source / "runs" / row["sample_id"]
        audit_path = source / f"sample-{index:03d}-audit.json"
        if not run.is_dir() or not audit_path.is_file():
            raise ValueError(f"missing run or independent audit for sample {index}")
        audit = json.loads(audit_path.read_text())
        recomputed = audit_run(run)
        if audit != {**recomputed, "sample_index": index} and audit != recomputed:
            raise ValueError(f"stored independent audit drift for sample {index}")
        if row.get("status") in TERMINAL_SUCCESS and not recomputed.get("passed"):
            raise ValueError(f"independent audit failed for successful imported sample {index}")
        if row.get("status") not in TERMINAL_SUCCESS and not allow_quality_blockers:
            raise ValueError(f"quality-blocked sample {index} requires explicit bootstrap opt-in")
    return identity, progress


def bootstrap(
    *,
    source: Path,
    destination: Path,
    config_path: Path,
    stage: int,
    imported_end: int,
    lineage_root: Path | None = None,
    allow_quality_blockers: bool = False,
) -> Path:
    source = source.resolve()
    destination = destination.resolve()
    config_path = config_path.resolve()
    lineage_root = lineage_root.resolve() if lineage_root else None
    if destination.exists():
        raise ValueError("destination already exists; bootstrap is append-only")
    spec = stage_spec(stage)
    if imported_end < spec["start"] or imported_end >= spec["end"]:
        raise ValueError("imported_end must be a non-empty proper prefix of the formal stage")
    source_identity, source_progress = _validate_source(
        source, imported_end, allow_quality_blockers=allow_quality_blockers
    )
    manifest_path = Path(source_identity["manifest"])
    manifest = json.loads(manifest_path.read_text())
    if source_identity.get("manifest_sha256") != sha256(manifest_path):
        raise ValueError("source manifest hash no longer matches")
    base_config = json.loads(config_path.read_text())
    source_config = json.loads((source / "frozen-config.json").read_text())
    if _normalized_config(base_config) != _normalized_config(source_config):
        raise ValueError("bootstrap config differs from the cumulative regression config")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.bootstrap-", dir=destination.parent))
    try:
        shutil.copytree(source / "runs", temporary / "runs")
        shutil.copytree(source / "knowledge", temporary / "knowledge")
        for index in range(1, imported_end + 1):
            shutil.copy2(source / f"sample-{index:03d}-audit.json", temporary / f"sample-{index:03d}-audit.json")

        config = json.loads(json.dumps(base_config))
        final_knowledge = destination / "knowledge"
        config.setdefault("knowledge", {})["path"] = str(final_knowledge)
        config.setdefault("refinement", {})["knowledge_path"] = str(final_knowledge)
        write_json(temporary / "frozen-config.json", config)

        samples = manifest["samples"][spec["start"] - 1:spec["end"]]
        source_files = _source_files()
        run_identity = {
            "manifest": str(manifest_path.resolve()),
            "manifest_sha256": sha256(manifest_path),
            "config_sha256": _json_identity(config),
            "source_hashes": {str(path.relative_to(ROOT)): sha256(path) for path in source_files},
            "requested_samples": len(samples),
            "global_requested_samples": min(100, len(manifest["samples"])),
            "sample_start_index": spec["start"],
            "sample_end_index": spec["end"],
            "sample_indices": list(range(spec["start"], spec["end"] + 1)),
            "stage": spec,
            "protocol_version": PROTOCOL_VERSION,
        }
        write_json(temporary / "training-identity.json", run_identity)

        imported_rows = json.loads(json.dumps(source_progress["samples"]))
        progress = {
            "status": "running",
            "protocol_version": PROTOCOL_VERSION,
            "stage": spec,
            "samples": imported_rows,
            "completed": len(imported_rows),
        }
        if source_progress.get("checkpoint_blockers"):
            progress["checkpoint_blockers"] = json.loads(json.dumps(source_progress["checkpoint_blockers"]))
        write_json(temporary / "progress.json", progress)
        first_observation = min(
            number for number in range(5, imported_end + 1, 5)
        ) if imported_end >= 5 else None
        if first_observation is not None:
            for number in range(first_observation, imported_end + 1, 5):
                partial = {**progress, "samples": imported_rows[:number], "completed": number}
                write_observation(temporary, partial, spec, number)

        imported_paths = [temporary / "runs", temporary / "knowledge"] + [
            temporary / f"sample-{index:03d}-audit.json" for index in range(1, imported_end + 1)
        ]
        destination_snapshot = _tree_manifest(temporary, imported_paths)
        source_paths = [source / "runs", source / "knowledge"] + [
            source / f"sample-{index:03d}-audit.json" for index in range(1, imported_end + 1)
        ]
        source_snapshot = _tree_manifest(source, source_paths)
        if destination_snapshot["tree_sha256"] != source_snapshot["tree_sha256"]:
            raise ValueError("copied regression snapshot is not byte-identical")
        import_record = {
            "schema_version": "decomposer.stage-bootstrap.v1",
            "append_only": True,
            "protocol_version": PROTOCOL_VERSION,
            "formal_stage": spec,
            "imported_sample_indices": list(range(1, imported_end + 1)),
            "next_sample_index": imported_end + 1,
            "source": {
                "path": str(source),
                "training_identity_sha256": sha256(source / "training-identity.json"),
                "frozen_config_sha256": sha256(source / "frozen-config.json"),
                "progress_sha256": sha256(source / "progress.json"),
                "checkpoint_sha256": sha256(source / "checkpoint.json"),
                "snapshot": source_snapshot,
                "checkpoint_status": json.loads((source / "checkpoint.json").read_text()).get("checkpoint_status", "eligible"),
            },
            "destination": {
                "path": str(destination),
                "training_identity_sha256": sha256(temporary / "training-identity.json"),
                "frozen_config_sha256": sha256(temporary / "frozen-config.json"),
                "progress_sha256": sha256(temporary / "progress.json"),
                "snapshot": destination_snapshot,
            },
            "bootstrap_script_sha256": sha256(Path(__file__)),
            "boundary_repeat_guard": {
                "required": True,
                "previous_sample_index": imported_end,
                "next_sample_index": imported_end + 1,
                "note": "Compare the exact issue key across the imported/live boundary after the next sample; stop early on an identical complete mechanism.",
            },
            "quality_blockers_imported": bool(source_progress.get("checkpoint_blockers")),
        }
        write_json(temporary / "bootstrap-import.json", import_record)
        temporary.rename(destination)

        if lineage_root:
            lineage_path = lineage_root / "lineage.json"
            lineage = _append_lineage(lineage_root, destination, imported_end)
            write_json(lineage_path, lineage)
        return destination
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--stage", type=int, required=True)
    parser.add_argument("--imported-end", type=int, required=True)
    parser.add_argument("--lineage-root")
    parser.add_argument("--allow-quality-blockers", action="store_true")
    args = parser.parse_args()
    path = bootstrap(
        source=Path(args.source),
        destination=Path(args.destination),
        config_path=Path(args.config),
        stage=args.stage,
        imported_end=args.imported_end,
        lineage_root=Path(args.lineage_root) if args.lineage_root else None,
        allow_quality_blockers=args.allow_quality_blockers,
    )
    print(json.dumps({"status": "ready", "destination": str(path)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
