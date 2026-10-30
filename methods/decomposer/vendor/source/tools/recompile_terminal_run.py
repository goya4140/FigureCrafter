#!/usr/bin/env python3
"""Isolated current-compiler replay of a terminal run's unchanged final scene."""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from audit_first100 import audit_run
from decomposer import VERSION
from decomposer.common import read_json, sha256, write_json
from decomposer.pptx import compile_scene
from decomposer.provenance import build as build_provenance
from decomposer.validation import validate_run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    source, out = args.source_run.resolve(), args.out.resolve()
    if out.exists():
        raise FileExistsError(out)
    source_status = read_json(source / "status.json")
    if source_status.get("status") not in {"render_success", "validation_failed"}:
        raise ValueError("source is not a terminal rendered run")
    record = read_json(source / "input" / "input.json")
    image = Path(record["source"])
    if not image.is_file() or sha256(image) != record["source_sha256"]:
        raise ValueError("source image hash mismatch")
    shutil.copytree(source, out)
    output = out / "output"
    scene = read_json(output / "scene.json")
    old_scene_hash = sha256(source / "output" / "scene.json")
    old_pptx_hash = sha256(source / "output" / "editable.pptx")
    config = read_json(args.config)
    report = compile_scene(output / "scene.json", output, config, True)
    build_provenance(scene, output / "editable.pptx", output)
    validation = validate_run(out, out / "input" / "normalized.png", config)
    write_json(out / "status.json", {
        **source_status,
        "version": VERSION,
        "status": "render_success" if validation["passed"] else "validation_failed",
        "validation": {
            "passed": validation["passed"], "visual": validation["visual"],
            "failure_count": len(validation["failures"]),
            "warning_count": len(validation["warnings"]),
        },
    })
    audit = audit_run(out)
    write_json(out / "candidate-independent-audit.json", audit)
    write_json(out / "recompile-identity.json", {
        "scope": "isolated-unchanged-scene-compiler-replay-not-formal-training",
        "source_run": str(source), "config_sha256": sha256(args.config),
        "source_scene_sha256": old_scene_hash,
        "replayed_scene_sha256": sha256(output / "scene.json"),
        "source_pptx_sha256": old_pptx_hash,
        "replayed_pptx_sha256": sha256(output / "editable.pptx"),
    })
    print({
        "same_scene": old_scene_hash == sha256(output / "scene.json"),
        "compiler_status": report["status"],
        "validation_passed": validation["passed"],
        "audit_passed": audit["passed"],
        "audit_failures": [item["code"] for item in audit["failures"]],
        "visual": validation["visual"].get("metrics"),
        "out": str(out),
    })


if __name__ == "__main__":
    main()
