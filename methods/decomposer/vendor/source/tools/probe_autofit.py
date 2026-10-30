#!/usr/bin/env python3
"""Measure an OOXML no-autofit text probe without changing the source deck."""
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pptx", required=True, type=Path)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--baseline-preview", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--target", action="append", default=[])
    args = parser.parse_args()
    out = args.out.resolve()
    if out.exists():
        raise FileExistsError(out)
    out.mkdir(parents=True)
    with zipfile.ZipFile(args.pptx) as source:
        members = {name: source.read(name) for name in source.namelist()}
    slide_name = "ppt/slides/slide1.xml"
    tree = ET.fromstring(members[slide_name])
    changed = []
    for shape in tree.findall(".//p:sp", NS):
        properties = shape.find("p:nvSpPr/p:cNvPr", NS)
        if properties is None or (args.target and properties.get("name") not in args.target):
            continue
        body = shape.find("p:txBody/a:bodyPr", NS)
        if body is None:
            continue
        current = body.find(f"{{{A}}}normAutofit")
        if current is None:
            continue
        body.remove(current)
        ET.SubElement(body, f"{{{A}}}noAutofit")
        changed.append(properties.get("name"))
    if args.target and set(changed) != set(args.target):
        raise ValueError(f"targets without normAutofit: {sorted(set(args.target) - set(changed))}")
    members[slide_name] = ET.tostring(tree, encoding="utf-8", xml_declaration=True)
    pptx = out / "no-autofit.pptx"
    with zipfile.ZipFile(pptx, "w", zipfile.ZIP_DEFLATED) as target:
        for name, payload in members.items():
            target.writestr(name, payload)
    with Image.open(args.reference) as image:
        canvas = image.size
    config = json.loads(args.config.read_text())
    preview = out / "preview.png"
    render_report = render(pptx, preview, canvas, config["runtime"])
    assessment = compare(args.reference, args.baseline_preview, preview, [0, 0, *canvas])
    result = {"changed_shapes": changed, "render": render_report, "assessment": assessment}
    write_json(out / "probe.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
