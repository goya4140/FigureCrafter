#!/usr/bin/env python3
"""Diagnose per-object native-text bbox changes from a measured reference scene.

The reference is only a source of candidate geometry. Every candidate is
recompiled and rerendered from the current scene; this is not a promotion path.
"""
from __future__ import annotations

import argparse
import copy
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from decomposer.common import read_json, write_json
from decomposer.pptx import compile_scene
from decomposer.provenance import build as build_provenance
from decomposer.quality import compare


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", required=True, type=Path)
    parser.add_argument("--reference-scene", required=True, type=Path)
    parser.add_argument("--targets-json", required=True, type=Path,
                        help="JSON with changes:[{id}] from a prior controlled probe")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()
    source, out = args.source_run.resolve(), args.out.resolve()
    if out.exists():
        raise FileExistsError(out)
    out.mkdir(parents=True)
    scene = read_json(source / "output" / "scene.json")
    reference = read_json(args.reference_scene)
    current = source / "output"
    image = source / "input" / "normalized.png"
    config = read_json(args.config)
    refs = {element["id"]: element for element in reference["elements"]}
    targets = [row["id"] for row in read_json(args.targets_json)["changes"]]
    if len(targets) != len(set(targets)):
        raise ValueError("duplicate candidate text IDs")
    decisions = []
    for index, target_id in enumerate(targets, 1):
        candidate_scene = copy.deepcopy(scene)
        element = next((item for item in candidate_scene["elements"] if item["id"] == target_id), None)
        reference_element = refs.get(target_id)
        if (element is None or reference_element is None or element["kind"] != "text"
                or reference_element["kind"] != "text" or element["text"] != reference_element["text"]
                or element["source_label_id"] != reference_element["source_label_id"]
                or element["bbox"][2] != reference_element["bbox"][2]):
            raise ValueError(f"incompatible reference text geometry: {target_id}")
        old_bbox = element["bbox"][:]
        element["bbox"] = reference_element["bbox"][:]
        candidate_dir = out / f"candidate-{index:03d}"
        candidate_dir.mkdir()
        write_json(candidate_dir / "scene.json", candidate_scene)
        report = compile_scene(candidate_dir / "scene.json", candidate_dir, config, True)
        build_provenance(candidate_scene, candidate_dir / "editable.pptx", candidate_dir)
        canvas = scene["canvas"]
        assessment = compare(image, current / "preview.png", candidate_dir / "preview.png",
                             [0, 0, canvas["width"], canvas["height"]])
        before, after = assessment["before_global"], assessment["after_global"]
        accepted = (report["status"] == "render_success"
                    and report["compiler"].get("fallbacks", []) == read_json(current / "report.json")["compiler"].get("fallbacks", [])
                    and after["pixel_similarity"] > before["pixel_similarity"]
                    and (after["edge_iou"] or 0) >= (before["edge_iou"] or 0))
        decisions.append({"id": target_id, "old_bbox": old_bbox, "candidate_bbox": element["bbox"],
                          "accepted": accepted, "assessment": assessment,
                          "compiler_fallbacks": report["compiler"].get("fallbacks", [])})
        if accepted:
            scene, current = candidate_scene, candidate_dir
    for name in ("scene.json", "editable.pptx", "preview.png", "report.json", "compiler-report.json",
                 "provenance.json", "id-mask.png", "label-index.json", "compile.log", "render.log"):
        if (current / name).is_file():
            shutil.copy2(current / name, out / name)
    write_json(out / "probe.json", {
        "source_run": str(source), "reference_scene": str(args.reference_scene.resolve()),
        "targets_json": str(args.targets_json.resolve()), "decisions": decisions,
        "final_source": str(current), "status": "diagnostic_only_not_checkpoint_evidence",
    })
    print({"accepted": [item["id"] for item in decisions if item["accepted"]],
           "final_source": str(current)})


if __name__ == "__main__":
    main()
