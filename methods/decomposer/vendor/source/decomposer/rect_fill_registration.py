"""Measure source color modes behind editable native rectangle fills."""
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


MAX_COLOR_DELTA = 15
MIN_MODE_PIXELS = 500
MIN_MODE_FRACTION = .55


def measure(scene: dict[str, Any], reference_path: Path, preview_path: Path) -> list[dict[str, Any]]:
    with Image.open(reference_path) as image:
        source = np.asarray(image.convert("RGB"), dtype=np.int16)
    with Image.open(preview_path) as image:
        preview = np.asarray(image.convert("RGB"), dtype=np.int16)
    if source.shape != preview.shape:
        raise ValueError("source and preview dimensions differ")
    proposals = []
    for element in scene["elements"]:
        if element["kind"] != "rect":
            continue
        fill = str(element.get("style", {}).get("fill") or "")
        if (len(fill) != 7 or not fill.startswith("#")
                or any(char not in "0123456789abcdefABCDEF" for char in fill[1:])):
            continue
        x, y, width, height = (round(value) for value in element["bbox"])
        if width < 40 or height < 15:
            continue
        x0, y0 = max(0, x + 3), max(0, y + 3)
        x1 = min(source.shape[1], x + width - 3)
        y1 = min(source.shape[0], y + height - 3)
        if x1 <= x0 or y1 <= y0:
            continue
        old = np.array([int(fill[i:i + 2], 16) for i in (1, 3, 5)], dtype=np.int16)
        src = source[y0:y1, x0:x1]
        rendered = preview[y0:y1, x0:x1]
        rendered_old = np.max(np.abs(rendered - old), axis=2) <= 2
        source_near = np.max(np.abs(src - old), axis=2) <= MAX_COLOR_DELTA
        matched = src[rendered_old & source_near]
        if len(matched) < max(MIN_MODE_PIXELS, .02 * (x1 - x0) * (y1 - y0)):
            continue
        values, counts = np.unique(matched, axis=0, return_counts=True)
        mode_index = counts.argmax()
        mode = values[mode_index]
        mode_pixels = int(counts[mode_index])
        if mode_pixels / len(matched) < MIN_MODE_FRACTION:
            continue
        if not np.any(mode != old):
            continue
        new_fill = "#" + "".join(f"{int(channel):02X}" for channel in mode)
        proposals.append({
            "id": element["id"], "source_label_id": element["source_label_id"],
            "old_fill": fill, "new_fill": new_fill,
            "bbox": element["bbox"][:], "mode_pixels": mode_pixels,
            "matched_pixels": len(matched),
            "source_mode_fraction": round(mode_pixels / len(matched), 6),
        })
    return proposals


def apply(scene: dict[str, Any], proposals: list[dict[str, Any]]) -> dict[str, Any]:
    candidate = copy.deepcopy(scene)
    by_id = {element["id"]: element for element in candidate["elements"]}
    for row in proposals:
        element = by_id.get(row["id"])
        if (element is None or element["kind"] != "rect"
                or element["source_label_id"] != row["source_label_id"]
                or element["bbox"] != row["bbox"]
                or element["style"].get("fill") != row["old_fill"]):
            raise ValueError("rect-fill proposal identity drift")
        element["style"]["fill"] = row["new_fill"]
    return candidate


def run(root: Path, reference_path: Path, config: dict[str, Any]) -> dict[str, Any]:
    output = root / "output"
    evidence = root / "rect-fill-registration"
    prior_path = root / "flat-panel-registration" / "decision.json"
    if not prior_path.is_file():
        raise ValueError("rect-fill registration requires flat-panel evidence")
    prior_final = read_json(prior_path)["final_scene_sha256"]
    current_hash = sha256(output / "scene.json")
    if evidence.exists():
        decision_path = evidence / "decision.json"
        if not decision_path.is_file():
            raise ValueError("incomplete rect-fill evidence; inspect before resuming")
        decision = read_json(decision_path)
        if current_hash == decision.get("final_scene_sha256"):
            return decision
        if current_hash == decision.get("baseline_scene_sha256") and decision.get("accepted"):
            _adopt_candidate(evidence / "candidate", output)
            if (sha256(output / "scene.json") != decision.get("final_scene_sha256")
                    or sha256(output / "editable.pptx") != decision.get("final_pptx_sha256")):
                raise ValueError("reused rect-fill candidate hash mismatch")
            return decision
        if current_hash == decision.get("baseline_scene_sha256") and not decision.get("accepted"):
            return decision
        raise ValueError("rect-fill identity drift")
    if current_hash != prior_final:
        raise ValueError("rect-fill input is not flat-panel final scene")
    scene = read_json(output / "scene.json")
    proposals = measure(scene, reference_path, output / "preview.png")
    boxes = [row["bbox"] for row in proposals]
    region = ([min(box[0] for box in boxes), min(box[1] for box in boxes),
               max(box[0] + box[2] for box in boxes) - min(box[0] for box in boxes),
               max(box[1] + box[3] for box in boxes) - min(box[1] for box in boxes)]
              if boxes else [0, 0, scene["canvas"]["width"], scene["canvas"]["height"]])
    result: dict[str, Any] = {
        "owner_stage": "representation", "mechanism": "source-measured-native-rectangle-fill-registration",
        "selected_candidate_id": "joint-source-mode-rect-fill-candidate",
        "affected_label_ids": sorted({row["source_label_id"] for row in proposals}),
        "affected_primitive_ids": [row["id"] for row in proposals],
        "field": "fill", "evaluation_region": region,
        "evaluation_region_source": "affected_scene_owners" if boxes else "full_canvas_no_candidate",
        "proposals": proposals, "accepted": False,
        "baseline_scene_sha256": current_hash,
        "baseline_pptx_sha256": sha256(output / "editable.pptx"),
    }
    evidence.mkdir(exist_ok=False)
    baseline_dir = evidence / "baseline"
    baseline_dir.mkdir()
    for name in ARTIFACTS:
        if (output / name).is_file():
            shutil.copy2(output / name, baseline_dir / name)
    if proposals:
        candidate_dir = evidence / "candidate"
        candidate_dir.mkdir()
        candidate_scene = apply(scene, proposals)
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
        accepted = (report["status"] == "render_success" and candidate_fallbacks == baseline_fallbacks
                    and after["pixel_similarity"] > before["pixel_similarity"]
                    and (after["edge_iou"] or 0) >= (before["edge_iou"] or 0))
        result.update({
            "assessment": assessment, "compiler_status": report["status"],
            "baseline_fallbacks": baseline_fallbacks,
            "candidate_fallbacks": candidate_fallbacks,
            "candidate_scene_sha256": sha256(candidate_dir / "scene.json"),
            "candidate_pptx_sha256": sha256(candidate_dir / "editable.pptx"),
            "accepted": accepted,
        })
        if accepted:
            _adopt_candidate(candidate_dir, output)
    result["status"] = "accepted" if result["accepted"] else "rejected" if proposals else "unchanged"
    result["final_scene_sha256"] = sha256(output / "scene.json")
    result["final_pptx_sha256"] = sha256(output / "editable.pptx")
    write_json(evidence / "decision.json", result)
    return result
