#!/usr/bin/env python3
"""Diagnostic native styled-multiline text registration and fresh render."""
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
from decomposer.styled_multiline_registration import apply, propose


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()
    source, out = args.source_run.resolve(), args.out.resolve()
    if out.exists():
        raise FileExistsError(out)
    out.mkdir(parents=True)
    scene = read_json(source / "output" / "scene.json")
    image = source / "input" / "normalized.png"
    proposals = propose(scene, image, source / "output" / "preview.png")
    candidate = apply(scene, proposals)
    write_json(out / "scene.json", candidate)
    report = compile_scene(out / "scene.json", out, read_json(args.config), True)
    build_provenance(candidate, out / "editable.pptx", out)
    with Image.open(image) as reference:
        canvas = reference.size
    assessment = compare(image, source / "output" / "preview.png", out / "preview.png", [0, 0, *canvas])
    write_json(out / "probe.json", {"source_run": str(source), "status": "diagnostic_only_not_checkpoint_evidence",
                                    "proposals": proposals, "assessment": assessment,
                                    "fallbacks": report["compiler"].get("fallbacks", [])})
    print({"proposals": [(row["id"], row["shift_x"], row["shift_y"]) for row in proposals],
           "before": assessment["before_global"], "after": assessment["after_global"],
           "fallbacks": report["compiler"].get("fallbacks", [])})


if __name__ == "__main__":
    main()
