"""Source-measured recovery of native text shrunken by short text boxes."""
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


def _ink_mask(pixels: np.ndarray, color: str) -> np.ndarray:
    if color == "#000000":
        return np.max(pixels, axis=2) < 125
    rgb = np.array([int(color[index:index + 2], 16) for index in (1, 3, 5)], dtype=np.int16)
    distance = np.linalg.norm(pixels - rgb, axis=2)
    return (distance < 75) & (np.linalg.norm(pixels - 255, axis=2) > 80)


def detect(scene: dict[str, Any], reference_path: Path, preview_path: Path) -> list[dict[str, Any]]:
    with Image.open(reference_path) as image:
        reference = np.asarray(image.convert("RGB"), dtype=np.int16)
    with Image.open(preview_path) as image:
        preview = np.asarray(image.convert("RGB"), dtype=np.int16)
    if reference.shape != preview.shape:
        raise ValueError("source and preview dimensions differ")
    proposals = []
    height, width = reference.shape[:2]
    for element in scene["elements"]:
        if element["kind"] != "text" or element.get("source_primitive_id"):
            continue
        bbox = element["bbox"]
        font = float(element["style"].get("font_size") or 0)
        color = str(element["style"].get("color") or "")
        if (font < 10 or font > 100 or bbox[3] / font >= .95
                or len(color) != 7 or not color.startswith("#")
                or not all(char in "0123456789ABCDEFabcdef" for char in color[1:])
                or not str(element.get("text") or "").strip()
                or "\n" in element["text"]):
            continue
        x, y, box_width, box_height = bbox
        x0, y0 = max(0, round(x + 3)), max(0, round(y - 3))
        x1, y1 = min(width, round(x + box_width - 3)), min(height, round(y + box_height + 3))
        if x1 <= x0 or y1 <= y0:
            continue
        source_ink = int(_ink_mask(reference[y0:y1, x0:x1], color).sum())
        render_ink = int(_ink_mask(preview[y0:y1, x0:x1], color).sum())
        ratio = render_ink / max(1, source_ink)
        if source_ink < 100 or ratio >= .4:
            continue
        new_height = round(font * 1.5, 4)
        if y + new_height > height:
            continue
        proposals.append({
            "id": element["id"], "source_label_id": element["source_label_id"],
            "old_bbox": bbox[:], "new_height": new_height,
            "font_size": font, "color": color,
            "source_ink_pixels": source_ink, "render_ink_pixels": render_ink,
            "ink_ratio": round(ratio, 6),
        })
    return proposals


def height_scene(scene: dict[str, Any], proposals: list[dict[str, Any]]) -> dict[str, Any]:
    candidate = copy.deepcopy(scene)
    by_id = {element["id"]: element for element in candidate["elements"]}
    for proposal in proposals:
        element = by_id[proposal["id"]]
        if element["kind"] != "text" or element["bbox"] != proposal["old_bbox"]:
            raise ValueError("short-text height proposal identity drift")
        element["bbox"][3] = proposal["new_height"]
    return candidate


