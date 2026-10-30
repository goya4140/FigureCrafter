#!/usr/bin/env python3
"""Recompile selected rounded cards as native paths at controlled radii."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path

from PIL import Image

from decomposer.common import sha256, write_json
from decomposer.pptx import compile_scene
from decomposer.quality import metrics


DEFAULT_CARD_LABELS = ("graph_cards", "hypergraph_cards")


def rounded_path(width: float, height: float, radius: float) -> str:
    if not 0 < radius <= min(width, height) / 2:
        raise ValueError("radius must fit the card")
    w, h, r = width, height, radius
    return (f"M {r:g} 0 L {w-r:g} 0 Q {w:g} 0 {w:g} {r:g} "
            f"L {w:g} {h-r:g} Q {w:g} {h:g} {w-r:g} {h:g} "
            f"L {r:g} {h:g} Q 0 {h:g} 0 {h-r:g} "
            f"L 0 {r:g} Q 0 0 {r:g} 0 Z")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--radius", type=float, required=True)
    parser.add_argument("--stroke-width", type=float, help="Optional controlled outline width")
    parser.add_argument("--label", action="append", help="Repeat to select card owner labels")
    parser.add_argument("--expected-count", type=int, help="Guard against incomplete card selection")
    args = parser.parse_args()
    run, out = args.run.resolve(), args.out.resolve()
    if out.exists():
        raise FileExistsError(out)
    source_scene = run / "output/scene.json"
    scene = deepcopy(json.loads(source_scene.read_text()))
    selected_labels = frozenset(args.label or DEFAULT_CARD_LABELS)
    changes = []
    for element in scene["elements"]:
        if element.get("source_label_id") not in selected_labels or element.get("kind") != "round_rect":
            continue
        _, _, width, height = element["bbox"]
        element["kind"] = "path"
        element["path"] = rounded_path(width, height, args.radius)
        element["path_viewbox"] = [0, 0, width, height]
        element["fallback_kind"] = "rect"
        if args.stroke_width is not None:
            if args.stroke_width <= 0:
                raise ValueError("stroke width must be positive")
            element["style"]["stroke_width"] = args.stroke_width
        changes.append({"element_id": element["id"], "bbox": element["bbox"], "radius": args.radius})
    expected = args.expected_count if args.expected_count is not None else 8
    if len(changes) != expected:
        raise ValueError(f"expected {expected} stacked card elements, found {len(changes)}")
    out.mkdir(parents=True)
    scene_path = out / "scene.json"
    write_json(scene_path, scene)
    config = json.loads(args.config.read_text())
    compiled = compile_scene(scene_path, out / "output", config, render_output=True)
    with (Image.open(run / "input/normalized.png") as reference,
          Image.open(run / "output/preview.png") as before,
          Image.open(out / "output/preview.png") as after):
        global_before = metrics(reference, before)
        global_after = metrics(reference, after)
        local = {}
        for label in sorted(selected_labels):
            boxes = [change["bbox"] for change in changes
                     if change["element_id"].startswith(f"{label}/")]
            left = min(box[0] for box in boxes)
            top = min(box[1] for box in boxes)
            right = max(box[0] + box[2] for box in boxes)
            bottom = max(box[1] + box[3] for box in boxes)
            bbox = [left, top, right - left, bottom - top]
            local[label] = {"region": bbox,
                            "before": metrics(reference, before, bbox),
                            "after": metrics(reference, after, bbox)}
    report = {
        "schema_version": "decomposer.card-corner-radius-probe.v1",
        "source_scene_sha256": sha256(source_scene),
        "source_pptx_sha256": sha256(run / "output/editable.pptx"),
        "source_image_sha256": sha256(run / "input/normalized.png"),
        "new_scene_sha256": sha256(scene_path),
        "new_pptx_sha256": sha256(out / "output/editable.pptx"),
        "changes": changes,
        "selected_labels": sorted(selected_labels),
        "stroke_width": args.stroke_width,
        "compiler_fallbacks": compiled["compiler"].get("fallbacks", []),
        "structure": compiled["structure"],
        "global_before": global_before,
        "global_after": global_after,
        "local": local,
    }
    write_json(out / "probe-report.json", report)
    print(json.dumps({"radius": args.radius, "stroke_width": args.stroke_width,
                      "global_before": global_before,
                      "global_after": global_after, "local": local,
                      "fallbacks": len(report["compiler_fallbacks"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
