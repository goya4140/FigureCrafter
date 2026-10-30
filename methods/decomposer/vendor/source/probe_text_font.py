#!/usr/bin/env python3
"""Controlled native-text font substitution on an existing terminal scene."""
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
    parser.add_argument("--from-font", default="Arial")
    parser.add_argument("--to-font", default="Arial Black")
    args = parser.parse_args()
    run, out = args.run.resolve(), args.out.resolve()
    if out.exists():
        raise FileExistsError(out)
    source_scene = run / "output/scene.json"
    source_image = run / "input/normalized.png"
    original = json.loads(source_scene.read_text())
    scene = deepcopy(original)
    affected = []
    for element in scene["elements"]:
        style = element.get("style", {})
        if (element.get("kind") == "text" and style.get("font_family") == args.from_font
                and style.get("bold") is True and str(element.get("text") or "").strip()):
            style["font_family"] = args.to_font
            affected.append({"id": element["id"], "source_label_id": element["source_label_id"], "bbox": element["bbox"]})
    if not affected:
        raise ValueError("no eligible native text elements")
    out.mkdir(parents=True)
    scene_path = out / "scene.json"
    write_json(scene_path, scene)
    config = json.loads(args.config.read_text())
    compiled = compile_scene(scene_path, out / "output", config, True)
    x0 = min(row["bbox"][0] for row in affected)
    y0 = min(row["bbox"][1] for row in affected)
    x1 = max(row["bbox"][0] + row["bbox"][2] for row in affected)
    y1 = max(row["bbox"][1] + row["bbox"][3] for row in affected)
    region = [x0, y0, x1 - x0, y1 - y0]
    with Image.open(source_image) as reference, Image.open(run / "output/preview.png") as before, Image.open(out / "output/preview.png") as after:
        global_before, global_after = metrics(reference, before), metrics(reference, after)
        local_before, local_after = metrics(reference, before, region), metrics(reference, after, region)
    report = {
        "schema_version": "decomposer.text-font-probe.v1",
        "sample_id": run.name,
        "change": {"from_font": args.from_font, "to_font": args.to_font, "bold_only": True},
        "affected": affected,
        "evaluation_region": region,
        "evaluation_region_source": "affected_scene_owners",
        "source_scene_sha256": sha256(source_scene),
        "source_pptx_sha256": sha256(run / "output/editable.pptx"),
        "source_image_sha256": sha256(source_image),
        "candidate_scene_sha256": sha256(scene_path),
        "candidate_pptx_sha256": sha256(out / "output/editable.pptx"),
        "compiler_fallbacks": compiled["compiler"].get("fallbacks", []),
        "structure": compiled["structure"],
        "global_before": global_before,
        "global_after": global_after,
        "local_before": local_before,
        "local_after": local_after,
        "global_pixel_delta": round(global_after["pixel_similarity"] - global_before["pixel_similarity"], 6),
        "global_edge_delta": round((global_after["edge_iou"] or 0) - (global_before["edge_iou"] or 0), 6),
        "local_pixel_delta": round(local_after["pixel_similarity"] - local_before["pixel_similarity"], 6),
        "local_edge_delta": round((local_after["edge_iou"] or 0) - (local_before["edge_iou"] or 0), 6),
    }
    write_json(out / "probe-report.json", report)
    print(json.dumps({key: report[key] for key in ("sample_id", "global_before", "global_after", "global_pixel_delta", "global_edge_delta", "local_pixel_delta", "local_edge_delta")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
