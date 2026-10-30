#!/usr/bin/env python3
"""Recompile an unchanged scene to measure the candidate relative-leading compiler."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from decomposer.common import read_json, write_json
from decomposer.pptx import compile_scene
from decomposer.quality import compare


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    run, out = args.run.resolve(), args.out.resolve()
    if out.exists():
        raise FileExistsError(out)
    scene = read_json(run / "output" / "scene.json")
    owners = [item for item in scene["elements"] if item["kind"] == "text"
              and 0.5 <= float(item["style"].get("line_spacing") or 0) <= 3]
    if not owners:
        raise ValueError("scene has no relative-leading text")
    out.mkdir(parents=True)
    shutil.copy2(run / "output" / "scene.json", out / "scene.json")
    report = compile_scene(out / "scene.json", out, read_json(args.config), True)
    reference_path = run / "input" / "normalized.png"
    with Image.open(reference_path) as image:
        canvas = image.size
        source = np.asarray(image.convert("RGB"))
    with Image.open(out / "preview.png") as image:
        preview = np.asarray(image.convert("RGB"))
    ink = []
    for owner in owners:
        x, y, w, h = (round(float(value)) for value in owner["bbox"])
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(canvas[0], x + w), min(canvas[1], y + h)
        source_dark = int((source[y0:y1, x0:x1].mean(axis=2) < 160).sum())
        rendered_dark = int((preview[y0:y1, x0:x1].mean(axis=2) < 160).sum())
        ink.append({"id": owner["id"], "source_dark_pixels": source_dark,
                    "rendered_dark_pixels": rendered_dark})
    assessment = compare(reference_path, run / "output" / "preview.png",
                         out / "preview.png", [0, 0, *canvas])
    result = {
        "affected_primitive_ids": [item["id"] for item in owners],
        "affected_label_ids": sorted({item["source_label_id"] for item in owners}),
        "evaluation_region": [0, 0, *canvas],
        "evaluation_region_source": "full_canvas_compiler_regression",
        "compiler_status": report["status"],
        "fallbacks": report["compiler"].get("fallbacks", []),
        "ink": ink, "assessment": assessment,
    }
    write_json(out / "probe.json", result)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
