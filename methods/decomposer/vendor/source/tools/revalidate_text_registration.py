#!/usr/bin/env python3
"""Recompile a terminal scene with measured text registration and independent audit.

This reuses frozen upstream and accepted refinement artifacts.  It is a targeted
mechanism regression, not a formal cumulative model-chain regression.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from audit_first100 import audit_run
from decomposer import VERSION
from decomposer.common import read_json, sha256, write_json
from decomposer.text_registration import run as run_text_registration
from decomposer.validation import validate_run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()
    source = args.source_run.resolve()
    out = args.out.resolve()
    if out.exists():
        raise FileExistsError(out)
    source_status = read_json(source / "status.json")
    if source_status.get("status") not in {"render_success", "structural_success", "validation_failed"}:
        raise ValueError("source is not a terminal processed sample")
    input_record = read_json(source / "input" / "input.json")
    reference = Path(input_record["source"])
    if not reference.is_file() or sha256(reference) != input_record["source_sha256"]:
        raise ValueError("source image is missing or hash-mismatched")
    out.mkdir(parents=True)
    for directory in ("input", "observations", "recognition", "representation", "layout", "output"):
        shutil.copytree(source / directory, out / directory)
    for directory in ("calls", "refinement", "knowledge"):
        if (source / directory).is_dir():
            shutil.copytree(source / directory, out / directory)
    write_json(out / "replay-identity.json", {
        "version": VERSION, "source_run": str(source),
        "source_status_sha256": sha256(source / "status.json"),
        "source_scene_sha256": sha256(source / "output" / "scene.json"),
        "source_pptx_sha256": sha256(source / "output" / "editable.pptx"),
        "config_sha256": sha256(args.config),
        "scope": "targeted-final-scene-mechanism-regression-not-cumulative-training",
    })
    config = read_json(args.config)
    registration = run_text_registration(out, out / "input" / "normalized.png", config)
    registration_pass_2 = run_text_registration(out, out / "input" / "normalized.png", config, pass_number=2)
    validation = validate_run(out, out / "input" / "normalized.png", config)
    status = {
        **source_status, "version": VERSION,
        "status": "render_success" if validation["passed"] else "validation_failed",
        "text_registration": registration,
        "text_registration_pass_2": registration_pass_2,
        "validation": {
            "passed": validation["passed"], "visual": validation["visual"],
            "failure_count": len(validation["failures"]), "warning_count": len(validation["warnings"]),
        },
    }
    write_json(out / "status.json", status)
    audit = audit_run(out)
    write_json(out / "independent-audit.json", audit)
    print({
        "registration": registration["status"],
        "registration_pass_2": registration_pass_2["status"],
        "visual": validation["visual"].get("metrics"),
        "validation_passed": validation["passed"], "audit_passed": audit["passed"],
        "audit_failures": audit.get("failures"), "out": str(out),
    })


if __name__ == "__main__":
    main()
