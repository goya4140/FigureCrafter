"""Measured, bounded post-render registration of native editable text shapes.

The source figure is available at inference time.  A cheap crop search proposes
small translations, but only a fresh PPTX render may authorize a replacement.
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
from .quality import affected_scene_region, compare


ARTIFACTS = (
    "scene.json", "editable.pptx", "preview.png", "report.json",
    "compiler-report.json", "provenance.json", "id-mask.png", "label-index.json",
    "compile.log", "render.log",
)


def _adopt_candidate(candidate_dir: Path, output: Path) -> None:
    for name in ARTIFACTS:
        source = candidate_dir / name
        if source.is_file():
            shutil.copy2(source, output / name)
    report = read_json(output / "report.json")
    report["render"]["path"] = str(output / "preview.png")
    write_json(output / "report.json", report)
    provenance = read_json(output / "provenance.json")
    provenance["id_mask"] = str(output / "id-mask.png")
    write_json(output / "provenance.json", provenance)


def propose(
    scene: dict[str, Any], reference_path: Path, preview_path: Path,
    *, max_shift: int = 3, min_absolute_gain: float = 3.0,
    min_relative_gain: float = 0.12,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not 1 <= max_shift <= 6:
        raise ValueError("max_shift must be 1..6 source pixels")
    candidate = copy.deepcopy(scene)
    with Image.open(reference_path) as image:
        reference = np.asarray(image.convert("RGB"), dtype=np.int16)
    with Image.open(preview_path) as image:
        preview = np.asarray(image.convert("RGB"), dtype=np.int16)
    if reference.shape != preview.shape:
        raise ValueError("source and preview dimensions differ")
    proposals: list[dict[str, Any]] = []
    height, width = reference.shape[:2]
    for element in candidate["elements"]:
        if element["kind"] != "text" or not str(element.get("text") or "").strip():
            continue
        x, y, box_width, box_height = element["bbox"]
        if box_width < 20 or box_height < 15:
            continue
        inset = 2
        x0, y0 = max(max_shift, round(x + inset)), max(max_shift, round(y + inset))
        x1, y1 = min(width - max_shift, round(x + box_width - inset)), min(height - max_shift, round(y + box_height - inset))
        if x1 <= x0 or y1 <= y0:
            continue
        target = reference[y0:y1, x0:x1]
        baseline = float(np.abs(target - preview[y0:y1, x0:x1]).mean())
        if baseline < 10:
            continue
        scored = []
        for dy in range(-max_shift, max_shift + 1):
            for dx in range(-max_shift, max_shift + 1):
                error = float(np.abs(target - preview[y0 + dy:y1 + dy, x0 + dx:x1 + dx]).mean())
                scored.append((error, dx, dy))
        best_error, dx, dy = min(scored)
        gain = baseline - best_error
        if not (dx or dy) or gain < min_absolute_gain or gain / baseline < min_relative_gain:
            continue
        next_x, next_y = x - dx, y - dy
        if next_x < 0 or next_y < 0 or next_x + box_width > width or next_y + box_height > height:
            continue
        element["bbox"][0], element["bbox"][1] = next_x, next_y
        proposals.append({
            "id": element["id"], "source_label_id": element["source_label_id"],
            "old_bbox_left": x, "old_bbox_top": y,
            "new_bbox_left": next_x, "new_bbox_top": next_y,
            "baseline_crop_mae": round(baseline, 4),
            "projected_crop_mae": round(best_error, 4),
            "projected_absolute_gain": round(gain, 4),
        })
    return candidate, proposals


def run(root: Path, reference_path: Path, config: dict[str, Any], *, pass_number: int = 1) -> dict[str, Any]:
    if pass_number not in (1, 2):
        raise ValueError("text registration supports at most two measured passes")
    output = root / "output"
    evidence_dir = root / ("text-registration" if pass_number == 1 else "text-registration-pass-2")
    if pass_number == 2:
        first_decision_path = root / "text-registration" / "decision.json"
        if not first_decision_path.is_file():
            raise ValueError("second text-registration pass requires first-pass evidence")
        first_decision = read_json(first_decision_path)
        first_final_hash = (
            first_decision.get("final_scene_sha256") if first_decision.get("accepted")
            else first_decision.get("baseline_scene_sha256")
        )
        allowed_hashes = {first_final_hash}
        if (evidence_dir / "decision.json").is_file():
            existing = read_json(evidence_dir / "decision.json")
            if existing.get("accepted"):
                allowed_hashes.add(existing.get("final_scene_sha256"))
        if sha256(output / "scene.json") not in allowed_hashes:
            raise ValueError("text-registration pass-chain identity drift")
    if evidence_dir.exists():
        decision = read_json(evidence_dir / "decision.json")
        current_hash = sha256(output / "scene.json")
        if decision.get("accepted") and current_hash == decision.get("baseline_scene_sha256"):
            _adopt_candidate(evidence_dir / "candidate", output)
            if sha256(output / "scene.json") != decision.get("final_scene_sha256") or sha256(output / "editable.pptx") != decision.get("final_pptx_sha256"):
                raise ValueError("reused text-registration candidate hash mismatch")
            return decision
        if decision.get("accepted") and current_hash == decision.get("final_scene_sha256"):
            return decision
        if not decision.get("accepted") and current_hash == decision.get("baseline_scene_sha256"):
            return decision
        raise ValueError("text-registration identity drift; use a new versioned run")
    current_scene = read_json(output / "scene.json")
    candidate_scene, proposals = propose(current_scene, reference_path, output / "preview.png")
    affected_ids = sorted(item["id"] for item in proposals)
    affected_labels = sorted({item["source_label_id"] for item in proposals})
    region = affected_scene_region(current_scene, candidate_scene, affected_ids)
    result: dict[str, Any] = {
        "pass_number": pass_number,
        "owner_stage": "layout", "mechanism": "rendered-text-registration",
        "selected_candidate_id": "deterministic-text-registration",
        "affected_label_ids": affected_labels,
        "affected_primitive_ids": affected_ids,
        "field": "bbox", "evaluation_region": region,
        "evaluation_region_source": "affected_scene_owners" if affected_ids else "full_canvas_no_candidate",
        "proposals": proposals, "accepted": False,
        "baseline_scene_sha256": sha256(output / "scene.json"),
    }
    evidence_dir.mkdir(exist_ok=False)
    if not proposals:
        result["status"] = "unchanged"
        write_json(evidence_dir / "decision.json", result)
        return result
    candidate_dir = evidence_dir / "candidate"
    candidate_dir.mkdir()
    write_json(candidate_dir / "scene.json", candidate_scene)
    try:
        candidate_report = compile_scene(candidate_dir / "scene.json", candidate_dir, config, True)
        build_provenance(candidate_scene, candidate_dir / "editable.pptx", candidate_dir)
        with Image.open(reference_path) as image:
            width, height = image.size
        assessment = compare(reference_path, output / "preview.png", candidate_dir / "preview.png", region)
        result["assessment"] = assessment
        result["candidate_scene_sha256"] = sha256(candidate_dir / "scene.json")
        baseline_fallbacks = read_json(output / "report.json")["compiler"].get("fallbacks", [])
        candidate_fallbacks = candidate_report["compiler"].get("fallbacks", [])
        before = assessment["before_global"]
        after = assessment["after_global"]
        accepted = (
            candidate_report["status"] == "render_success"
            and candidate_fallbacks == baseline_fallbacks
            and after["pixel_similarity"] > before["pixel_similarity"]
            and (after["edge_iou"] or 0) >= (before["edge_iou"] or 0)
        )
        result["accepted"] = accepted
        result["status"] = "accepted" if accepted else "rejected"
        if accepted:
            baseline_dir = evidence_dir / "baseline"
            baseline_dir.mkdir()
            for name in ARTIFACTS:
                source = output / name
                if source.is_file():
                    shutil.copy2(source, baseline_dir / name)
            _adopt_candidate(candidate_dir, output)
            result["final_scene_sha256"] = sha256(output / "scene.json")
            result["final_pptx_sha256"] = sha256(output / "editable.pptx")
    except Exception as exc:
        result["status"] = "rejected_runtime_error"
        result["error"] = f"{type(exc).__name__}: {exc}"
        write_json(evidence_dir / "decision.json", result)
        if result.get("accepted"):
            raise
        return result
    write_json(evidence_dir / "decision.json", result)
    return result
