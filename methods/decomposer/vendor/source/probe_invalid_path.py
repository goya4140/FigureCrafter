#!/usr/bin/env python3
"""Recompile a terminal scene after native-rect repair of an invalid path."""
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
    parser.add_argument("--element", required=True)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    run, out = args.run.resolve(), args.out.resolve()
    if out.exists():
        raise FileExistsError(out)
    source_scene = run / "output/scene.json"
    scene = deepcopy(json.loads(source_scene.read_text()))
    element = next(item for item in scene["elements"] if item["id"] == args.element)
    if element["kind"] != "path" or str(element.get("path") or "").strip():
        raise ValueError("probe requires an empty path element")
    if element.get("arrow_start") or element.get("arrow_end"):
        raise ValueError("arrowed paths need a separate repair")
    element["kind"] = "rect"
    for key in ("path", "path_viewbox", "fallback_kind", "arrow_start", "arrow_end"):
        element.pop(key, None)
    out.mkdir(parents=True)
    scene_path = out / "scene.json"
    write_json(scene_path, scene)
    config = json.loads(args.config.read_text())
    compiled = compile_scene(scene_path, out / "output", config, render_output=True)
    source = run / "input/normalized.png"
    with Image.open(source) as reference, Image.open(run / "output/preview.png") as old_preview, Image.open(out / "output/preview.png") as new_preview:
        global_before = metrics(reference, old_preview)
        global_after = metrics(reference, new_preview)
        local_before = metrics(reference, old_preview, element["bbox"])
        local_after = metrics(reference, new_preview, element["bbox"])
    report = {
        "schema_version": "decomposer.invalid-path-probe.v1",
        "sample_id": run.name, "element_id": args.element,
        "source_scene_sha256": sha256(source_scene),
        "source_pptx_sha256": sha256(run / "output/editable.pptx"),
        "new_scene_sha256": sha256(scene_path),
        "new_pptx_sha256": sha256(out / "output/editable.pptx"),
        "compiler_fallbacks": compiled["compiler"].get("fallbacks", []),
        "structure": compiled["structure"],
        "global_before": global_before, "global_after": global_after,
        "local_before": local_before, "local_after": local_after,
    }
    write_json(out / "probe-report.json", report)
    print(json.dumps({
        "sample_id": run.name, "element_id": args.element,
        "global_before": global_before, "global_after": global_after,
        "compiler_fallbacks": len(report["compiler_fallbacks"]),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