def align(
    height_candidate: dict[str, Any], proposals: list[dict[str, Any]],
    reference_path: Path, height_preview_path: Path, *, max_shift: int = 12,
) -> list[dict[str, Any]]:
    with Image.open(reference_path) as image:
        reference = np.asarray(image.convert("RGB"), dtype=np.int16)
    with Image.open(height_preview_path) as image:
        preview = np.asarray(image.convert("RGB"), dtype=np.int16)
    if reference.shape != preview.shape:
        raise ValueError("source and height-preview dimensions differ")
    by_id = {element["id"]: element for element in height_candidate["elements"]}
    aligned = []
    canvas_height, canvas_width = reference.shape[:2]
    for proposal in proposals:
        element = by_id[proposal["id"]]
        x, y, box_width, box_height = element["bbox"]
        x0, y0 = max(max_shift, round(x - 12)), max(max_shift, round(y - 14))
        x1 = min(canvas_width - max_shift, round(x + box_width + 12))
        y1 = min(canvas_height - max_shift, round(y + box_height + 14))
        source_ink = _ink_mask(reference[y0:y1, x0:x1], proposal["color"])
        scored = []
        for dy in range(-max_shift, max_shift + 1):
            for dx in range(-max_shift, max_shift + 1):
                render_ink = _ink_mask(preview[y0 + dy:y1 + dy, x0 + dx:x1 + dx], proposal["color"])
                intersection = int((source_ink & render_ink).sum())
                union = int((source_ink | render_ink).sum())
                iou = intersection / union if union else 0.0
                scored.append((iou, -(abs(dx) + abs(dy)), -abs(dy), -abs(dx), dx, dy))
        score, _, _, _, dx, dy = max(scored)
        new_x, new_y = x - dx, y - dy
        if new_x < 0 or new_y < 0 or new_x + box_width > canvas_width or new_y + box_height > canvas_height:
            continue
        aligned.append({**proposal, "shift_x": -dx, "shift_y": -dy,
                        "new_bbox": [new_x, new_y, box_width, box_height],
                        "projected_ink_iou": round(score, 6)})
    return aligned


def apply(scene: dict[str, Any], proposal: dict[str, Any]) -> dict[str, Any]:
    candidate = copy.deepcopy(scene)
    element = next((item for item in candidate["elements"] if item["id"] == proposal["id"]), None)
    if element is None or element["kind"] != "text" or element["bbox"] != proposal["old_bbox"]:
        raise ValueError("short-text candidate identity drift")
    element["bbox"] = proposal["new_bbox"][:]
    return candidate


def proposal_region(proposal: dict[str, Any]) -> list[float]:
    """Bound only the old and proposed owner boxes for one candidate attempt."""
    old, new = proposal["old_bbox"], proposal["new_bbox"]
    left, top = min(old[0], new[0]), min(old[1], new[1])
    right = max(old[0] + old[2], new[0] + new[2])
    bottom = max(old[1] + old[3], new[1] + new[3])
    return [left, top, right - left, bottom - top]


