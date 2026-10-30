#!/usr/bin/env python3
"""Measure native editable per-line text boxes while preserving styled source runs."""
from __future__ import annotations

import argparse
import copy
from pathlib import Path
import sys

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from decomposer.common import read_json, write_json
from decomposer.pptx import compile_scene
from decomposer.provenance import build as build_provenance
from decomposer.quality import compare


def split_runs(element: dict) -> list[list[dict]]:
    lines: list[list[dict]] = [[]]
    for run in element.get("runs") or [{"text": element["text"], "source_text": element["text"], "style": element["style"], "script": "normal"}]:
        for index, segment in enumerate(run["text"].split("\n")):
            if index:
                lines.append([])
            if segment:
                piece = copy.deepcopy(run)
                piece["text"] = segment
                piece["source_text"] = segment
                lines[-1].append(piece)
    if ["".join(run["text"] for run in line) for line in lines] != element["text"].split("\n"):
        raise ValueError("split styled runs did not preserve source lines")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", required=True, type=Path)
    parser.add_argument("--scene-dir", type=Path, help="prior probe directory containing scene.json and preview.png")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--target", required=True)
    parser.add_argument("--first-top", type=float)
    parser.add_argument("--line-advance", type=float)
    parser.add_argument("--line-tops", help="comma-separated source-measured top per line")
    parser.add_argument("--line-height", required=True, type=float)
    args = parser.parse_args()
    source, out = args.source_run.resolve(), args.out.resolve()
    scene_dir = args.scene_dir.resolve() if args.scene_dir else source / "output"
    if out.exists():
        raise FileExistsError(out)
    out.mkdir(parents=True)
    scene = read_json(scene_dir / "scene.json")
    by_id = {element["id"]: element for element in scene["elements"]}
    original = by_id.get(args.target)
    if not original or original["kind"] != "text" or "\n" not in original["text"]:
        raise ValueError("target must be multiline native text")
    lines = split_runs(original)
    line_tops = [float(value) for value in args.line_tops.split(",")] if args.line_tops else None
    if line_tops is not None and len(line_tops) != len(lines):
        raise ValueError("line-tops must have one position per exact source line")
    if line_tops is None and (args.first_top is None or args.line_advance is None):
        raise ValueError("provide line-tops or first-top and line-advance")
    replacements = []
    for index, runs in enumerate(lines):
        element = copy.deepcopy(original)
        element["id"] = f"{original['id']}/line-{index + 1}"
        element["runs"] = runs
        element["text"] = "".join(run["text"] for run in runs)
        element["source_primitive_id"] = original["id"]
        element["source_line_index"] = index
        element["source_line_count"] = len(lines)
        element["line_break_after"] = index < len(lines) - 1
        top = line_tops[index] if line_tops is not None else args.first_top + index * args.line_advance
        element["bbox"] = [original["bbox"][0], top, original["bbox"][2], args.line_height]
        replacements.append(element)
    element_index = scene["elements"].index(original)
    scene["elements"][element_index:element_index + 1] = replacements
    z = scene["z_order"].index(args.target)
    scene["z_order"][z:z + 1] = [element["id"] for element in replacements]
    write_json(out / "scene.json", scene)
    report = compile_scene(out / "scene.json", out, read_json(args.config), True)
    build_provenance(scene, out / "editable.pptx", out)
    with Image.open(source / "input" / "normalized.png") as image:
        canvas = image.size
    assessment = compare(source / "input" / "normalized.png", scene_dir / "preview.png", out / "preview.png", [0, 0, *canvas])
    result = {"original": args.target, "lines": [element["id"] for element in replacements], "parameters": {"first_top": args.first_top, "line_advance": args.line_advance, "line_tops": line_tops, "line_height": args.line_height}, "compiler_fallbacks": report["compiler"].get("fallbacks"), "assessment": assessment}
    write_json(out / "probe.json", result)
    print(result)


if __name__ == "__main__":
    main()
