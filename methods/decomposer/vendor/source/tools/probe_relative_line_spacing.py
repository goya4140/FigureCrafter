#!/usr/bin/env python3
"""Measure whether short line-spacing values are relative, not absolute points."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from decomposer.common import read_json, write_json
from decomposer.pptx import compile_scene
from decomposer.quality import affected_scene_region, compare


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--target", action="append", default=[])
    args = parser.parse_args()
    run, out = args.run.resolve(), args.out.resolve()
    if out.exists():
        raise FileExistsError(out)
    before = read_json(run / "output" / "scene.json")
    candidate = read_json(run / "output" / "scene.json")
    changes = []
    targets = set(args.target)
    for element in candidate["elements"]:
        if element["kind"] != "text" or (targets and element["id"] not in targets):
            continue
        style = element["style"]
        value = float(style.get("line_spacing") or 0)
        if not 0.5 <= value <= 3:
            continue
        font_size = float(style["font_size"])
        # The compiler passes lineSpacing to PPTXGenJS as points; native
        # source font_size is in pixels, so convert a relative em ratio.
        converted = round(value * font_size * 0.75, 4)
        style["line_spacing"] = converted
        changes.append({"id": element["id"], "old": value, "new": converted})
    if targets != {item["id"] for item in changes} and targets:
        raise ValueError("a requested target was not a text shape with relative spacing")
    if not changes:
        raise ValueError("no relative-spacing text owner found")
    out.mkdir(parents=True)
    write_json(out / "scene.json", candidate)
    report = compile_scene(out / "scene.json", out, read_json(args.config), True)
    region = affected_scene_region(before, candidate, [item["id"] for item in changes])
    assessment = compare(
        run / "input" / "normalized.png", run / "output" / "preview.png",
        out / "preview.png", region,
    )
    result = {
        "owner_stage": "representation",
        "mechanism": "relative-text-leading-interpreted-as-absolute-points",
        "affected_label_ids": sorted({item["id"].split("/", 1)[0] for item in changes}),
        "affected_primitive_ids": [item["id"] for item in changes],
        "evaluation_region": region,
        "evaluation_region_source": "affected_scene_owners",
        "changes": changes,
        "compiler_status": report["status"],
        "compiler_fallbacks": report["compiler"].get("fallbacks", []),
        "assessment": assessment,
    }
    write_json(out / "probe.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
