"""Source-color registration proposals for native styled multiline text."""
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
from .quality import affected_scene_region, compare
from .text_registration import ARTIFACTS, _adopt_candidate


MAX_SHIFT = 16


def _colored_mask(pixels: np.ndarray, colors: list[np.ndarray]) -> np.ndarray:
    mask = np.zeros(pixels.shape[:2], dtype=bool)
    for color in colors:
        distance = np.linalg.norm(pixels - color, axis=2)
        mask |= (distance < 65) & (np.max(pixels, axis=2) - np.min(pixels, axis=2) > 35)
    return mask


def propose(scene: dict[str, Any], reference_path: Path, preview_path: Path) -> list[dict[str, Any]]:
    with Image.open(reference_path) as image:
        source = np.asarray(image.convert("RGB"), dtype=np.int16)
    with Image.open(preview_path) as image:
        preview = np.asarray(image.convert("RGB"), dtype=np.int16)
    if source.shape != preview.shape:
        raise ValueError("source and preview dimensions differ")
    canvas_height, canvas_width = source.shape[:2]
    proposals = []
    for element in scene["elements"]:
        if (element["kind"] != "text" or "\n" not in str(element.get("text") or "")
                or len(element.get("runs") or []) < 2):
            continue
        colors = []
        for run in element["runs"]:
            value = str(run.get("style", {}).get("color") or "")
            if len(value) != 7 or not value.startswith("#"):
                continue
            rgb = np.array([int(value[i:i + 2], 16) for i in (1, 3, 5)], dtype=np.int16)
            if np.max(rgb) - np.min(rgb) > 50:
                colors.append(rgb)
        if not colors:
            continue
        x, y, width, height = (round(value) for value in element["bbox"])
        x0, y0 = max(MAX_SHIFT, x - 12), max(MAX_SHIFT, y - 10)
        x1 = min(canvas_width - MAX_SHIFT, x + width + 12)
        y1 = min(canvas_height - MAX_SHIFT, y + height + 10)
        if x1 <= x0 or y1 <= y0:
            continue
        source_mask = _colored_mask(source[y0:y1, x0:x1], colors)
        if int(source_mask.sum()) < 200:
            continue
        preview_mask = _colored_mask(
            preview[y0 - MAX_SHIFT:y1 + MAX_SHIFT, x0 - MAX_SHIFT:x1 + MAX_SHIFT], colors,
        )
        crop_height, crop_width = source_mask.shape
        scored = []
        for dy in range(-MAX_SHIFT, MAX_SHIFT + 1):
            for dx in range(-MAX_SHIFT, MAX_SHIFT + 1):
                start_y, start_x = dy + MAX_SHIFT, dx + MAX_SHIFT
                rendered = preview_mask[start_y:start_y + crop_height, start_x:start_x + crop_width]
                intersection = int((source_mask & rendered).sum())
                union = int((source_mask | rendered).sum())
                score = intersection / union if union else 0.0
                scored.append((score, -(abs(dx) + abs(dy)), -abs(dy), -abs(dx), dx, dy))
        best_iou, _, _, _, dx, dy = max(scored)
        baseline = next(row[0] for row in scored if row[-2:] == (0, 0))
        shift_x, shift_y = -dx, -dy
        if (max(abs(shift_x), abs(shift_y)) < 2 or best_iou - baseline < .02
                or x + shift_x < 0 or y + shift_y < 0
                or x + shift_x + width > canvas_width or y + shift_y + height > canvas_height):
            continue
        proposals.append({
            "id": element["id"], "source_label_id": element["source_label_id"],
            "old_bbox": element["bbox"][:], "shift_x": shift_x, "shift_y": shift_y,
            "new_bbox": [element["bbox"][0] + shift_x, element["bbox"][1] + shift_y,
                         element["bbox"][2], element["bbox"][3]],
            "source_color_pixels": int(source_mask.sum()),
            "baseline_color_iou": round(baseline, 6),
            "projected_color_iou": round(best_iou, 6),
        })
    return proposals


def apply(scene: dict[str, Any], proposals: list[dict[str, Any]]) -> dict[str, Any]:
    candidate = copy.deepcopy(scene)
    by_id = {element["id"]: element for element in candidate["elements"]}
    for row in proposals:
        element = by_id.get(row["id"])
        if (element is None or element["kind"] != "text"
                or element["source_label_id"] != row["source_label_id"]
                or element["bbox"] != row["old_bbox"]):
            raise ValueError("styled multiline proposal identity drift")
        element["bbox"] = row["new_bbox"][:]
    return candidate


def run(root: Path, reference_path: Path, config: dict[str, Any]) -> dict[str, Any]:
    output = root / "output"
    evidence = root / "styled-multiline-registration"
    prior_path = root / "rect-fill-registration" / "decision.json"
    if not prior_path.is_file():
        raise ValueError("styled multiline registration requires rect-fill evidence")
    prior_final = read_json(prior_path)["final_scene_sha256"]
    current_hash = sha256(output / "scene.json")
    if evidence.exists():
        decision_path = evidence / "decision.json"
        if not decision_path.is_file():
            raise ValueError("incomplete styled-multiline evidence; inspect before resuming")
        decision = read_json(decision_path)
        if current_hash == decision.get("final_scene_sha256"):
            return decision
        if current_hash == decision.get("baseline_scene_sha256") and decision.get("accepted"):
            _adopt_candidate(evidence / "candidate", output)
            if (sha256(output / "scene.json") != decision.get("final_scene_sha256")
                    or sha256(output / "editable.pptx") != decision.get("final_pptx_sha256")):
                raise ValueError("reused styled-multiline candidate hash mismatch")
            return decision
        if current_hash == decision.get("baseline_scene_sha256") and not decision.get("accepted"):
            return decision
        raise ValueError("styled-multiline identity drift")
    if current_hash != prior_final:
        raise ValueError("styled-multiline input is not rect-fill final scene")
    scene = read_json(output / "scene.json")
    proposals = propose(scene, reference_path, output / "preview.png")
    candidate_scene = apply(scene, proposals) if proposals else scene
    region = affected_scene_region(scene, candidate_scene, [row["id"] for row in proposals])
    result: dict[str, Any] = {
        "owner_stage": "layout", "mechanism": "source-color-styled-multiline-text-registration",
        "selected_candidate_id": "joint-source-color-multiline-candidate",
        "affected_label_ids": sorted({row["source_label_id"] for row in proposals}),
        "affected_primitive_ids": [row["id"] for row in proposals],
        "field": "bbox", "evaluation_region": region,
        "evaluation_region_source": "affected_scene_owners" if proposals else "full_canvas_no_candidate",
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
