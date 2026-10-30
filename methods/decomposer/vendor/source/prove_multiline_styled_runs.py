#!/usr/bin/env python3
"""Recompile a stored run to prove multiline styled-run OOXML fidelity."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET
import zipfile

from decomposer.common import read_json, write_json
from decomposer.pptx import compile_scene
from decomposer.provenance import build as build_provenance
from decomposer.quality import compare
from decomposer.validation import validate_run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()
    source = args.run.resolve(); target = args.out.resolve()
    if target.exists():
        raise FileExistsError(target)
    shutil.copytree(source, target)
    before_preview = source / "output/preview.png"
    scene = read_json(target / "output/scene.json")
    config = read_json(args.config)
    compile_scene(target / "output/scene.json", target / "output", config, render_output=True)
    provenance = build_provenance(scene, target / "output/editable.pptx", target / "output")
    validation = validate_run(target, target / "input/normalized.png", config)
    assessment = compare(
        target / "input/normalized.png", before_preview, target / "output/preview.png",
        [0, 0, scene["canvas"]["width"], scene["canvas"]["height"]],
    )
    record = next(
        item
        for item in provenance["labels"]["mmos"]["ooxml_shapes"]
        if item["name"] == "mmos/text"
    )
    with zipfile.ZipFile(target / "output/editable.pptx") as archive:
        root = ET.fromstring(archive.read("ppt/slides/slide1.xml"))
    ns = {
        "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
        "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    }
    paragraphs = []
    for shape in root.findall(".//p:sp", ns):
        props = shape.find(".//p:cNvPr", ns)
        if props is None or props.get("name") != "mmos/text":
            continue
        paragraphs = [
            "".join(node.text or "" for node in paragraph.findall(".//a:t", ns))
            for paragraph in shape.findall("p:txBody/a:p", ns)
        ]
    same_line = paragraphs == ["MMOs:", "", "fluents +- δ"]
    structural_accepted = (
        validation["passed"]
        and record["text"] == "MMOs:\n\nfluents +- δ"
        and same_line
        and assessment["global_pixel_delta"] >= -0.002
    )
    proof = {
        "schema_version": "decomposer.multiline-styled-runs-proof.v1",
        "version": "0.5.7",
        "source_run": str(source),
        "element_id": "mmos/text",
        "expected_visible_text": "MMOs:\n\nfluents +- δ",
        "ooxml_visible_text": record["text"],
        "ooxml_paragraphs": paragraphs,
        "same_line_final_styled_run": same_line,
        "structural_repair_accepted": structural_accepted,
        "validation_passed": validation["passed"],
        "validation_check_count": len(validation["checks"]),
        "assessment": assessment,
        "pptx": str(target / "output/editable.pptx"),
    }
    write_json(target / "proof.json", proof)
    write_json(target / "status.json", {
        "version": "0.5.7",
        "status": "render_success",
        "degraded": bool(scene.get("degraded")),
        "validation": {
            "passed": validation["passed"],
            "visual": validation["visual"],
            "failure_count": len(validation["failures"]),
            "warning_count": len(validation["warnings"]),
        },
        "artifacts": {
            "scene": "output/scene.json", "pptx": "output/editable.pptx",
            "provenance": "output/provenance.json", "manifest": "output/page-manifest.json",
            "validation": "output/validation.json", "preview": "output/preview.png",
        },
    })
    print(json.dumps(proof, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
