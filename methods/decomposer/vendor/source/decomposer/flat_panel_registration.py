"""Source-measured fill and bounded geometry recovery for large native panels."""
from __future__ import annotations

import copy
from pathlib import Path
import shutil
from typing import Any

import numpy as np
from PIL import Image

from .common import read_json, sha256, write_json
from .pptx import compile_scene
from .provenance import build as build_provenance
from .quality import compare
from .text_registration import ARTIFACTS, _adopt_candidate


def _longest(indices: np.ndarray) -> tuple[int, int] | None:
    if not len(indices):
        return None
    groups = np.split(indices, np.flatnonzero(np.diff(indices) > 1) + 1)
    group = max(groups, key=len)
    return int(group[0]), int(group[-1])


def measure(scene: dict[str, Any], reference_path: Path) -> list[dict[str, Any]]:
    with Image.open(reference_path) as image:
        source = np.asarray(image.convert("RGB"), dtype=np.int16)
    canvas_height, canvas_width = source.shape[:2]
    area = canvas_height * canvas_width
    proposals = []
    for element in scene["elements"]:
        box = element["bbox"]
        x, y, width, height = (round(value) for value in box)
        fill = str(element.get("style", {}).get("fill") or "")
        if (element["kind"] != "rect" or not element["style"].get("shadow")
                or element["style"].get("stroke") != "none"
                or width * height < .10 * area or width < 80 or height < 80 or height < width
                or len(fill) != 7 or not fill.startswith("#")
                or any(char not in "0123456789abcdefABCDEF" for char in fill[1:])
                or x < 20 or y < 20 or x + width + 20 >= canvas_width
                or y + height + 20 >= canvas_height):
            continue
        old_color = np.array([int(fill[i:i + 2], 16) for i in (1, 3, 5)], dtype=np.int16)
        interior = source[y + 15:y + height - 15, x + 15:x + width - 15]
        nearby = interior[np.max(np.abs(interior - old_color), axis=2) <= 8]
        if len(nearby) < .15 * interior.shape[0] * interior.shape[1]:
            continue
        values, counts = np.unique(nearby, axis=0, return_counts=True)
        source_color = values[counts.argmax()]
        if np.max(np.abs(source_color - old_color)) > 8:
            continue
        row_y = y + height - 12
        row_left, row_right = x - 20, x + width + 20
        row_match = np.max(np.abs(source[row_y, row_left:row_right] - source_color), axis=1) <= 2
        extent = _longest(np.flatnonzero(row_match))
        if extent is None or extent[1] - extent[0] < .8 * width:
            continue
        source_left, source_right = extent[0] + row_left, extent[1] + row_left
        bottom_match = np.max(np.abs(source[y + height - 20:y + height + 21,
                                            x + 20:x + width - 20] - source_color), axis=2) <= 2
        coverage = bottom_match.mean(axis=1)
        rows = np.flatnonzero(coverage >= .5)
        if not len(rows):
            continue
        source_bottom = y + height - 20 + int(rows[-1]) + 1
        if (abs(source_left - x) > 12 or abs((source_right + 1) - (x + width)) > 12
                or abs(source_bottom - (y + height)) > 12):
            continue
        new_fill = "#" + "".join(f"{int(value):02X}" for value in source_color)
        variants = []
        if new_fill != fill:
            variants.append({"name": "fill-only", "bbox": box[:], "fill": new_fill})
        for inset in (0, 2):
            left = source_left - inset
            bbox = [float(left), box[1], float(source_right + 1 - left), float(source_bottom - y)]
            if bbox != box or new_fill != fill:
                variants.append({"name": f"measured-extent-minus-{inset}", "bbox": bbox, "fill": new_fill})
        if variants:
            proposals.append({
                "id": element["id"], "source_label_id": element["source_label_id"],
                "old_bbox": box[:], "old_fill": fill,
                "source_fill": new_fill, "source_left": source_left,
                "source_right": source_right, "source_bottom": source_bottom,
                "row_y": row_y, "source_color_pixels": int(counts.max()),
                "variants": variants,
            })
    return proposals


def apply(scene: dict[str, Any], proposal: dict[str, Any], variant: dict[str, Any]) -> dict[str, Any]:
    candidate = copy.deepcopy(scene)
    element = next((item for item in candidate["elements"] if item["id"] == proposal["id"]), None)
    if (element is None or element["kind"] != "rect"
            or element["source_label_id"] != proposal["source_label_id"]
            or element["bbox"] != proposal["old_bbox"]
            or element["style"].get("fill") != proposal["old_fill"]):
        raise ValueError("flat-panel proposal identity drift")
    element["bbox"] = variant["bbox"][:]
    element["style"]["fill"] = variant["fill"]
    return candidate


