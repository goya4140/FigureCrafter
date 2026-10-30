#!/usr/bin/env python3
"""Deterministically replay one frozen sample from Representation onward.

This is a mechanism probe, not a substitute for a formal cumulative regression.
It reuses only copied, validated upstream artifacts and saved model proposals;
the source run remains untouched and no model call is issued.
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
from decomposer.pipeline import execute


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()

    source = args.source_run.resolve()
    out = args.out.resolve()
    if out.exists():
        raise FileExistsError(f"replay target already exists: {out}")
    source_input = read_json(source / "input" / "input.json")
    image = Path(source_input["source"])
    if not image.is_file() or sha256(image) != source_input["source_sha256"]:
        raise ValueError("source image is missing or hash-mismatched")
    if read_json(source / "status.json").get("status") not in {
        "render_success", "structural_success", "validation_failed",
    }:
        raise ValueError("source run is not a terminal processed sample")
    for relative in (
        "input/normalized.png", "observations/candidates.json",
        "observations/evidence-ir.json", "recognition/recognition.json",
        "representation/model-output.json", "layout/model-output.json",
    ):
        if not (source / relative).is_file():
            raise FileNotFoundError(source / relative)

    out.mkdir(parents=True)
    for directory in ("input", "observations", "recognition"):
        shutil.copytree(source / directory, out / directory)
    fixtures = out / "saved-proposals"
    fixtures.mkdir()
    for stage in ("representation", "layout"):
        shutil.copy2(source / stage / "model-output.json", fixtures / f"{stage}.json")

    config = read_json(args.config)
    config.setdefault("refinement", {})["max_rounds"] = 0
    config.setdefault("knowledge", {})["path"] = str(out / "knowledge")
    config["refinement"]["knowledge_path"] = str(out / "knowledge")
    write_json(out / "replay-identity.json", {
        "version": VERSION,
        "source_run": str(source),
        "source_status_sha256": sha256(source / "status.json"),
        "source_recognition_sha256": sha256(source / "recognition" / "recognition.json"),
        "source_representation_proposal_sha256": sha256(source / "representation" / "model-output.json"),
        "source_layout_proposal_sha256": sha256(source / "layout" / "model-output.json"),
        "config_sha256": sha256(args.config),
        "scope": "deterministic-mechanism-probe-not-cumulative-regression",
    })
    result = execute(image, out, config, fixture_dir=fixtures, render_output=True, resume=True)
    audit = audit_run(out)
    write_json(out / "independent-audit.json", audit)
    print({"status": result["status"], "audit_passed": audit["passed"], "out": str(out)})


if __name__ == "__main__":
    main()
