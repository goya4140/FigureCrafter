#!/usr/bin/env python3
"""Probe bottom-edge native text-box underflow on a terminal scene."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path

from PIL import Image

from decomposer.common import sha256, write_json
from decomposer.pptx import compile_scene
from decomposer.quality import metrics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--height-factor", type=float, default=1.5)
    parser.add_argument("--label", help="Probe only this source label ID")
    parser.add_argument("--scene", type=Path, help="Optional previously probed scene for combined controls")
    parser.add_argument("--before-preview", type=Path)
    parser.add_argument("--before-pptx", type=Path)
    args = parser.parse_args()
    run, out = args.run.resolve(), args.out.resolve()
    if out.exists():
        raise FileExistsError(out)
    source_scene = args.scene.resolve() if args.scene else run / "output/scene.json"
    source_image = run / "input/normalized.png"
    before_preview = args.before_preview.resolve() if args.before_preview else run / "output/preview.png"
    before_pptx = args.before_pptx.resolve() if args.before_pptx else run / "output/editable.pptx"
    scene = deepcopy(json.loads(source_scene.read_text()))
    height = float(scene["canvas"]["height"])
    affected = []
    for element in scene["elements"]:
        if (element.get("kind") != "text" or "\n" in str(element.get("text") or "")
                or (args.label and element.get("source_label_id") != args.label)):
            continue
        x, y, width, old_height = element["bbox"]
        font = float(element.get("style", {}).get("font_size") or 0)
        new_height = round(args.height_factor * font, 4)
        if not (font >= 10 and old_height / font < .95 and y + new_height > height
                and new_height <= height and str(element.get("text") or "").strip()):
            continue
        new_y = max(0.0, height - new_height)
        element["bbox"] = [x, new_y, width, new_height]
        affected.append({"id": element["id"], "source_label_id": element["source_label_id"],
                         "old_bbox": [x, y, width, old_height], "new_bbox": element["bbox"][:]})
    if not affected:
        raise ValueError("no eligible bottom-edge native text")
    out.mkdir(parents=True)
    scene_path = out / "scene.json"
    write_json(scene_path, scene)
    compiled = compile_scene(scene_path, out / "output", json.loads(args.config.read_text()), True)
    x0 = min(row["old_bbox"][0] for row in affected)
    y0 = min(min(row["old_bbox"][1], row["new_bbox"][1]) for row in affected)
    x1 = max(row["old_bbox"][0] + row["old_bbox"][2] for row in affected)
    y1 = max(row["old_bbox"][1] + row["old_bbox"][3] for row in affected)
    region = [x0, y0, x1 - x0, y1 - y0]
    with Image.open(source_image) as reference, Image.open(before_preview) as before, Image.open(out / "output/preview.png") as after:
        global_before, global_after = metrics(reference, before), metrics(reference, after)
        local_before, local_after = metrics(reference, before, region), metrics(reference, after, region)
    report = {
        "schema_version": "decomposer.bottom-text-height-probe.v1",
        "sample_id": run.name, "height_factor": args.height_factor, "affected": affected,
        "evaluation_region": region, "evaluation_region_source": "affected_scene_owners",
        "source_scene_sha256": sha256(source_scene),
        "source_pptx_sha256": sha256(before_pptx),
        "source_image_sha256": sha256(source_image),
        "candidate_scene_sha256": sha256(scene_path),
        "candidate_pptx_sha256": sha256(out / "output/editable.pptx"),
        "compiler_fallbacks": compiled["compiler"].get("fallbacks", []),
        "structure": compiled["structure"],
        "global_before": global_before, "global_after": global_after,
        "local_before": local_before, "local_after": local_after,
        "global_pixel_delta": round(global_after["pixel_similarity"] - global_before["pixel_similarity"], 6),
        "global_edge_delta": round((global_after["edge_iou"] or 0) - (global_before["edge_iou"] or 0), 6),
        "local_pixel_delta": round(local_after["pixel_similarity"] - local_before["pixel_similarity"], 6),
        "local_edge_delta": round((local_after["edge_iou"] or 0) - (local_before["edge_iou"] or 0), 6),
    }
    write_json(out / "probe-report.json", report)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