def run(root: Path, reference_path: Path, config: dict[str, Any]) -> dict[str, Any]:
    output = root / "output"
    evidence = root / "flat-panel-registration"
    prior_path = root / "post-short-text-registration" / "decision.json"
    if not prior_path.is_file():
        raise ValueError("flat-panel registration requires post-short-text evidence")
    prior_final = read_json(prior_path)["final_scene_sha256"]
    current_hash = sha256(output / "scene.json")
    if evidence.exists():
        decision_path = evidence / "decision.json"
        if not decision_path.is_file():
            raise ValueError("incomplete flat-panel evidence; inspect before resuming")
        decision = read_json(decision_path)
        if current_hash == decision.get("final_scene_sha256"):
            return decision
        if current_hash == decision.get("baseline_scene_sha256") and decision.get("accepted"):
            _adopt_candidate(evidence / "final", output)
            if (sha256(output / "scene.json") != decision.get("final_scene_sha256")
                    or sha256(output / "editable.pptx") != decision.get("final_pptx_sha256")):
                raise ValueError("reused flat-panel candidate hash mismatch")
            return decision
        if current_hash == decision.get("baseline_scene_sha256") and not decision.get("accepted"):
            return decision
        raise ValueError("flat-panel identity drift")
    if current_hash != prior_final:
        raise ValueError("flat-panel input is not post-short-text final scene")
    scene = read_json(output / "scene.json")
    proposals = measure(scene, reference_path)
    boxes = [row["old_bbox"] for row in proposals]
    region = ([min(box[0] for box in boxes), min(box[1] for box in boxes),
               max(box[0] + box[2] for box in boxes) - min(box[0] for box in boxes),
               max(box[1] + box[3] for box in boxes) - min(box[1] for box in boxes)]
              if boxes else [0, 0, scene["canvas"]["width"], scene["canvas"]["height"]])
    result: dict[str, Any] = {
        "owner_stage": "layout", "mechanism": "source-measured-large-flat-native-panel-registration",
        "selected_candidate_id": "sequential-source-measured-panel-variants",
        "affected_label_ids": sorted({row["source_label_id"] for row in proposals}),
        "affected_primitive_ids": [row["id"] for row in proposals],
        "field": "bbox+fill", "evaluation_region": region,
        "evaluation_region_source": "affected_scene_owners" if boxes else "full_canvas_no_candidate",
        "proposals": proposals, "attempts": [], "accepted": False,
        "baseline_scene_sha256": current_hash,
        "baseline_pptx_sha256": sha256(output / "editable.pptx"),
    }
    evidence.mkdir(exist_ok=False)
    baseline_dir = evidence / "baseline"
    baseline_dir.mkdir()
    for name in ARTIFACTS:
        if (output / name).is_file():
            shutil.copy2(output / name, baseline_dir / name)
    for proposal in proposals:
        trial_rows = []
        for variant in proposal["variants"]:
            index = len(result["attempts"]) + len(trial_rows) + 1
            candidate_dir = evidence / f"candidate-{index:03d}"
            candidate_dir.mkdir()
            candidate_scene = apply(read_json(output / "scene.json"), proposal, variant)
            write_json(candidate_dir / "scene.json", candidate_scene)
            report = compile_scene(candidate_dir / "scene.json", candidate_dir, config, True)
            build_provenance(candidate_scene, candidate_dir / "editable.pptx", candidate_dir)
            with Image.open(reference_path) as image:
                canvas = image.size
            assessment = compare(reference_path, output / "preview.png", candidate_dir / "preview.png",
                                 region)
            before, after = assessment["before_global"], assessment["after_global"]
            baseline_fallbacks = read_json(output / "report.json")["compiler"].get("fallbacks", [])
            candidate_fallbacks = report["compiler"].get("fallbacks", [])
            qualified = (report["status"] == "render_success" and candidate_fallbacks == baseline_fallbacks
                         and after["pixel_similarity"] > before["pixel_similarity"]
                         and (after["edge_iou"] or 0) >= (before["edge_iou"] or 0))
            trial_rows.append({
                "proposal_id": proposal["id"], "variant": variant,
                "qualified": qualified, "selected": False, "assessment": assessment,
                "baseline_scene_sha256": sha256(output / "scene.json"),
                "baseline_pptx_sha256": sha256(output / "editable.pptx"),
                "candidate_scene_sha256": sha256(candidate_dir / "scene.json"),
                "candidate_pptx_sha256": sha256(candidate_dir / "editable.pptx"),
                "baseline_fallbacks": baseline_fallbacks,
                "candidate_fallbacks": candidate_fallbacks,
                "compiler_status": report["status"],
            })
        qualified_rows = [(index, row) for index, row in enumerate(trial_rows) if row["qualified"]]
        if qualified_rows:
            chosen_index, chosen = max(
                qualified_rows,
                key=lambda item: (item[1]["assessment"]["after_global"]["pixel_similarity"],
                                  item[1]["assessment"]["after_global"]["edge_iou"] or 0),
            )
            chosen["selected"] = True
            candidate_dir = evidence / f"candidate-{len(result['attempts']) + chosen_index + 1:03d}"
            _adopt_candidate(candidate_dir, output)
            result["accepted"] = True
        result["attempts"].extend(trial_rows)
    result["status"] = "accepted" if result["accepted"] else "rejected" if proposals else "unchanged"
    result["final_scene_sha256"] = sha256(output / "scene.json")
    result["final_pptx_sha256"] = sha256(output / "editable.pptx")
    if result["accepted"]:
        final_dir = evidence / "final"
        final_dir.mkdir()
        for name in ARTIFACTS:
            if (output / name).is_file():
                shutil.copy2(output / name, final_dir / name)
    write_json(evidence / "decision.json", result)
    return result
