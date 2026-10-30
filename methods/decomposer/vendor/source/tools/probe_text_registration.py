#!/usr/bin/env python3
"""Measure small source-backed text-position corrections on a copied scene."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from decomposer.common import read_json, write_json
from decomposer.pptx import compile_scene
from decomposer.provenance import build as build_provenance
from decomposer.quality import compare
from decomposer.text_registration import propose


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", required=True, type=Path)
    parser.add_argument("--scene-dir", type=Path, help="previous probe directory containing scene.json and preview.png")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--max-shift", type=int, default=3)
    parser.add_argument("--min-absolute-gain", type=float, default=3.0)
    parser.add_argument("--min-relative-gain", type=float, default=0.12)
    args = parser.parse_args()
    source = args.source_run.resolve()
    scene_dir = args.scene_dir.resolve() if args.scene_dir else source / "output"
    out = args.out.resolve()
    if out.exists():
        raise FileExistsError(out)
    out.mkdir(parents=True)
    scene = read_json(scene_dir / "scene.json")
    with Image.open(source / "input" / "normalized.png") as image:
        canvas = image.size
    candidate, proposals = propose(
        scene, source / "input" / "normalized.png", scene_dir / "preview.png",
        max_shift=args.max_shift, min_absolute_gain=args.min_absolute_gain,
        min_relative_gain=args.min_relative_gain,
    )
    write_json(out / "scene.json", candidate)
    report = compile_scene(out / "scene.json", out, read_json(args.config), True)
    build_provenance(candidate, out / "editable.pptx", out)
    assessment = compare(
        source / "input" / "normalized.png", scene_dir / "preview.png",
        out / "preview.png", [0, 0, *canvas],
    )
    result = {"proposals": proposals, "scene_dir": str(scene_dir), "compiler_fallbacks": report["compiler"].get("fallbacks"), "assessment": assessment}
    write_json(out / "probe.json", result)
    print({"proposals": len(proposals), "assessment": assessment})


if __name__ == "__main__":
    main()
