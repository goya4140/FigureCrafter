#!/usr/bin/env python3
"""Isolated policy-gated photo-leaf crop probe against a completed sample."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from decomposer.asset_policy import decide
from decomposer.assemble import validate as validate_scene
from decomposer.common import read_json, write_json
from decomposer.pptx import compile_scene
from decomposer.quality import compare
from decomposer.representation import _crop_asset


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--label", required=True)
    parser.add_argument("--max-area-ratio", required=True, type=float)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    run, out = args.run.resolve(), args.out.resolve()
    if out.exists():
        raise FileExistsError(out)
    config = read_json(args.config)
    config["asset_policy"] = {
        **config.get("asset_policy", {}),
        "max_area_ratio": args.max_area_ratio,
    }
    recognition = read_json(run / "recognition" / "recognition.json")
    evidence = read_json(run / "observations" / "evidence-ir.json")
    original_scene = read_json(run / "output" / "scene.json")
    obj = next(item for item in recognition["objects"] if item["id"] == args.label)
    if (obj.get("category") != "image" or obj.get("semantic_level") != "leaf"
            or str(obj.get("text") or "").strip()):
        raise ValueError("target is not a text-free Recognition image leaf")
    policy = decide(recognition, evidence, config)
    decision = next(item for item in policy["decisions"] if item["label_id"] == args.label)
    if decision["decision"] != "localized_raster" or decision["source_basis"] != "recognition_bbox":
        raise ValueError(f"policy did not approve a Recognition bbox crop: {decision}")
    old_owned = [element for element in original_scene["elements"]
                 if element["source_label_id"] == args.label]
    if not old_owned:
        raise ValueError("target label has no scene owner")
    out.mkdir(parents=True)
    asset_id, asset = _crop_asset(
        run / "input" / "normalized.png", decision["approved_bbox"],
        args.label, out / "assets", decision["source_candidate_id"], decision["source_basis"],
    )
    replacement = {
        "id": f"{args.label}/content", "source_label_id": args.label,
        "category": "image", "kind": "image", "style": old_owned[0]["style"],
        "rotation_deg": 0.0, "editable": False,
        "bbox": obj["bbox"], "asset_id": asset_id,
    }
    first_index = original_scene["elements"].index(old_owned[0])
    elements = [element for element in original_scene["elements"]
                if element["source_label_id"] != args.label]
    elements.insert(first_index, replacement)
    candidate_scene = {
        **original_scene, "elements": elements,
        "z_order": [element["id"] for element in elements],
        "assets": {**original_scene["assets"], asset_id: asset},
    }
    validate_scene(candidate_scene)
    write_json(out / "scene.json", candidate_scene)
    write_json(out / "asset-decisions.json", policy)
    report = compile_scene(out / "scene.json", out, config, True)
    changed_boxes = [element["bbox"] for element in old_owned] + [replacement["bbox"]]
    x0 = min(box[0] for box in changed_boxes)
    y0 = min(box[1] for box in changed_boxes)
    x1 = max(box[0] + box[2] for box in changed_boxes)
    y1 = max(box[1] + box[3] for box in changed_boxes)
    region = [x0, y0, x1 - x0, y1 - y0]
    assessment = compare(
        run / "input" / "normalized.png", run / "output" / "preview.png",
        out / "preview.png", region,
    )
    result = {
        "label_id": args.label, "hypothesis": "high-confidence photo leaf blocked by area cap",
        "max_area_ratio": args.max_area_ratio, "decision": decision,
        "removed_primitive_ids": [element["id"] for element in old_owned],
        "replacement_primitive_id": replacement["id"],
        "evaluation_region": region, "evaluation_region_source": "affected_scene_owners",
        "compiler_status": report["status"],
        "compiler_fallbacks": report["compiler"].get("fallbacks", []),
        "assessment": assessment,
    }
    write_json(out / "probe.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
