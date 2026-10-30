#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import time

from decomposer.common import sha256, write_json
from decomposer import VERSION
from decomposer.evolution import (
    PROTOCOL_VERSION, QUALITY_FAILURE_STATUSES, TERMINAL_PROCESSED, TERMINAL_SUCCESS,
    freeze_checkpoint, issue_key, issue_rows, stage_spec, write_observation,
)
from decomposer.knowledge import merge_into
from decomposer.pipeline import execute
from audit_first100 import audit_run


def _json_identity(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _knowledge_snapshot_identity(path: Path) -> dict:
    files = {
        str(item.relative_to(path)): sha256(item)
        for bucket in ("memory", "skills", "evidence")
        for item in sorted((path / bucket).rglob("*"))
        if item.is_file()
    }
    return {
        "source": str(path.resolve()),
        "memory_count": len(list((path / "memory").glob("*.json"))),
        "skill_count": len(list((path / "skills").glob("*/strategy.json"))),
        "files": files,
        "snapshot_sha256": _json_identity(files),
    }


EXECUTION_BLOCKER_STATUSES = {"failed", "runtime_failed", "missing_input", "hash_mismatch"}
RESUME_MIGRATABLE_SOURCE_FILES = {"train_first100.py"}


def _merge_config(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_config(result[key], value)
        else:
            result[key] = value
    return result


def _manifest_indexed_samples(manifest: dict, limit: int) -> list[tuple[int, dict]]:
    indexed_samples: list[tuple[int, dict]] = []
    for sample in manifest["samples"][:limit]:
        index = sample.get("sample_index")
        if not isinstance(index, int) or isinstance(index, bool) or not 1 <= index <= 100:
            raise ValueError("manifest-indexed samples require integer sample_index in 1..100")
        indexed_samples.append((index, sample))
    indices = [index for index, _ in indexed_samples]
    if indices != sorted(set(indices)):
        raise ValueError("manifest sample_index values must be unique and increasing")
    if not indexed_samples:
        raise ValueError("manifest contains no samples")
    return indexed_samples


def _record_sample(progress: dict, row: dict) -> None:
    """Keep one current row per sample while preserving failed attempts."""
    index = next(
        (offset for offset, existing in enumerate(progress.get("samples", []))
         if existing.get("sample_index") == row.get("sample_index")),
        None,
    )
    if index is None:
        progress.setdefault("samples", []).append(row)
    else:
        prior = progress["samples"][index]
        progress.setdefault("attempt_history", []).append(prior)
        progress["samples"][index] = row
    progress["samples"].sort(key=lambda item: int(item.get("sample_index", 0)))
    progress["completed"] = len(progress["samples"])


def _add_checkpoint_blocker(progress: dict, event: dict, row: dict | None = None) -> None:
    blockers = progress.setdefault("checkpoint_blockers", [])
    identity = (event.get("reason"), event.get("sample_index"), event.get("sample_id"), event.get("issue_key"))
    if not any(
        (item.get("reason"), item.get("sample_index"), item.get("sample_id"), item.get("issue_key")) == identity
        for item in blockers
    ):
        blockers.append(event)
    if row is not None:
        row["checkpoint_blocking"] = True
        row.setdefault("quality_events", []).append(event)


def _resume_identity_migration(prior: dict, current: dict) -> dict | None:
    """Allow only the audited runner-policy migration used by resilient batches."""
    prior_base = {key: value for key, value in prior.items() if key not in {"source_hashes", "execution_failure_policy"}}
    current_base = {key: value for key, value in current.items() if key not in {"source_hashes", "execution_failure_policy"}}
    if prior_base != current_base:
        return None
    if prior.get("execution_failure_policy", "stop") not in {"stop", "continue"}:
        return None
    if current.get("execution_failure_policy") != "continue":
        return None
    prior_sources = prior.get("source_hashes", {})
    current_sources = current.get("source_hashes", {})
    changed = sorted(
        key for key in set(prior_sources) | set(current_sources)
        if prior_sources.get(key) != current_sources.get(key)
    )
    if not changed or not set(changed) <= RESUME_MIGRATABLE_SOURCE_FILES:
        return None
    return {
        "reason": "enable_explicit_continue_on_hard_failure",
        "changed_source_files": changed,
        "prior_execution_failure_policy": prior.get("execution_failure_policy", "stop"),
        "current_execution_failure_policy": "continue",
        "prior_source_hashes": {key: prior_sources.get(key) for key in changed},
        "current_source_hashes": {key: current_sources.get(key) for key in changed},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the frozen first-100 manifest with shared auditable knowledge.")
    parser.add_argument("--manifest", default=str(Path(__file__).resolve().parents[4] / "01_methods/Figure2PPTX/v6/experiments/v6-first100-server-20260917/manifest.json"))
    parser.add_argument("--images", default=str(Path(__file__).resolve().parents[4] / "05_data/datasets/benchmark_annotated/images"))
    parser.add_argument("--config", required=True)
    parser.add_argument("--model-profile", help="Optional model-only JSON profile overlaid on --config")
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--stage", type=int)
    parser.add_argument("--start-index", type=int)
    parser.add_argument("--end-index", type=int)
    parser.add_argument("--observation-interval", type=int, default=5)
    parser.add_argument(
        "--manifest-indices", action="store_true",
        help="Use each manifest row's original sample_index (Stage 5 targeted regeneration).",
    )
    parser.add_argument("--systemic-repeat-threshold", type=int, default=2)
    parser.add_argument(
        "--stop-on-quality-failure", action="store_true",
        help="Legacy diagnostic mode: stop sampling on a quality failure. Formal training continues by default.",
    )
    parser.add_argument(
        "--continue-on-hard-failure", action="store_true",
        help="Record a per-sample execution failure and continue with the next sample.",
    )
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    root = Path(args.out).resolve(); root.mkdir(parents=True, exist_ok=True)
    with (root / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manifest = json.loads(Path(args.manifest).read_text())
        if args.manifest_indices:
            if args.stage is not None or args.start_index is not None or args.end_index is not None:
                raise ValueError("--manifest-indices cannot be combined with stage or range arguments")
            indexed_samples = _manifest_indexed_samples(manifest, args.limit)
            indices = [index for index, _ in indexed_samples]
            start_index, end_index = indices[0], indices[-1]
            spec = {
                "stage": 5,
                "start": start_index,
                "end": end_index,
                "sample_indices": indices,
                "focus": "failed-sample-regeneration-and-mechanism-convergence",
            }
        elif args.stage is not None:
            spec = stage_spec(args.stage)
            start_index = args.start_index or spec["start"]
            end_index = args.end_index or spec["end"]
            if (start_index, end_index) != (spec["start"], spec["end"]):
                raise ValueError("formal stage boundaries are fixed by the evolution protocol")
        else:
            start_index, end_index = args.start_index or 1, args.end_index or args.limit
            spec = {"stage": None, "start": start_index, "end": end_index, "focus": "legacy-or-ad-hoc"}
        if not args.manifest_indices:
            if start_index < 1 or end_index < start_index or end_index > min(100, len(manifest["samples"])):
                raise ValueError("invalid 1-based sample range")
            indexed_samples = list(enumerate(manifest["samples"][start_index - 1:end_index], start_index))
        samples = [sample for _, sample in indexed_samples]
        config = json.loads(Path(args.config).read_text())
        if args.model_profile:
            config = _merge_config(config, json.loads(Path(args.model_profile).read_text()))
        knowledge = root / "knowledge"
        seed_knowledge = config.get("knowledge", {}).get("seed_path") or config.get("refinement", {}).get("knowledge_path")
        seed_identity = _knowledge_snapshot_identity(Path(seed_knowledge)) if seed_knowledge and Path(seed_knowledge).is_dir() else None
        seed_identity_path = root / "initial-knowledge-snapshot.json"
        if seed_identity_path.exists() and json.loads(seed_identity_path.read_text()) != seed_identity:
            raise ValueError("Knowledge seed changed; resume requires the identical frozen snapshot")
        if seed_identity is not None:
            write_json(seed_identity_path, seed_identity)
        if not knowledge.exists():
            if seed_knowledge and Path(seed_knowledge).is_dir():
                merge_into(knowledge, seed_knowledge)
            else:
                knowledge.mkdir(parents=True)
        config.setdefault("knowledge", {})["path"] = str(knowledge)
        config.setdefault("refinement", {})["knowledge_path"] = str(knowledge)
        frozen_config = root / "frozen-config.json"
        if frozen_config.exists() and json.loads(frozen_config.read_text()) != config:
            raise ValueError("Training configuration changed; use a new output directory instead of mixing runs")
        write_json(frozen_config, config)
        source_files = (
            [Path(__file__)]
            + sorted((Path(__file__).parent / "decomposer").glob("*.py"))
            + sorted((Path(__file__).parent / "prompts").glob("*.md"))
            + sorted((Path(__file__).parent / "tools").glob("*.py"))
            + [Path(__file__).parent / "backend" / "compile.mjs", Path(__file__).parent / "audit_first100.py"]
        )
        run_identity = {
            "manifest": str(Path(args.manifest).resolve()), "manifest_sha256": sha256(args.manifest),
            "config_sha256": _json_identity(config),
            "source_hashes": {str(path.relative_to(Path(__file__).parent)): sha256(path) for path in source_files},
            "requested_samples": len(samples),
            "global_requested_samples": min(100, len(manifest["samples"])),
            "sample_start_index": start_index, "sample_end_index": end_index,
            "sample_indices": [index for index, _ in indexed_samples],
            "stage": spec, "protocol_version": PROTOCOL_VERSION,
            "execution_failure_policy": "continue" if args.continue_on_hard_failure else "stop",
        }
        identity_path = root / "training-identity.json"
        if identity_path.exists() and json.loads(identity_path.read_text()) != run_identity:
            prior_identity = json.loads(identity_path.read_text())
            migration = (
                _resume_identity_migration(prior_identity, run_identity)
                if args.resume and args.continue_on_hard_failure else None
            )
            if migration is None:
                raise ValueError("Training code or manifest changed; resume requires an identical frozen identity")
            migration_path = root / "identity-migrations.json"
            migrations = json.loads(migration_path.read_text()) if migration_path.exists() else []
            migrations.append(migration)
            write_json(migration_path, migrations)
        write_json(identity_path, run_identity)
        progress_path = root / "progress.json"
        progress = json.loads(progress_path.read_text()) if args.resume and progress_path.exists() else {
            "status": "running", "protocol_version": PROTOCOL_VERSION, "stage": spec, "samples": []
        }
        if args.resume:
            progress["status"] = "running"
            progress.pop("stop_event", None)
        done = {row["sample_id"] for row in progress["samples"] if row.get("status") in TERMINAL_PROCESSED}
        image_root = Path(args.images)
        last_issue_key = None; repeat_count = 0; stopped_early = None
        for index, sample in indexed_samples:
            sample_id = sample["sample_id"]
            if sample_id in done: continue
            matches = list(image_root.glob(sample_id + ".*"))
            if not matches:
                row = {"sample_index": index, "sample_id": sample_id, "version": VERSION, "status": "missing_input"}
                _record_sample(progress, row); write_json(progress_path, progress)
                stopped_early = {"reason": "missing_input", "sample_index": index, "sample_id": sample_id}
                progress.update(status="interrupted", stop_event=stopped_early); write_json(progress_path, progress); break
            image = matches[0]
            if sample.get("sha256") and sha256(image) != sample["sha256"]:
                row = {"sample_index": index, "sample_id": sample_id, "version": VERSION, "status": "hash_mismatch", "input": str(image)}
                _record_sample(progress, row); write_json(progress_path, progress)
                stopped_early = {"reason": "input_hash_mismatch", "sample_index": index, "sample_id": sample_id}
                progress.update(status="interrupted", stop_event=stopped_early); write_json(progress_path, progress); break
            started = time.monotonic(); run_dir = root / "runs" / sample_id
            existing_status = run_dir / "status.json"
            if args.resume and existing_status.exists():
                prior = json.loads(existing_status.read_text())
                if prior.get("status") in {"render_success", "structural_success"}:
                    row = {"sample_index": index, "sample_id": sample_id, "version": prior.get("version", VERSION), "status": prior["status"], "degraded": prior.get("degraded"), "refinement": prior.get("refinement"), "validation": prior.get("validation"), "seconds": 0, "reused": True}
                    progress["samples"].append(row); progress["completed"] = len(progress["samples"]); write_json(progress_path, progress); done.add(sample_id); continue
            try:
                result = execute(image, run_dir, config, render_output=True, resume=args.resume)
                row = {"sample_index": index, "sample_id": sample_id, "version": result.get("version", VERSION), "status": result["status"], "degraded": result["degraded"], "refinement": result.get("refinement"), "validation": result.get("validation"), "seconds": round(time.monotonic() - started, 3)}
            except Exception as exc:
                row = {"sample_index": index, "sample_id": sample_id, "version": VERSION, "status": "failed", "error": f"{type(exc).__name__}: {exc}", "seconds": round(time.monotonic() - started, 3)}
            _record_sample(progress, row); write_json(progress_path, progress)
            quality_event = None
            execution_event = None
            if row["status"] in TERMINAL_PROCESSED:
                audit = audit_run(run_dir)
                audit_path = root / f"sample-{index:03d}-audit.json"
                write_json(audit_path, {**audit, "sample_index": index})
                if not audit.get("passed"):
                    quality_event = {"reason": "independent_audit_failed", "sample_index": index, "sample_id": sample_id, "failures": audit.get("failures")}
            if row["status"] in QUALITY_FAILURE_STATUSES:
                quality_event = quality_event or {"reason": "quality_validation_failure", "sample_index": index, "sample_id": sample_id}
            elif row["status"] in EXECUTION_BLOCKER_STATUSES:
                execution_event = {"reason": "execution_failure", "sample_index": index, "sample_id": sample_id, "status": row["status"], "error": row.get("error")}
            issues = issue_rows(run_dir)
            keys = [issue_key(issue) for issue in issues]
            current_key = keys[0] if keys else None
            if current_key and current_key == last_issue_key:
                repeat_count += 1
            elif current_key:
                last_issue_key, repeat_count = current_key, 1
            else:
                last_issue_key, repeat_count = None, 0
            if repeat_count >= args.systemic_repeat_threshold:
                quality_event = quality_event or {"reason": "systemic_mechanism_repeated", "sample_index": index, "sample_id": sample_id, "repeat_count": repeat_count, "issue_key": current_key}
            if quality_event:
                _add_checkpoint_blocker(progress, quality_event, row)
            write_json(progress_path, progress)
            if len(progress["samples"]) % args.observation_interval == 0 or execution_event:
                write_observation(root, progress, spec, len(progress["samples"]))
            if execution_event:
                if args.continue_on_hard_failure:
                    _add_checkpoint_blocker(progress, execution_event, row)
                    write_json(progress_path, progress)
                else:
                    stopped_early = execution_event
                    progress.update(status="interrupted", stop_event=execution_event)
                    write_json(progress_path, progress)
                    break
            if quality_event and args.stop_on_quality_failure:
                stopped_early = quality_event
                progress.update(status="stopped_early", stop_event=quality_event)
                write_json(progress_path, progress)
                break
        counts: dict[str, int] = {}
        for row in progress["samples"]: counts[row["status"]] = counts.get(row["status"], 0) + 1
        if not stopped_early:
            final_status = "complete_checkpoint_blocked" if progress.get("checkpoint_blockers") else "complete"
            progress.update(status=final_status, requested=len(samples), counts=counts)
            write_json(progress_path, progress)
            if len(progress["samples"]) == len(samples):
                freeze_checkpoint(root, progress, spec)
        print(json.dumps(progress, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
