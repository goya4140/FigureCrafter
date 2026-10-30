"""Source-measured proposals for splitting multiline native text into line shapes.

This module only proposes geometry. A fresh PPTX compile/render and independent
quality gate must decide whether any proposal replaces the current scene.
"""
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
from .quality import boxes_region, compare
from .text_registration import ARTIFACTS, _adopt_candidate


def split_runs(element: dict[str, Any]) -> list[list[dict[str, Any]]]:
    text = str(element.get("text") or "")
    runs = element.get("runs") or [{
        "text": text, "source_text": text,
        "style": element.get("style", {}), "script": "normal",
    }]
    if any(str(run.get("source_text", run.get("text", ""))) != str(run.get("text", "")) for run in runs):
        raise ValueError("line splitting requires identical visible and source run text")
    lines: list[list[dict[str, Any]]] = [[]]
    for run in runs:
        for index, segment in enumerate(str(run.get("text") or "").split("\n")):
            if index:
                lines.append([])
            if segment:
                piece = copy.deepcopy(run)
                piece["text"] = segment
                piece["source_text"] = segment
                lines[-1].append(piece)
    if ["".join(run["text"] for run in line) for line in lines] != text.split("\n"):
        raise ValueError("split styled runs did not preserve exact source lines")
    return lines


def _ink_rows(image: np.ndarray, bbox: list[float], expected_lines: int) -> list[int]:
    height, width = image.shape[:2]
    x, y, box_width, box_height = bbox
    left = max(0, round(x + 10))
    right = min(width, round(x + box_width - 10))
    top = max(0, round(y - 8))
    bottom = min(height, round(y + box_height + 8))
    if right <= left or bottom <= top:
        return []
    crop = image[top:bottom, left:right]
    # Conservative dark-ink detector: ambiguous colored/light text is skipped.
    count = (crop.mean(axis=2) < 160).sum(axis=1)
    rows = np.flatnonzero(count > max(8, box_width * .03)) + top
    groups: list[list[int]] = []
    for row in rows:
        if not groups or row > groups[-1][-1] + 1:
            groups.append([int(row)])
        else:
            groups[-1].append(int(row))
    starts = [group[0] for group in groups if len(group) >= 8 and y - 5 <= group[0] <= y + box_height - 5]
    return starts if len(starts) == expected_lines else []


def propose(scene: dict[str, Any], reference_path: Path, preview_path: Path) -> list[dict[str, Any]]:
    """Return source-backed, bounded candidates; no scene mutation or acceptance."""
    with Image.open(reference_path) as image:
        reference = np.asarray(image.convert("RGB"))
    with Image.open(preview_path) as image:
        preview = np.asarray(image.convert("RGB"))
    if reference.shape != preview.shape:
        raise ValueError("source and preview dimensions differ")
    proposals = []
    for element in scene.get("elements", []):
        if element.get("kind") != "text" or "\n" not in str(element.get("text") or ""):
            continue
        if element.get("source_primitive_id") or element.get("rotation_deg", 0) not in (0, 0.0):
            continue
        text = element["text"]
        line_count = len(text.split("\n"))
        if not 3 <= line_count <= 12 or any(not line.strip() for line in text.split("\n")):
            continue
        bbox = element.get("bbox", [])
        if len(bbox) != 4 or bbox[2] < 120 or bbox[3] < 40:
            continue
        try:
            split_runs(element)
        except ValueError:
            continue
        source_rows = _ink_rows(reference, bbox, line_count)
        rendered_rows = _ink_rows(preview, bbox, line_count)
        if not source_rows or not rendered_rows:
            continue
        drift = [rendered - source for source, rendered in zip(source_rows, rendered_rows)]
        if max(abs(value) for value in drift) < 5:
            continue
        font_size = float(element.get("style", {}).get("font_size") or 0)
        if font_size < 10 or font_size > 80:
            continue
        source_gaps = np.diff(source_rows)
        first_top = source_rows[0] - 4
        line_advance = min(font_size + 4, float(np.median(source_gaps)))
        line_height = font_size + 15
        if first_top < 0 or first_top + (line_count - 1) * line_advance + line_height > reference.shape[0]:
            continue
        proposals.append({
            "id": element["id"], "source_label_id": element["source_label_id"],
            "source_rows": source_rows, "rendered_rows": rendered_rows, "row_drift": drift,
            "first_top": round(first_top, 4),
            "line_advance": round(line_advance, 4),
            "line_height": round(line_height, 4),
            "line_count": line_count,
        })
    return sorted(proposals, key=lambda item: (-max(abs(value) for value in item["row_drift"]), item["id"]))


def apply(scene: dict[str, Any], proposal: dict[str, Any]) -> dict[str, Any]:
    candidate = copy.deepcopy(scene)
    target = next((element for element in candidate["elements"] if element["id"] == proposal["id"]), None)
    if target is None or target.get("kind") != "text":
        raise ValueError("proposal target is not a native text shape")
    lines = split_runs(target)
    if len(lines) != proposal["line_count"]:
        raise ValueError("source line count changed")
    replacements = []
    for index, runs in enumerate(lines):
        element = copy.deepcopy(target)
        element["id"] = f"{target['id']}/line-{index + 1}"
        element["runs"] = runs
        element["text"] = "".join(run["text"] for run in runs)
        element["source_primitive_id"] = target["id"]
        element["source_line_index"] = index
        element["source_line_count"] = len(lines)
        element["line_break_after"] = index < len(lines) - 1
        element["bbox"] = [
            target["bbox"][0],
            proposal["first_top"] + index * proposal["line_advance"],
            target["bbox"][2], proposal["line_height"],
        ]
        replacements.append(element)
    position = candidate["elements"].index(target)
    candidate["elements"][position:position + 1] = replacements
    z_position = candidate["z_order"].index(target["id"])
    candidate["z_order"][z_position:z_position + 1] = [element["id"] for element in replacements]
    return candidate


