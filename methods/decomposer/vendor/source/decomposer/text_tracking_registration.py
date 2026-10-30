"""Bounded native character-spacing calibration for long multiline text."""
from __future__ import annotations

import copy
from pathlib import Path
import shutil
from typing import Any

from PIL import Image

from .common import read_json, sha256, write_json
from .pptx import compile_scene
from .provenance import build as build_provenance
from .quality import compare
from .text_registration import ARTIFACTS, _adopt_candidate


TRACKING_CANDIDATES = (2.0, 3.0, 4.0, 5.0)


def eligible(scene: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for element in scene["elements"]:
        text = str(element.get("text") or "")
        if (element["kind"] != "text" or len(text) < 80 or text.count("\n") < 2
                or element.get("runs") or element["bbox"][2] < 300
                or element["style"].get("char_spacing") is not None):
            continue
        color = str(element["style"].get("color") or "")
        if color not in {"#000000", "#111111", "#222222", "#333333"}:
            continue
        rows.append({"id": element["id"], "source_label_id": element["source_label_id"],
                     "bbox": element["bbox"][:], "text": text})
    return rows


def apply(scene: dict[str, Any], rows: list[dict[str, Any]], spacing: float) -> dict[str, Any]:
    if spacing not in TRACKING_CANDIDATES:
        raise ValueError("unsupported tracking candidate")
    candidate = copy.deepcopy(scene)
    by_id = {element["id"]: element for element in candidate["elements"]}
    for row in rows:
        element = by_id.get(row["id"])
        if (element is None or element["kind"] != "text"
                or element["source_label_id"] != row["source_label_id"]
                or element["bbox"] != row["bbox"] or element["text"] != row["text"]
                or element["style"].get("char_spacing") is not None):
            raise ValueError("tracking candidate identity drift")
        element["style"]["char_spacing"] = spacing
    return candidate


def run(root: Path, reference_path: Path, config: dict[str, Any]) -> dict[str, Any]:
    output = root / "output"
    evidence = root / "text-tracking-registration"
    prior_path = root / "styled-multiline-registration" / "decision.json"
    if not prior_path.is_file():
        raise ValueError("text tracking requires styled-multiline evidence")
    prior_final = read_json(prior_path)["final_scene_sha256"]
    current_hash = sha256(output / "scene.json")
    if evidence.exists():
        decision_path = evidence / "decision.json"
        if not decision_path.is_file():
            raise ValueError("incomplete tracking evidence; inspect before resuming")
        decision = read_json(decision_path)
        if current_hash == decision.get("final_scene_sha256"):
            return decision
        if current_hash == decision.get("baseline_scene_sha256") and decision.get("accepted"):
            _adopt_candidate(evidence / str(decision["selected_candidate_id"]), output)
            if (sha256(output / "scene.json") != decision.get("final_scene_sha256")
                    or sha256(output / "editable.pptx") != decision.get("final_pptx_sha256")):
                raise ValueError("reused tracking candidate hash mismatch")
            return decision
        if current_hash == decision.get("baseline_scene_sha256") and not decision.get("accepted"):
            return decision
        raise ValueError("text tracking identity drift")
    if current_hash != prior_final:
        raise ValueError("text tracking input is not styled-multiline final scene")
    scene = read_json(output / "scene.json")
    rows = eligible(scene)
    boxes = [row["bbox"] for row in rows]
    region = ([min(box[0] for box in boxes), min(box[1] for box in boxes),
               max(box[0] + box[2] for box in boxes) - min(box[0] for box in boxes),
               max(box[1] + box[3] for box in boxes) - min(box[1] for box in boxes)]
              if boxes else [0, 0, scene["canvas"]["width"], scene["canvas"]["height"]])
    result: dict[str, Any] = {
        "owner_stage": "representation", "mechanism": "native-long-multiline-character-spacing-calibration",
        "selected_candidate_id": None,
        "affected_label_ids": sorted({row["source_label_id"] for row in rows}),
        "affected_primitive_ids": [row["id"] for row in rows],
        "field": "style.char_spacing", "evaluation_region": region,
        "evaluation_region_source": "affected_scene_owners" if boxes else "full_canvas_no_candidate",
        "eligible": rows, "candidates": list(TRACKING_CANDIDATES),
        "attempts": [], "accepted": False,
        "baseline_scene_sha256": current_hash,
        "baseline_pptx_sha256": sha256(output / "editable.pptx"),
    }
    evidence.mkdir(exist_ok=False)
    baseline_dir = evidence / "baseline"
    baseline_dir.mkdir()
    for name in ARTIFACTS:
        if (output / name).is_file():
            shutil.copy2(output / name, baseline_dir / name)
    for spacing in TRACKING_CANDIDATES if rows else ():
        candidate_id = f"tracking-{int(spacing)}"
        candidate_dir = evidence / candidate_id
        candidate_dir.mkdir()
        candidate_scene = apply(scene, rows, spacing)
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
        result["attempts"].append({
            "candidate_id": candidate_id, "spacing": spacing, "qualified": qualified,
            "assessment": assessment, "compiler_status": report["status"],
            "baseline_fallbacks": baseline_fallbacks,
            "candidate_fallbacks": candidate_fallbacks,
            "candidate_scene_sha256": sha256(candidate_dir / "scene.json"),
            "candidate_pptx_sha256": sha256(candidate_dir / "editable.pptx"),
        })
    qualified_rows = [row for row in result["attempts"] if row["qualified"]]
    if qualified_rows:
        chosen = max(qualified_rows, key=lambda row: (
            row["assessment"]["after_global"]["pixel_similarity"],
            row["assessment"]["after_global"]["edge_iou"] or 0,
        ))
        result["selected_candidate_id"] = chosen["candidate_id"]
        _adopt_candidate(evidence / chosen["candidate_id"], output)
        result["accepted"] = True
    result["status"] = "accepted" if result["accepted"] else "rejected" if rows else "unchanged"
    result["final_scene_sha256"] = sha256(output / "scene.json")
    result["final_pptx_sha256"] = sha256(output / "editable.pptx")
    write_json(evidence / "decision.json", result)
    return result
