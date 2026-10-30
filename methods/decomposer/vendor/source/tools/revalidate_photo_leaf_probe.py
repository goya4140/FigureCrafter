#!/usr/bin/env python3
"""Replay an approved image-leaf area gate into an isolated, consistent run."""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from decomposer.asset_policy import decide
from decomposer.assemble import validate as validate_scene
from decomposer.common import read_json, sha256, write_json
from decomposer.pptx import compile_scene
from decomposer.provenance import build as build_provenance
from decomposer.representation import canonicalize as canonicalize_representation
from decomposer.validation import validate_run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--label", required=True)
    parser.add_argument("--max-recognition-leaf-area-ratio", required=True, type=float)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    source, out = args.source_run.resolve(), args.out.resolve()
    if out.exists():
        raise FileExistsError(out)
    config = read_json(args.config)
    config["asset_policy"] = {
        **config.get("asset_policy", {}),
        "max_recognition_leaf_area_ratio": args.max_recognition_leaf_area_ratio,
    }
    recognition = read_json(source / "recognition" / "recognition.json")
    evidence = read_json(source / "observations" / "evidence-ir.json")
    decisions = decide(recognition, evidence, config)
    decision = next(item for item in decisions["decisions"] if item["label_id"] == args.label)
    if decision["decision"] != "localized_raster" or decision["source_basis"] != "recognition_bbox":
        raise ValueError(f"policy did not approve a Recognition bbox leaf crop: {decision}")
    for name in ("input", "observations", "recognition", "representation", "layout"):
        shutil.copytree(source / name, out / name)
    write_json(out / "representation" / "asset-decisions.json", decisions)
    representation, representation_report = canonicalize_representation(
        read_json(source / "representation" / "model-output.json"), recognition,
        out / "input" / "normalized.png", out / "representation", decisions,
    )
    write_json(out / "representation" / "representation.json", representation)
    write_json(out / "representation" / "assets.json", representation["assets"])
    write_json(out / "representation" / "canonicalization-report.json", representation_report)
    component = next(item for item in representation["components"] if item["label_id"] == args.label)
    image_primitives = [item for item in component["primitives"] if item["kind"] == "image"]
    if len(image_primitives) != 1:
        raise ValueError("approved leaf did not produce exactly one source-crop image")
    primitive = image_primitives[0]
    original = read_json(source / "output" / "scene.json")
    owned = [item for item in original["elements"] if item["source_label_id"] == args.label]
    if not owned:
        raise ValueError("target has no final scene owner")
    object_bbox = next(item["bbox"] for item in recognition["objects"] if item["id"] == args.label)
    replacement = {
        "id": f"{args.label}/{primitive['id']}", "source_label_id": args.label,
        "category": "image", "kind": "image", "style": primitive["style"],
        "rotation_deg": 0.0, "editable": False, "bbox": object_bbox,
        "asset_id": primitive["asset_id"],
    }
    elements = [item for item in original["elements"] if item["source_label_id"] != args.label]
    elements.insert(original["elements"].index(owned[0]), replacement)
    scene = {
        **original, "elements": elements, "z_order": [item["id"] for item in elements],
        "assets": {**original["assets"], **representation["assets"]},
    }
    validate_scene(scene)
    output = out / "output"
    output.mkdir()
    write_json(output / "scene.json", scene)
    compile_scene(output / "scene.json", output, config, True)
    build_provenance(scene, output / "editable.pptx", output)
    validation = validate_run(out, out / "input" / "normalized.png", config)
    write_json(out / "probe-identity.json", {
        "scope": "isolated-deterministic-photo-leaf-recovery-not-stage-checkpoint",
        "source_run": str(source), "target_label": args.label,
        "source_scene_sha256": sha256(source / "output" / "scene.json"),
        "recovery_scene_sha256": sha256(output / "scene.json"),
        "recovery_pptx_sha256": sha256(output / "editable.pptx"),
        "max_recognition_leaf_area_ratio": args.max_recognition_leaf_area_ratio,
    })
    write_json(out / "status.json", {
        "status": "render_success" if validation["passed"] else "validation_failed",
        "validation": {"passed": validation["passed"], "visual": validation["visual"]},
    })
    print({"validation_passed": validation["passed"],
           "failed_checks": [name for name, result in validation["checks"].items()
                             if not result["passed"]],
           "visual": validation["visual"].get("metrics"), "out": str(out)})


if __name__ == "__main__":
    main()