def replacement_boxes(before_scene: dict[str, Any], after_scene: dict[str, Any],
                      proposal: dict[str, Any]) -> list[list[float]]:
    old_box = next(item["bbox"] for item in before_scene["elements"] if item["id"] == proposal["id"])
    new_boxes = [item["bbox"] for item in after_scene["elements"]
                 if item.get("source_primitive_id") == proposal["id"]]
    if len(new_boxes) != proposal["line_count"]:
        raise ValueError("source-line replacement identity drift")
    return [old_box] + new_boxes


def run(root: Path, reference_path: Path, config: dict[str, Any]) -> dict[str, Any]:
    """Measure each proposal on a fresh PPTX and retain only joint global gains."""
    output = root / "output"
    evidence = root / "source-line-registration"
    prior_path = root / "text-registration-pass-2" / "decision.json"
    if not prior_path.is_file():
        raise ValueError("source-line registration requires second text-registration evidence")
    prior = read_json(prior_path)
    prior_final = prior.get("final_scene_sha256") if prior.get("accepted") else prior.get("baseline_scene_sha256")
    current_hash = sha256(output / "scene.json")
    if evidence.exists():
        decision_path = evidence / "decision.json"
        if not decision_path.is_file():
            raise ValueError("incomplete source-line registration evidence; inspect before resuming")
        decision = read_json(decision_path)
        if current_hash == decision.get("final_scene_sha256"):
            return decision
        if current_hash == decision.get("baseline_scene_sha256") and decision.get("accepted"):
            _adopt_candidate(evidence / "final", output)
            if (sha256(output / "scene.json") != decision.get("final_scene_sha256")
                    or sha256(output / "editable.pptx") != decision.get("final_pptx_sha256")):
                raise ValueError("reused source-line candidate hash mismatch")
            return decision
        if current_hash == decision.get("baseline_scene_sha256") and not decision.get("accepted"):
            return decision
        raise ValueError("source-line registration identity drift")
    if current_hash != prior_final:
        raise ValueError("source-line registration input is not second-pass final scene")
    scene = read_json(output / "scene.json")
    proposals = propose(scene, reference_path, output / "preview.png")
    boxes = [next(item["bbox"] for item in scene["elements"] if item["id"] == proposal["id"]) for proposal in proposals]
    region_boxes = boxes[:]
    region = boxes_region(region_boxes, scene["canvas"])
    result: dict[str, Any] = {
        "owner_stage": "layout", "mechanism": "source-measured-multiline-native-text-registration",
        "selected_candidate_id": "deterministic-source-line-registration",
        "affected_label_ids": sorted({item["source_label_id"] for item in proposals}),
        "affected_primitive_ids": [item["id"] for item in proposals],
        "field": "multiline_text_geometry", "evaluation_region": region,
        "evaluation_region_source": "affected_scene_owners" if boxes else "full_canvas_no_candidate",
        "baseline_scene_sha256": current_hash,
        "baseline_pptx_sha256": sha256(output / "editable.pptx"),
        "attempts": [], "accepted": False,
    }
    evidence.mkdir(exist_ok=False)
    for index, proposal in enumerate(proposals, 1):
        attempt_dir = evidence / f"attempt-{index:03d}"
        attempt_dir.mkdir()
        baseline_dir = attempt_dir / "baseline"
        baseline_dir.mkdir()
        for name in ARTIFACTS:
            if (output / name).is_file():
                shutil.copy2(output / name, baseline_dir / name)
        baseline_scene = read_json(output / "scene.json")
        candidate_scene = apply(baseline_scene, proposal)
        attempt_boxes = replacement_boxes(baseline_scene, candidate_scene, proposal)
        attempt_region = boxes_region(attempt_boxes, scene["canvas"])
        region_boxes.extend(attempt_boxes)
        candidate_dir = attempt_dir / "candidate"
        candidate_dir.mkdir()
        write_json(candidate_dir / "scene.json", candidate_scene)
        report = compile_scene(candidate_dir / "scene.json", candidate_dir, config, True)
        build_provenance(candidate_scene, candidate_dir / "editable.pptx", candidate_dir)
        assessment = compare(reference_path, output / "preview.png", candidate_dir / "preview.png",
                             attempt_region)
        before, after = assessment["before_global"], assessment["after_global"]
        baseline_fallbacks = read_json(output / "report.json")["compiler"].get("fallbacks", [])
        candidate_fallbacks = report["compiler"].get("fallbacks", [])
        accepted = (report["status"] == "render_success" and candidate_fallbacks == baseline_fallbacks
                    and after["pixel_similarity"] > before["pixel_similarity"]
                    and (after["edge_iou"] or 0) >= (before["edge_iou"] or 0))
        attempt = {"proposal": proposal, "accepted": accepted, "assessment": assessment,
                   "evaluation_region": attempt_region,
                   "evaluation_region_source": "affected_scene_owners",
                   "baseline_scene_sha256": sha256(baseline_dir / "scene.json"),
                   "baseline_pptx_sha256": sha256(baseline_dir / "editable.pptx"),
                   "candidate_scene_sha256": sha256(candidate_dir / "scene.json"),
                   "candidate_pptx_sha256": sha256(candidate_dir / "editable.pptx"),
                   "baseline_fallbacks": baseline_fallbacks, "candidate_fallbacks": candidate_fallbacks,
                   "compiler_status": report["status"]}
        result["attempts"].append(attempt)
        if accepted:
            _adopt_candidate(candidate_dir, output)
            result["accepted"] = True
    result["evaluation_region"] = boxes_region(region_boxes, scene["canvas"])
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
