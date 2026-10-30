"""Atomically calibrate dark source shadows and post-tracking native text alignment."""
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
from .text_registration import ARTIFACTS, _adopt_candidate, propose


MAX_SHIFT = 6
MIN_ABSOLUTE_GAIN = 1.0
MIN_RELATIVE_GAIN = .03
SHADOW_OFFSET_DELTAS = (2, 4, 6)
MIN_SHADOW_DARKNESS_GAP = 20.0


def shadow_owners(scene: dict[str, Any], reference_path: Path, preview_path: Path) -> list[dict[str, Any]]:
    with Image.open(reference_path) as image:
        source = np.asarray(image.convert("RGB"), dtype=np.float32)
    with Image.open(preview_path) as image:
        rendered = np.asarray(image.convert("RGB"), dtype=np.float32)
    if source.shape != rendered.shape:
        raise ValueError("source and preview dimensions differ")
    height, width = source.shape[:2]
    rows: list[dict[str, Any]] = []
    for element in scene["elements"]:
        if element["kind"] != "rect":
            continue
        shadow = element.get("style", {}).get("shadow") or {}
        x, y, box_width, box_height = element["bbox"]
        if (shadow.get("type") != "outer" or box_height < height * .4 or box_width < width * .1
                or x + box_width + 16 >= width or y + box_height + 16 >= height):
            continue
        left, top, right, bottom = round(x), round(y), round(x + box_width), round(y + box_height)
        right_band = (slice(top + 100, bottom - 100), slice(right + 2, right + 16))
        bottom_band = (slice(bottom + 2, bottom + 16), slice(left + 100, right - 100))
        if (right_band[0].start >= right_band[0].stop
                or bottom_band[1].start >= bottom_band[1].stop):
            continue
        right_gap = float(rendered[right_band].mean() - source[right_band].mean())
        bottom_gap = float(rendered[bottom_band].mean() - source[bottom_band].mean())
        if max(right_gap, bottom_gap) < MIN_SHADOW_DARKNESS_GAP:
            continue
        rows.append({
            "id": element["id"], "source_label_id": element["source_label_id"],
            "bbox": element["bbox"][:], "shadow": copy.deepcopy(shadow),
            "right_luminance_gap": round(right_gap, 4),
            "bottom_luminance_gap": round(bottom_gap, 4),
        })
    return rows


def apply_shadow(scene: dict[str, Any], owners: list[dict[str, Any]], offset_delta: int) -> dict[str, Any]:
    if offset_delta not in SHADOW_OFFSET_DELTAS:
        raise ValueError("unsupported shadow offset delta")
    candidate = copy.deepcopy(scene)
    by_id = {element["id"]: element for element in candidate["elements"]}
    for row in owners:
        element = by_id.get(row["id"])
        if (element is None or element["kind"] != "rect"
                or element["source_label_id"] != row["source_label_id"]
                or element["bbox"] != row["bbox"]
                or element["style"].get("shadow") != row["shadow"]):
            raise ValueError("shadow-owner identity drift")
        new_shadow = copy.deepcopy(row["shadow"])
        new_shadow["color"] = "000000"
        new_shadow["opacity"] = .35
        new_shadow["offset"] = float(round(float(row["shadow"].get("offset", 0)) + offset_delta))
        element["style"]["shadow"] = new_shadow
    return candidate


