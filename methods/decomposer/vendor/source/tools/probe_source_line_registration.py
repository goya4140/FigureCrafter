#!/usr/bin/env python3
"""Isolated source-line registration probe; never mutates a production run."""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from decomposer.common import read_json, write_json
from decomposer.pptx import compile_scene
from decomposer.provenance import build as build_provenance
from decomposer.quality import compare
from decomposer.source_line_registration import apply, propose


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", required=True, type=Path)
    parser.add_argument("--scene-dir", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()
    source = args.source_run.resolve()
    baseline = args.scene_dir.resolve() if args.scene_dir else source / "output"
    out = args.out.resolve()
    if out.exists():
        raise FileExistsError(out)
    out.mkdir(parents=True)
    config = read_json(args.config)
    reference = source / "input" / "normalized.png"
    scene = read_json(baseline / "scene.json")
    proposals = propose(scene, reference, baseline / "preview.png")
    current = baseline
    decisions = []
    for index, proposal in enumerate(proposals, 1):
        candidate_scene = apply(scene, proposal)
        candidate_dir = out / f"candidate-{index:03d}"
        candidate_dir.mkdir()
        write_json(candidate_dir / "scene.json", candidate_scene)
        report = compile_scene(candidate_dir / "scene.json", candidate_dir, config, True)
        build_provenance(candidate_scene, candidate_dir / "editable.pptx", candidate_dir)
        canvas = candidate_scene["canvas"]
        assessment = compare(reference, current / "preview.png", candidate_dir / "preview.png",
                             [0, 0, canvas["width"], canvas["height"]])
        before, after = assessment["before_global"], assessment["after_global"]
        accepted = (report["status"] == "render_success"
                    and report["compiler"].get("fallbacks", []) == read_json(current / "report.json")["compiler"].get("fallbacks", [])
                    and after["pixel_similarity"] > before["pixel_similarity"]
                    and (after["edge_iou"] or 0) >= (before["edge_iou"] or 0))
        decisions.append({"proposal": proposal, "accepted": accepted, "assessment": assessment,
                          "compiler_fallbacks": report["compiler"].get("fallbacks", [])})
        if accepted:
            scene = candidate_scene
            current = candidate_dir
    for name in ("scene.json", "editable.pptx", "preview.png", "report.json", "compiler-report.json",
                 "provenance.json", "id-mask.png", "label-index.json", "compile.log", "render.log"):
        if (current / name).is_file():
            shutil.copy2(current / name, out / name)
    result = {"source_run": str(source), "baseline": str(baseline), "decisions": decisions,
              "final_source": str(current), "status": "diagnostic_only_not_checkpoint_evidence"}
    write_json(out / "probe.json", result)
    print({"proposals": len(proposals), "accepted": [d["proposal"]["id"] for d in decisions if d["accepted"]],
           "final_source": str(current)})


if __name__ == "__main__":
    main()