def run(root: Path, reference_path: Path, config: dict[str, Any]) -> dict[str, Any]:
    output = root / "output"
    evidence = root / "short-text-registration"
    prior_path = root / "native-shadow-registration" / "decision.json"
    if not prior_path.is_file():
        raise ValueError("short-text registration requires native-shadow evidence")
    prior_final = read_json(prior_path)["final_scene_sha256"]
    current_hash = sha256(output / "scene.json")
    if evidence.exists():
        decision_path = evidence / "decision.json"
        if not decision_path.is_file():
            raise ValueError("incomplete short-text evidence; inspect before resuming")
        decision = read_json(decision_path)
        if current_hash == decision.get("final_scene_sha256"):
            return decision
        if current_hash == decision.get("baseline_scene_sha256") and decision.get("accepted"):
            _adopt_candidate(evidence / "final", output)
            if (sha256(output / "scene.json") != decision.get("final_scene_sha256")
                    or sha256(output / "editable.pptx") != decision.get("final_pptx_sha256")):
                raise ValueError("reused short-text candidate hash mismatch")
            return decision
        if current_hash == decision.get("baseline_scene_sha256") and not decision.get("accepted"):
            return decision
        raise ValueError("short-text registration identity drift")
    if current_hash != prior_final:
        raise ValueError("short-text input is not native-shadow final scene")
    scene = read_json(output / "scene.json")
    proposals = detect(scene, reference_path, output / "preview.png")
    boxes = [proposal["old_bbox"] for proposal in proposals]
    if boxes:
        x1 = min(box[0] for box in boxes); y1 = min(box[1] for box in boxes)
        x2 = max(box[0] + box[2] for box in boxes); y2 = max(box[1] + box[3] for box in boxes)
        region = [x1, y1, x2 - x1, y2 - y1]
    else:
        region = [0, 0, scene["canvas"]["width"], scene["canvas"]["height"]]
    result: dict[str, Any] = {
        "owner_stage": "representation", "mechanism": "short-native-text-box-shrink-recovery",
        "selected_candidate_id": "deterministic-source-ink-registration",
        "affected_label_ids": sorted({proposal["source_label_id"] for proposal in proposals}),
        "affected_primitive_ids": [proposal["id"] for proposal in proposals],
        "field": "text_bbox", "evaluation_region": region,
        "evaluation_region_source": "affected_scene_owners" if boxes else "full_canvas_no_candidate",
        "detected": proposals, "attempts": [], "accepted": False,
        "baseline_scene_sha256": current_hash,
        "baseline_pptx_sha256": sha256(output / "editable.pptx"),
    }
    evidence.mkdir(exist_ok=False)
    if proposals:
        baseline_dir = evidence / "baseline"
        baseline_dir.mkdir()
        for name in ARTIFACTS:
            if (output / name).is_file():
                shutil.copy2(output / name, baseline_dir / name)
        height_candidate = height_scene(scene, proposals)
        height_dir = evidence / "height-probe"
        height_dir.mkdir()
        write_json(height_dir / "scene.json", height_candidate)
        height_report = compile_scene(height_dir / "scene.json", height_dir, config, True)
        build_provenance(height_candidate, height_dir / "editable.pptx", height_dir)
        if height_report["status"] != "render_success":
            raise ValueError("short-text height probe failed to render")
        result["height_probe_scene_sha256"] = sha256(height_dir / "scene.json")
        result["height_probe_pptx_sha256"] = sha256(height_dir / "editable.pptx")
        aligned = align(height_candidate, proposals, reference_path, height_dir / "preview.png")
    else:
        aligned = []
    result["aligned"] = aligned
    for index, proposal in enumerate(aligned, 1):
        attempt_dir = evidence / f"attempt-{index:03d}"
        attempt_dir.mkdir()
        baseline_dir = attempt_dir / "baseline"
        baseline_dir.mkdir()
        for name in ARTIFACTS:
            if (output / name).is_file():
                shutil.copy2(output / name, baseline_dir / name)
        candidate_scene = apply(read_json(output / "scene.json"), proposal)
        candidate_dir = attempt_dir / "candidate"
        candidate_dir.mkdir()
        write_json(candidate_dir / "scene.json", candidate_scene)
        report = compile_scene(candidate_dir / "scene.json", candidate_dir, config, True)
        build_provenance(candidate_scene, candidate_dir / "editable.pptx", candidate_dir)
        with Image.open(reference_path) as image:
            canvas = image.size
        attempt_region = proposal_region(proposal)
        assessment = compare(reference_path, output / "preview.png", candidate_dir / "preview.png",
                             attempt_region)
        before, after = assessment["before_global"], assessment["after_global"]
        baseline_fallbacks = read_json(output / "report.json")["compiler"].get("fallbacks", [])
        candidate_fallbacks = report["compiler"].get("fallbacks", [])
        accepted = (report["status"] == "render_success" and candidate_fallbacks == baseline_fallbacks
                    and after["pixel_similarity"] > before["pixel_similarity"]
                    and (after["edge_iou"] or 0) >= (before["edge_iou"] or 0))
        result["attempts"].append({
            "proposal": proposal, "accepted": accepted, "assessment": assessment,
            "evaluation_region": attempt_region,
            "evaluation_region_source": "affected_scene_owners",
            "baseline_scene_sha256": sha256(baseline_dir / "scene.json"),
            "baseline_pptx_sha256": sha256(baseline_dir / "editable.pptx"),
            "candidate_scene_sha256": sha256(candidate_dir / "scene.json"),
            "candidate_pptx_sha256": sha256(candidate_dir / "editable.pptx"),
            "baseline_fallbacks": baseline_fallbacks,
            "candidate_fallbacks": candidate_fallbacks, "compiler_status": report["status"],
        })
        if accepted:
            _adopt_candidate(candidate_dir, output)
            result["accepted"] = True
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
