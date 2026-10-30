#!/usr/bin/env python3
"""Run production validation on an isolated copied scene probe, without checkpoint mutation."""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from decomposer import VERSION
from decomposer.common import read_json, sha256, write_json
from decomposer.provenance import build as build_provenance
from decomposer.validation import validate_run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", required=True, type=Path)
    parser.add_argument("--probe", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()
    source, probe, out = args.source_run.resolve(), args.probe.resolve(), args.out.resolve()
    if out.exists():
        raise FileExistsError(out)
    out.mkdir(parents=True)
    for name in ("input", "observations", "recognition", "representation", "layout"):
        shutil.copytree(source / name, out / name)
    output = out / "output"
    output.mkdir()
    for name in ("scene.json", "editable.pptx", "preview.png", "report.json", "compiler-report.json", "compile.log", "render.log"):
        shutil.copy2(probe / name, output / name)
    scene = read_json(output / "scene.json")
    build_provenance(scene, output / "editable.pptx", output)
    report = read_json(output / "report.json")
    report["render"]["path"] = str(output / "preview.png")
    write_json(output / "report.json", report)
    write_json(out / "probe-identity.json", {
        "version": VERSION, "scope": "isolated-scene-validation-not-independent-audit-or-checkpoint",
        "source_run": str(source), "probe": str(probe),
        "source_scene_sha256": sha256(source / "output" / "scene.json"),
        "probe_scene_sha256": sha256(probe / "scene.json"),
        "config_sha256": sha256(args.config),
    })
    result = validate_run(out, out / "input" / "normalized.png", read_json(args.config))
    write_json(out / "status.json", {
        "version": VERSION, "status": "render_success" if result["passed"] else "validation_failed",
        "validation": {"passed": result["passed"], "visual": result["visual"], "failure_count": len(result["failures"]), "warning_count": len(result["warnings"])},
    })
    print({"validation_passed": result["passed"], "failed_checks": [name for name, item in result["checks"].items() if not item["passed"]], "visual": result["visual"].get("metrics"), "out": str(out)})


if __name__ == "__main__":
    main()
