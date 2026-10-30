#!/usr/bin/env python3
"""Replay a mixed-text/math heading with native editable PowerPoint text runs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import xml.etree.ElementTree as ET
import zipfile

from decomposer.assemble import assemble
from decomposer.common import read_json, write_json
from decomposer.pptx import compile_scene
from decomposer.provenance import build as build_provenance
from decomposer.quality import compare
from decomposer.refinement import apply_patch
from decomposer.validation import validate_run


HEADING = re.compile(r"^(.*?)(I)_([A-Za-z]+)(.*?)(I)_([A-Za-z]+)(.*)$")


def runs_for(text: str) -> list[dict]:
    match = HEADING.fullmatch(text)
    if not match:
        raise ValueError(f"unsupported proof heading: {text!r}")
    before, first_i, first_sub, middle, second_i, second_sub, after = match.groups()
    return [
        {"text": before, "source_text": before, "style": {}, "script": "normal"},
        {"text": first_i, "source_text": first_i, "style": {"italic": True}, "script": "normal"},
        {"text": first_sub, "source_text": "_" + first_sub, "style": {"italic": True}, "script": "subscript"},
        {"text": middle, "source_text": middle, "style": {}, "script": "normal"},
        {"text": second_i, "source_text": second_i, "style": {"italic": True}, "script": "normal"},
        {"text": second_sub, "source_text": "_" + second_sub, "style": {"italic": True}, "script": "subscript"},
        {"text": after, "source_text": after, "style": {}, "script": "normal"},
    ]


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
    recognition = read_json(target / "recognition/recognition.json")
    representation = read_json(target / "representation/representation.json")
    layout = read_json(target / "layout/layout.json")
    by_component = {item["label_id"]: item for item in representation["components"]}
    operations = []
    for label_id in ("title1", "title2"):
        primitive = next(item for item in by_component[label_id]["primitives"] if item["kind"] == "text")
        operations.append({
            "op": "primitive_text_runs", "label_id": label_id,
            "primitive_id": f"{label_id}/{primitive['id']}", "runs": runs_for(primitive["text"]),
        })
    decision = {"owner_stage": "representation", "affected_label_ids": ["title1", "title2"], "operations": operations}
    _, patched, _ = apply_patch(recognition, representation, layout, decision)
    write_json(target / "representation/representation.json", patched)
    scene = assemble(recognition, patched, layout)
    write_json(target / "output/scene.json", scene)
    before_preview = source / "output/preview.png"
    config = read_json(args.config)
    compile_scene(target / "output/scene.json", target / "output", config, render_output=True)
    build_provenance(scene, target / "output/editable.pptx", target / "output")
    validation = validate_run(target, target / "input/normalized.png", config)
    affected = [item["bbox"] for item in scene["elements"] if item["source_label_id"] in {"title1", "title2"}]
    x1 = min(item[0] for item in affected); y1 = min(item[1] for item in affected)
    x2 = max(item[0] + item[2] for item in affected); y2 = max(item[1] + item[3] for item in affected)
    assessment = compare(target / "input/normalized.png", before_preview, target / "output/preview.png", [x1, y1, x2 - x1, y2 - y1])
    with zipfile.ZipFile(target / "output/editable.pptx") as archive:
        root = ET.fromstring(archive.read("ppt/slides/slide1.xml"))
    ns = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
    visible_text = "".join(item.text or "" for item in root.findall(".//a:t", ns))
    baselines = [int(item.get("baseline", "0")) for item in root.findall(".//a:rPr", ns)]
    proof = {
        "schema_version": "decomposer.styled-text-runs-proof.v1",
        "version": "0.5.6",
        "source_run": str(source),
        "labels": ["title1", "title2"],
        "source_identity_preserved": all(
            "".join(run["source_text"] for run in primitive["runs"]) == primitive["text"]
            for component in patched["components"] if component["label_id"] in {"title1", "title2"}
            for primitive in component["primitives"] if primitive["kind"] == "text"
        ),
        "literal_heading_markup_absent_from_pptx": "I_Source" not in visible_text and "I_Bald" not in visible_text and "I_Target" not in visible_text,
        "native_subscript_runs_present": any(value < 0 for value in baselines),
        "assessment": assessment,
        "validation_passed": validation["passed"],
        "validation_check_count": len(validation["checks"]),
        "pptx": str(target / "output/editable.pptx"),
    }
    write_json(target / "proof.json", proof)
    print(json.dumps(proof, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
