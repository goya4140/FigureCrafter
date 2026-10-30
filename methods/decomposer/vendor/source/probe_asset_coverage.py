#!/usr/bin/env python3
"""Recompile one terminal scene with its candidate safe crop, without model calls."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path

from PIL import Image

from decomposer.asset_policy import decide
from decomposer.common import sha256, write_json
from decomposer.pptx import compile_scene
from decomposer.quality import metrics
from decomposer.representation import _crop_asset


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--label", required=True)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    run, out = args.run.resolve(), args.out.resolve()
    if out.exists():
        raise FileExistsError(out)
    recognition = json.loads((run / "recognition/recognition.json").read_text())
    evidence = json.loads((run / "observations/evidence-ir.json").read_text())
    old_decisions = json.loads((run / "representation/asset-decisions.json").read_text())
    config = json.loads(args.config.read_text())
    new_decisions = decide(recognition, evidence, config)
    before = next(item for item in old_decisions["decisions"] if item["label_id"] == args.label)
    after = next(item for item in new_decisions["decisions"] if item["label_id"] == args.label)
    if before["source_basis"] != "sam_candidate" or after["source_basis"] != "recognition_bbox":
        raise ValueError("probe requires a SAM-to-safe-Recognition decision change")
    source_scene = run / "output/scene.json"
    scene = deepcopy(json.loads(source_scene.read_text()))
    image_elements = [
        item for item in scene["elements"]
        if item["source_label_id"] == args.label and item["kind"] == "image"
    ]
    if len(image_elements) != 1:
        raise ValueError(f"expected one image element for {args.label}, found {len(image_elements)}")
    source = run / "input/normalized.png"
    out.mkdir(parents=True)
    asset_id, asset = _crop_asset(
        source, after["approved_bbox"], args.label, out / "assets",
        after["source_candidate_id"], after["source_basis"],
    )
    old_asset_id = image_elements[0]["asset_id"]
    image_elements[0]["asset_id"] = asset_id
    if "sam_insufficient_object_coverage" in after.get("reason_codes", []):
        layout = json.loads((run / "layout/layout.json").read_text())
        placement = next(item for item in layout["placements"] if item["label_id"] == args.label)
        image_elements[0]["bbox"] = list(placement["bbox"])
    if not any(item.get("asset_id") == old_asset_id for item in scene["elements"]):
        scene["assets"].pop(old_asset_id)
    scene["assets"][asset_id] = asset
    scene_path = out / "scene.json"
    write_json(scene_path, scene)
    compiled = compile_scene(scene_path, out / "output", config, render_output=True)
    with Image.open(source) as reference, Image.open(run / "output/preview.png") as old_preview, Image.open(out / "output/preview.png") as new_preview:
        global_before = metrics(reference, old_preview)
        global_after = metrics(reference, new_preview)
        local_before = metrics(reference, old_preview, image_elements[0]["bbox"])
        local_after = metrics(reference, new_preview, image_elements[0]["bbox"])
    report = {
        "schema_version": "decomposer.asset-coverage-probe.v1",
        "sample_id": run.name, "label_id": args.label,
        "source_scene_sha256": sha256(source_scene),
        "source_pptx_sha256": sha256(run / "output/editable.pptx"),
        "source_image_sha256": sha256(source),
        "old_decision": before, "new_decision": after,
        "new_asset": asset, "new_scene_sha256": sha256(scene_path),
        "new_pptx_sha256": sha256(out / "output/editable.pptx"),
        "compiler_fallbacks": compiled["compiler"].get("fallbacks", []),
        "structure": compiled["structure"],
        "global_before": global_before, "global_after": global_after,
        "local_before": local_before, "local_after": local_after,
        "global_pixel_delta": round(global_after["pixel_similarity"] - global_before["pixel_similarity"], 6),
        "local_pixel_delta": round(local_after["pixel_similarity"] - local_before["pixel_similarity"], 6),
        "local_edge_delta": round((local_after["edge_iou"] or 0) - (local_before["edge_iou"] or 0), 6),
    }
    write_json(out / "probe-report.json", report)
    print(json.dumps({
        "sample_id": run.name, "label_id": args.label,
        "global_before": global_before, "global_after": global_after,
        "local_before": local_before, "local_after": local_after,
        "compiler_fallbacks": len(report["compiler_fallbacks"]),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
