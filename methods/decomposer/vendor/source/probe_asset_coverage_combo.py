#!/usr/bin/env python3
"""Recompile a terminal scene with several safe crop decisions, without model calls."""
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
    parser.add_argument("--labels", required=True, help="comma-separated Recognition label IDs")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    run, out = args.run.resolve(), args.out.resolve()
    if out.exists():
        raise FileExistsError(out)
    labels = [item.strip() for item in args.labels.split(",") if item.strip()]
    if not labels or len(labels) != len(set(labels)):
        raise ValueError("labels must be nonempty and unique")
    recognition = json.loads((run / "recognition/recognition.json").read_text())
    evidence = json.loads((run / "observations/evidence-ir.json").read_text())
    old_decisions = {item["label_id"]: item for item in json.loads((run / "representation/asset-decisions.json").read_text())["decisions"]}
    new_decisions = {item["label_id"]: item for item in decide(recognition, evidence, json.loads(args.config.read_text()))["decisions"]}
    placements = {item["label_id"]: item for item in json.loads((run / "layout/layout.json").read_text())["placements"]}
    source_scene = run / "output/scene.json"
    scene = deepcopy(json.loads(source_scene.read_text()))
    source = run / "input/normalized.png"
    out.mkdir(parents=True)
    changes = []
    for label in labels:
        before, after = old_decisions[label], new_decisions[label]
        if before["source_basis"] != "sam_candidate" or after["source_basis"] != "recognition_bbox":
            raise ValueError(f"{label} is not a SAM-to-safe-Recognition change")
        elements = [item for item in scene["elements"] if item["source_label_id"] == label and item["kind"] == "image"]
        if len(elements) != 1:
            raise ValueError(f"expected one image element for {label}, found {len(elements)}")
        element = elements[0]
        old_asset_id = element["asset_id"]
        asset_id, asset = _crop_asset(source, after["approved_bbox"], label, out / "assets", after["source_candidate_id"], after["source_basis"])
        element["asset_id"] = asset_id
        element["bbox"] = list(placements[label]["bbox"])
        if not any(item.get("asset_id") == old_asset_id for item in scene["elements"]):
            scene["assets"].pop(old_asset_id)
        scene["assets"][asset_id] = asset
        changes.append({"label_id": label, "old_decision": before, "new_decision": after, "new_asset": asset, "evaluation_region": element["bbox"]})
    scene_path = out / "scene.json"
    write_json(scene_path, scene)
    config = json.loads(args.config.read_text())
    compiled = compile_scene(scene_path, out / "output", config, render_output=True)
    with Image.open(source) as reference, Image.open(run / "output/preview.png") as old_preview, Image.open(out / "output/preview.png") as new_preview:
        global_before = metrics(reference, old_preview)
        global_after = metrics(reference, new_preview)
        for item in changes:
            item["local_before"] = metrics(reference, old_preview, item["evaluation_region"])
            item["local_after"] = metrics(reference, new_preview, item["evaluation_region"])
    report = {
        "schema_version": "decomposer.asset-coverage-combo-probe.v1",
        "sample_id": run.name, "labels": labels,
        "source_scene_sha256": sha256(source_scene),
        "source_pptx_sha256": sha256(run / "output/editable.pptx"),
        "new_scene_sha256": sha256(scene_path),
        "new_pptx_sha256": sha256(out / "output/editable.pptx"),
        "compiler_fallbacks": compiled["compiler"].get("fallbacks", []),
        "structure": compiled["structure"],
        "global_before": global_before, "global_after": global_after,
        "changes": changes,
    }
    write_json(out / "probe-report.json", report)
    print(json.dumps({
        "sample_id": run.name, "labels": labels,
        "global_before": global_before, "global_after": global_after,
        "compiler_fallbacks": len(report["compiler_fallbacks"]),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
