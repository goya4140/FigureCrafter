#!/usr/bin/env python3
"""Convert a legacy quality-stop prefix into a frozen blocked checkpoint.

The migration is deliberately narrow: every sample must already have an
auditable terminal artifact, the legacy stop must point at a validation
failure, and the experiment lock must be acquirable.  Runtime failures,
missing inputs, and identity drift are never adopted as quality results.
"""
from __future__ import annotations

import argparse
import fcntl
import json
from pathlib import Path
from typing import Any

from audit_first100 import audit_run
from decomposer.common import sha256, write_json
from decomposer.evolution import TERMINAL_PROCESSED, freeze_checkpoint, write_observation


LEGACY_QUALITY_REASONS = {
    "hard_validation_failure",
    "quality_validation_failure",
    "independent_audit_failed",
}


def adopt(root: Path) -> dict[str, Any]:
    root = root.resolve()
    lock_path = root / ".lock"
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        progress_path = root / "progress.json"
        before_sha256 = sha256(progress_path)
        progress = json.loads(progress_path.read_text())
        stop_event = progress.get("stop_event") or {}
        if progress.get("status") != "stopped_early":
            raise ValueError("only a legacy stopped_early run can be adopted")
        if stop_event.get("reason") not in LEGACY_QUALITY_REASONS:
            raise ValueError("legacy stop is not a quality-only event")
        rows = progress.get("samples", [])
        if not rows or any(row.get("status") not in TERMINAL_PROCESSED for row in rows):
            raise ValueError("all imported rows must be terminal processed artifacts")
        stopped_index = int(stop_event.get("sample_index", 0))
        stopped_row = next((row for row in rows if int(row.get("sample_index", 0)) == stopped_index), None)
        if not stopped_row or stopped_row.get("status") != "validation_failed":
            raise ValueError("legacy stop must point at a validation_failed sample")

        audits = []
        for row in rows:
            index = int(row["sample_index"])
            run = root / "runs" / row["sample_id"]
            audit = audit_run(run)
            write_json(root / f"sample-{index:03d}-audit.json", {**audit, "sample_index": index})
            audits.append({"sample_index": index, "sample_id": row["sample_id"], "passed": audit.get("passed"), "failures": audit.get("failures", [])})
            if row.get("status") != "validation_failed" and not audit.get("passed"):
                raise ValueError(f"successful sample {index} failed independent audit")

        blocker = {
            "reason": "quality_validation_failure",
            "sample_index": stopped_index,
            "sample_id": stop_event.get("sample_id"),
            "legacy_reason": stop_event.get("reason"),
        }
        stopped_row["checkpoint_blocking"] = True
        stopped_row.setdefault("quality_events", []).append(blocker)
        progress["checkpoint_blockers"] = [blocker]
        progress["legacy_stop_event"] = stop_event
        progress.pop("stop_event", None)
        progress["status"] = "complete_checkpoint_blocked"
        progress["requested"] = len(rows)
        counts: dict[str, int] = {}
        for row in rows:
            counts[row["status"]] = counts.get(row["status"], 0) + 1
        progress["counts"] = counts
        write_json(progress_path, progress)
        write_observation(root, progress, progress["stage"], len(rows))
        checkpoint = freeze_checkpoint(root, progress, progress["stage"])
        migration = {
            "schema_version": "decomposer.quality-stop-adoption.v1",
            "append_only_artifacts": True,
            "progress_before_sha256": before_sha256,
            "progress_after_sha256": sha256(progress_path),
            "legacy_stop_event": stop_event,
            "checkpoint_status": checkpoint["checkpoint_status"],
            "checkpoint_sha256": sha256(root / "checkpoint.json"),
            "audits": audits,
        }
        write_json(root / "quality-stop-adoption.json", migration)
        return migration


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root")
    args = parser.parse_args()
    print(json.dumps(adopt(Path(args.root)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
