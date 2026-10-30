#!/usr/bin/env python3
"""Controlled native panel-fill recompile; never edits the source run."""
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
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--element", action="append", required=True)
    parser.add_argument("--fill", required=True)
    args = parser.parse_args()
    run, out = args.run.resolve(), args.out.resolve()
    if out.exists():
        raise FileExistsError(out)
    source_scene = run / "output/scene.json"
    scene = deepcopy(json.loads(source_scene.read_text()))
    selected = [item for item in scene["elements"] if item["id"] in set(args.element)]
    if len(selected) != len(set(args.element)):
        raise ValueError("element IDs must exist exactly once")
    changes = []
    for item in selected:
        if item["kind"] not in {"rect", "round_rect", "path", "polygon"}:
            raise ValueError("probe only accepts native filled geometry")
        before = item["style"].get("fill")
        item["style"]["fill"] = args.fill
        changes.append({"element_id": item["id"], "before": before, "after": args.fill, "bbox": item["bbox"]})
    out.mkdir(parents=True)
    scene_path = out / "scene.json"
    write_json(scene_path, scene)
    config = json.loads(args.config.read_text())
    compiled = compile_scene(scene_path, out / "output", config, render_output=True)
    with Image.open(run / "input/normalized.png") as reference, Image.open(run / "output/preview.png") as before, Image.open(out / "output/preview.png") as after:
        global_before = metrics(reference, before)
        global_after = metrics(reference, after)
        local = []
        for change in changes:
            local.append({
                "element_id": change["element_id"],
                "before": metrics(reference, before, change["bbox"]),
                "after": metrics(reference, after, change["bbox"]),
            })
    report = {
        "schema_version": "decomposer.panel-fill-probe.v1",
        "sample_id": run.name,
        "source_scene_sha256": sha256(source_scene),
        "source_pptx_sha256": sha256(run / "output/editable.pptx"),
        "source_image_sha256": sha256(run / "input/normalized.png"),
        "new_scene_sha256": sha256(scene_path),
        "new_pptx_sha256": sha256(out / "output/editable.pptx"),
        "changes": changes,
        "compiler_fallbacks": compiled["compiler"].get("fallbacks", []),
        "structure": compiled["structure"],
        "global_before": global_before,
        "global_after": global_after,
        "local": local,
    }
    write_json(out / "probe-report.json", report)
    print(json.dumps({"global_before": global_before, "global_after": global_after, "local": local, "fallbacks": len(report["compiler_fallbacks"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
