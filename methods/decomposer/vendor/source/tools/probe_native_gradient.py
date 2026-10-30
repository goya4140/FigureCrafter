#!/usr/bin/env python3
"""Probe source-backed vertical native DrawingML gradients without changing production policy."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import zipfile
import xml.etree.ElementTree as ET

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from decomposer.common import read_json, write_json
from decomposer.pptx import compile_scene, render
from decomposer.provenance import build as build_provenance
from decomposer.quality import compare

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
NS = {"a": A, "p": P}


def _color(top: tuple[int, ...], bottom: tuple[int, ...], position: float) -> str:
    return "".join(f"{round(left + (right - left) * position):02X}" for left, right in zip(top, bottom))


def patch_gradient(pptx: Path, segments: list[tuple[str, str, str]], angle: int) -> list[str]:
    with zipfile.ZipFile(pptx) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    slide = "ppt/slides/slide1.xml"
    tree = ET.fromstring(files[slide])
    by_id = {shape.find("p:nvSpPr/p:cNvPr", NS).get("name"): shape for shape in tree.findall(".//p:sp", NS) if shape.find("p:nvSpPr/p:cNvPr", NS) is not None}
    patched = []
    for shape_id, first, last in segments:
        shape = by_id.get(shape_id)
        if shape is None:
            raise ValueError(f"gradient shape not found: {shape_id}")
        properties = shape.find("p:spPr", NS)
        if properties is None:
            raise ValueError(f"shape properties missing: {shape_id}")
        for child in list(properties):
            if child.tag in {f"{{{A}}}solidFill", f"{{{A}}}noFill", f"{{{A}}}gradFill"}:
                properties.remove(child)
        gradient = ET.Element(f"{{{A}}}gradFill", rotWithShape="1")
        stops = ET.SubElement(gradient, f"{{{A}}}gsLst")
        for position, color in ((0, first), (100000, last)):
            stop = ET.SubElement(stops, f"{{{A}}}gs", pos=str(position))
            ET.SubElement(stop, f"{{{A}}}srgbClr", val=color)
        ET.SubElement(gradient, f"{{{A}}}lin", ang=str(angle * 60000), scaled="1")
        geometry_index = next((index for index, child in enumerate(properties) if child.tag in {f"{{{A}}}prstGeom", f"{{{A}}}custGeom"}), 1)
        properties.insert(geometry_index + 1, gradient)
        patched.append(shape_id)
    files[slide] = ET.tostring(tree, encoding="utf-8", xml_declaration=True)
    temporary = pptx.with_suffix(".gradient.tmp")
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    temporary.replace(pptx)
    return patched


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--top", default="99AFDE", help="top source-backed RGB")
    parser.add_argument("--bottom", default="F8CCB1", help="bottom source-backed RGB")
    parser.add_argument("--angle", type=int, choices=(90, 270), default=90)
    args = parser.parse_args()
    source, out = args.source_run.resolve(), args.out.resolve()
    if out.exists():
        raise FileExistsError(out)
    out.mkdir(parents=True)
    scene = read_json(source / "output" / "scene.json")
    segments = [element for element in scene["elements"] if element["id"].startswith("focus_bar/") and element["id"] != "focus_bar/base"]
    if len(segments) != 6:
        raise ValueError(f"expected six focus-bar segments, found {len(segments)}")
    segments.sort(key=lambda item: item["bbox"][1])
    top = tuple(bytes.fromhex(args.top))
    bottom = tuple(bytes.fromhex(args.bottom))
    if len(top) != 3 or len(bottom) != 3:
        raise ValueError("RGB colors must be six hex digits")
    start, end = segments[0]["bbox"][1], segments[-1]["bbox"][1] + segments[-1]["bbox"][3]
    specification = []
    for element in segments:
        y, height = element["bbox"][1], element["bbox"][3]
        specification.append((element["id"], _color(top, bottom, (y - start) / (end - start)), _color(top, bottom, (y + height - start) / (end - start))))
    write_json(out / "scene.json", scene)
    config = read_json(args.config)
    report = compile_scene(out / "scene.json", out, config, True)
    patched = patch_gradient(out / "editable.pptx", specification, args.angle)
    with Image.open(source / "input" / "normalized.png") as image:
        canvas = image.size
    render(out / "editable.pptx", out / "preview.png", canvas, config["runtime"])
    build_provenance(scene, out / "editable.pptx", out)
    assessment = compare(source / "input" / "normalized.png", source / "output" / "preview.png", out / "preview.png", [0, 0, *canvas])
    result = {"segments": specification, "patched": patched, "angle": args.angle, "compiler_fallbacks": report["compiler"].get("fallbacks"), "assessment": assessment}
    write_json(out / "probe.json", result)
    print({"patched": patched, "assessment": assessment})


if __name__ == "__main__":
    main()