def text_proposal(scene: dict[str, Any], reference_path: Path, preview_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    return propose(scene, reference_path, preview_path,
                   max_shift=MAX_SHIFT, min_absolute_gain=MIN_ABSOLUTE_GAIN,
                   min_relative_gain=MIN_RELATIVE_GAIN)


def run(root: Path, reference_path: Path, config: dict[str, Any]) -> dict[str, Any]:
    output = root / "output"
    evidence = root / "joint-shadow-text-registration"
    prior_path = root / "text-tracking-registration" / "decision.json"
    if not prior_path.is_file():
        raise ValueError("joint calibration requires text-tracking evidence")
    prior_final = read_json(prior_path)["final_scene_sha256"]
    current_hash = sha256(output / "scene.json")
    if evidence.exists():
        decision_path = evidence / "decision.json"
        if not decision_path.is_file():
            raise ValueError("incomplete joint-calibration evidence; inspect before resuming")
        decision = read_json(decision_path)
        if current_hash == decision.get("final_scene_sha256"):
            return decision
        if current_hash == decision.get("baseline_scene_sha256") and decision.get("accepted"):
            _adopt_candidate(evidence / str(decision["selected_candidate_id"]), output)
            if (sha256(output / "scene.json") != decision.get("final_scene_sha256")
                    or sha256(output / "editable.pptx") != decision.get("final_pptx_sha256")):
                raise ValueError("reused joint candidate hash mismatch")
            return decision
        if current_hash == decision.get("baseline_scene_sha256") and not decision.get("accepted"):
            return decision
        raise ValueError("joint-calibration identity drift")
    if current_hash != prior_final:
        raise ValueError("joint calibration input is not text-tracking final scene")
    baseline_scene = read_json(output / "scene.json")
    owners = shadow_owners(baseline_scene, reference_path, output / "preview.png")
    result: dict[str, Any] = {
        "owner_stage": "layout", "mechanism": "source-dark-shadow-and-native-text-joint-calibration",
        "selected_candidate_id": None, "affected_label_ids": [], "affected_primitive_ids": [],
        "field": ["style.shadow", "bbox"], "evaluation_region": [],
        "evaluation_region_source": "full_canvas_no_candidate",
        "shadow_owners": owners, "shadow_offset_deltas": list(SHADOW_OFFSET_DELTAS),
        "max_shift": MAX_SHIFT, "min_absolute_gain": MIN_ABSOLUTE_GAIN,
        "min_relative_gain": MIN_RELATIVE_GAIN, "attempts": [], "accepted": False,
        "baseline_scene_sha256": current_hash,
        "baseline_pptx_sha256": sha256(output / "editable.pptx"),
    }
    evidence.mkdir(exist_ok=False)
    baseline_dir = evidence / "baseline"
    baseline_dir.mkdir()
    for name in ARTIFACTS:
        if (output / name).is_file():
            shutil.copy2(output / name, baseline_dir / name)
    variants = [("text-only", None)] + [(f"shadow-plus-text-{delta}", delta) for delta in SHADOW_OFFSET_DELTAS if owners]
    affected_ids = {row["id"] for row in owners}
    baseline_by_id = {element["id"]: element for element in baseline_scene["elements"]}
    for candidate_id, offset_delta in variants:
        intermediate_dir = None
        if offset_delta is None:
            intermediate_scene = baseline_scene
            intermediate_preview = output / "preview.png"
        else:
            intermediate_dir = evidence / f"shadow-{offset_delta}"
            intermediate_dir.mkdir()
            intermediate_scene = apply_shadow(baseline_scene, owners, offset_delta)
            write_json(intermediate_dir / "scene.json", intermediate_scene)
            shadow_report = compile_scene(intermediate_dir / "scene.json", intermediate_dir, config, True)
            build_provenance(intermediate_scene, intermediate_dir / "editable.pptx", intermediate_dir)
            if shadow_report["status"] != "render_success":
                raise ValueError("joint shadow probe did not render")
            intermediate_preview = intermediate_dir / "preview.png"
        candidate_scene, proposals = text_proposal(intermediate_scene, reference_path, intermediate_preview)
        affected_ids.update(item["id"] for item in proposals)
        attempt_boxes = [baseline_by_id[item_id]["bbox"] for item_id in sorted(affected_ids)]
        attempt_region = ([min(box[0] for box in attempt_boxes), min(box[1] for box in attempt_boxes),
                           max(box[0] + box[2] for box in attempt_boxes) - min(box[0] for box in attempt_boxes),
                           max(box[1] + box[3] for box in attempt_boxes) - min(box[1] for box in attempt_boxes)]
                          if attempt_boxes else [0, 0, baseline_scene["canvas"]["width"], baseline_scene["canvas"]["height"]])
        if not proposals and offset_delta is None:
            continue
        candidate_dir = evidence / candidate_id
        candidate_dir.mkdir()
        write_json(candidate_dir / "scene.json", candidate_scene)
        report = compile_scene(candidate_dir / "scene.json", candidate_dir, config, True)
        build_provenance(candidate_scene, candidate_dir / "editable.pptx", candidate_dir)
        assessment = compare(reference_path, output / "preview.png", candidate_dir / "preview.png",
                             attempt_region)
        before, after = assessment["before_global"], assessment["after_global"]
        baseline_fallbacks = read_json(output / "report.json")["compiler"].get("fallbacks", [])
        candidate_fallbacks = report["compiler"].get("fallbacks", [])
        qualified = (report["status"] == "render_success" and candidate_fallbacks == baseline_fallbacks
                     and after["pixel_similarity"] > before["pixel_similarity"]
                     and (after["edge_iou"] or 0) >= (before["edge_iou"] or 0))
        result["attempts"].append({
            "candidate_id": candidate_id, "shadow_offset_delta": offset_delta,
            "shadow_scene_sha256": sha256(intermediate_dir / "scene.json") if intermediate_dir else None,
            "shadow_pptx_sha256": sha256(intermediate_dir / "editable.pptx") if intermediate_dir else None,
            "text_proposals": proposals, "assessment": assessment,
            "evaluation_region": attempt_region,
            "evaluation_region_source": "affected_scene_owners" if attempt_boxes else "full_canvas_no_candidate",
            "compiler_status": report["status"], "baseline_fallbacks": baseline_fallbacks,
            "candidate_fallbacks": candidate_fallbacks, "qualified": qualified,
            "candidate_scene_sha256": sha256(candidate_dir / "scene.json"),
            "candidate_pptx_sha256": sha256(candidate_dir / "editable.pptx"),
        })
    affected = [baseline_by_id[item_id] for item_id in sorted(affected_ids)]
    boxes = [item["bbox"] for item in affected]
    result["affected_primitive_ids"] = sorted(affected_ids)
    result["affected_label_ids"] = sorted({item["source_label_id"] for item in affected})
    result["evaluation_region"] = ([min(box[0] for box in boxes), min(box[1] for box in boxes),
                                    max(box[0] + box[2] for box in boxes) - min(box[0] for box in boxes),
                                    max(box[1] + box[3] for box in boxes) - min(box[1] for box in boxes)]
                                   if boxes else [0, 0, baseline_scene["canvas"]["width"], baseline_scene["canvas"]["height"]])
    result["evaluation_region_source"] = "affected_scene_owners" if boxes else "full_canvas_no_candidate"
    qualified_rows = [item for item in result["attempts"] if item["qualified"]]
    if qualified_rows:
        selected = max(qualified_rows, key=lambda item: (
            item["assessment"]["after_global"]["pixel_similarity"],
            item["assessment"]["after_global"]["edge_iou"] or 0,
        ))
        result["selected_candidate_id"] = selected["candidate_id"]
        _adopt_candidate(evidence / selected["candidate_id"], output)
        result["accepted"] = True
    result["status"] = "accepted" if result["accepted"] else "rejected" if result["attempts"] else "unchanged"
    result["final_scene_sha256"] = sha256(output / "scene.json")
    result["final_pptx_sha256"] = sha256(output / "editable.pptx")
    write_json(evidence / "decision.json", result)
    return result
