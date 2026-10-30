"""Source-measured native text alignment after character-spacing calibration."""
from __future__ import annotations

from pathlib import Path
import shutil
from typing import Any

from PIL import Image

from .common import read_json, sha256, write_json
from .pptx import compile_scene
from .provenance import build as build_provenance
from .quality import affected_scene_region, compare
from .text_registration import ARTIFACTS, _adopt_candidate, propose


MAX_SHIFT = 6
MIN_ABSOLUTE_GAIN = 1.0
MIN_RELATIVE_GAIN = .03


def run(root: Path, reference_path: Path, config: dict[str, Any]) -> dict[str, Any]:
    output = root / "output"
    evidence = root / "post-tracking-text-registration"
    prior_path = root / "text-tracking-registration" / "decision.json"
    if not prior_path.is_file():
        raise ValueError("post-tracking text alignment requires text-tracking evidence")
    prior_final = read_json(prior_path)["final_scene_sha256"]
    current_hash = sha256(output / "scene.json")
    if evidence.exists():
        decision_path = evidence / "decision.json"
        if not decision_path.is_file():
            raise ValueError("incomplete post-tracking alignment evidence; inspect before resuming")
        decision = read_json(decision_path)
        if current_hash == decision.get("final_scene_sha256"):
            return decision
        if current_hash == decision.get("baseline_scene_sha256") and decision.get("accepted"):
            _adopt_candidate(evidence / "candidate", output)
            if (sha256(output / "scene.json") != decision.get("final_scene_sha256")
                    or sha256(output / "editable.pptx") != decision.get("final_pptx_sha256")):
                raise ValueError("reused post-tracking alignment candidate hash mismatch")
            return decision
        if current_hash == decision.get("baseline_scene_sha256") and not decision.get("accepted"):
            return decision
        raise ValueError("post-tracking alignment identity drift")
    if current_hash != prior_final:
        raise ValueError("post-tracking alignment input is not text-tracking final scene")
    scene = read_json(output / "scene.json")
    candidate_scene, proposals = propose(
        scene, reference_path, output / "preview.png",
        max_shift=MAX_SHIFT, min_absolute_gain=MIN_ABSOLUTE_GAIN,
        min_relative_gain=MIN_RELATIVE_GAIN,
    )
    region = affected_scene_region(scene, candidate_scene, [item["id"] for item in proposals])
    result: dict[str, Any] = {
        "owner_stage": "layout", "mechanism": "post-tracking-source-measured-native-text-alignment",
        "selected_candidate_id": "deterministic-post-tracking-text-registration" if proposals else None,
        "affected_label_ids": sorted({item["source_label_id"] for item in proposals}),
        "affected_primitive_ids": [item["id"] for item in proposals],
        "field": "bbox", "evaluation_region": region,
        "evaluation_region_source": "affected_scene_owners" if proposals else "full_canvas_no_candidate",
        "max_shift": MAX_SHIFT, "min_absolute_gain": MIN_ABSOLUTE_GAIN,
        "min_relative_gain": MIN_RELATIVE_GAIN,
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
            "baseline_fallbacks": baseline_fallbacks, "candidate_fallbacks": candidate_fallbacks,
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
