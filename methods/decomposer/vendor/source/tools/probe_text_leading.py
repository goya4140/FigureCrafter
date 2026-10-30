#!/usr/bin/env python3
"""Probe an editable OOXML paragraph's line spacing and top anchor together."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import zipfile
import xml.etree.ElementTree as ET

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from decomposer.common import write_json
from decomposer.pptx import render
from decomposer.quality import compare

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
NS = {"a": A, "p": P}
EMU_PER_SOURCE_PIXEL = 9525


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--target", required=True)
    parser.add_argument("--line-spacing-factor", required=True, type=float)
    parser.add_argument("--top-shift", required=True, type=float, help="source pixels; positive means down")
    args = parser.parse_args()
    if not 0.75 <= args.line_spacing_factor <= 1.35 or not -20 <= args.top_shift <= 20:
        raise ValueError("probe outside bounded text-layout range")
    source, out = args.source_run.resolve(), args.out.resolve()
    if out.exists():
        raise FileExistsError(out)
    out.mkdir(parents=True)
    with zipfile.ZipFile(source / "output" / "editable.pptx") as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    slide_name = "ppt/slides/slide1.xml"
    tree = ET.fromstring(files[slide_name])
    changed = []
    for shape in tree.findall(".//p:sp", NS):
        properties = shape.find("p:nvSpPr/p:cNvPr", NS)
        if properties is None or properties.get("name") != args.target:
            continue
        transform = shape.find("p:spPr/a:xfrm/a:off", NS)
        if transform is None:
            raise ValueError("text target has no transform")
        old_y = int(transform.get("y"))
        transform.set("y", str(old_y + round(args.top_shift * EMU_PER_SOURCE_PIXEL)))
        for paragraph in shape.findall("p:txBody/a:p", NS):
            spacing = paragraph.find("a:pPr/a:lnSpc/a:spcPts", NS)
            if spacing is None:
                raise ValueError("text target lacks exact line spacing")
            old_value = int(spacing.get("val"))
            spacing.set("val", str(round(old_value * args.line_spacing_factor)))
            changed.append({"old_spc_pts": old_value, "new_spc_pts": int(spacing.get("val"))})
        break
    if not changed:
        raise ValueError(f"text target not found: {args.target}")
    files[slide_name] = ET.tostring(tree, encoding="utf-8", xml_declaration=True)
    pptx = out / "leading.pptx"
    with zipfile.ZipFile(pptx, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    config = json.loads(args.config.read_text())
    with Image.open(source / "input" / "normalized.png") as image:
        canvas = image.size
    render(pptx, out / "preview.png", canvas, config["runtime"])
    assessment = compare(source / "input" / "normalized.png", source / "output" / "preview.png", out / "preview.png", [0, 0, *canvas])
    result = {"target": args.target, "line_spacing_factor": args.line_spacing_factor, "top_shift": args.top_shift, "paragraphs": changed, "assessment": assessment}
    write_json(out / "probe.json", result)
    print(result)


if __name__ == "__main__":
    main()
