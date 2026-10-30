"""Guarded, artifact-backed native text style correction after ordinary registration.

Only owners with a conservative source-ink deficit are searched. Each proposal
is recompiled and rendered; only owner-local and global non-regressing PPTX
measurements can replace the current output. Existing frozen runs are never
modified by this module unless it is explicitly invoked on that run.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
from typing import Any, Callable

from .common import read_json, sha256, write_json
from .native_text_style_probe import (
    dark_ink_candidates, search_owner, search_single_line_monospace_owner,
    single_line_monospace_ink_candidates,
)
from .text_registration import ARTIFACTS, _adopt_candidate


def _copy_artifacts(source: Path, target: Path) -> None:
    target.mkdir(exist_ok=False)
    for name in ARTIFACTS:
        if (source / name).is_file():
            shutil.copy2(source / name, target / name)


def _run(
    root: Path, reference_path: Path, config: dict[str, Any], *,
    evidence_name: str, schema_version: str, mechanism: str, field: str,
    trigger_fn: Callable[..., list[dict[str, Any]]],
    search_fn: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    output = root / "output"
    evidence = root / evidence_name
    scene_path = output / "scene.json"
    pptx_path = output / "editable.pptx"
    current_hash = sha256(scene_path)
    config_sha256 = hashlib.sha256(json.dumps(config, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if evidence.exists():
        decision_path = evidence / "decision.json"
        if not decision_path.is_file():
            raise ValueError("incomplete native text style evidence; inspect before resuming")
        decision = read_json(decision_path)
        if decision.get("config_sha256") != config_sha256:
            raise ValueError("native text style configuration identity drift")
        if current_hash == decision.get("final_scene_sha256"):
            if sha256(pptx_path) != decision.get("final_pptx_sha256"):
                _adopt_candidate(evidence / ("final" if decision.get("accepted") else "baseline"), output)
                if (sha256(scene_path) != decision.get("final_scene_sha256")
                        or sha256(pptx_path) != decision.get("final_pptx_sha256")):
                    raise ValueError("native text style final PPTX identity drift")
            return decision
        if current_hash == decision.get("baseline_scene_sha256") and decision.get("accepted"):
            _adopt_candidate(evidence / "final", output)
            if (sha256(scene_path) != decision.get("final_scene_sha256")
                    or sha256(pptx_path) != decision.get("final_pptx_sha256")):
                raise ValueError("native text style replay identity drift")
            return decision
        if current_hash == decision.get("baseline_scene_sha256") and not decision.get("accepted"):
            if sha256(pptx_path) != decision.get("baseline_pptx_sha256"):
                raise ValueError("native text style baseline PPTX identity drift")
            return decision
        raise ValueError("native text style input identity drift")

    initial_scene = read_json(scene_path)
    triggers = trigger_fn(initial_scene, reference_path, output / "preview.png")
    result: dict[str, Any] = {
        "schema_version": schema_version,
        "owner_stage": "representation",
        "mechanism": mechanism,
        "selected_candidate_id": None,
        "affected_label_ids": [], "affected_primitive_ids": [],
        "field": field,
        "evaluation_region": None,
        "evaluation_region_source": "full_canvas_no_candidate",
        "triggers": triggers,
        "owner_decisions": [], "accepted": False,
        "baseline_scene_sha256": current_hash,
        "baseline_pptx_sha256": sha256(pptx_path),
        "config_sha256": config_sha256,
    }
    evidence.mkdir(exist_ok=False)
    _copy_artifacts(output, evidence / "baseline")
    for index, trigger in enumerate(triggers, 1):
        owner_id = trigger["id"]
        scene = read_json(scene_path)
        current_triggers = {item["id"] for item in
                            trigger_fn(scene, reference_path, output / "preview.png")}
        if owner_id not in current_triggers:
            result["owner_decisions"].append({
                "owner_id": owner_id, "status": "trigger-resolved-by-prior-owner",
                "accepted": False,
            })
            continue
        search = search_fn(
            scene, reference_path, output / "preview.png", owner_id, config,
            evidence / f"owner-{index:03d}", read_json(output / "report.json"),
        )
        best_id = search["best_candidate_id"]
        best = next((item for item in search["attempts"]
                     if item.get("candidate_id") == best_id), None)
        owner_decision = {
            "owner_id": owner_id,
            "owner_stage": "representation",
            "mechanism": mechanism,
            "selected_candidate_id": best_id,
            "affected_label_ids": [trigger["source_label_id"]],
            "affected_primitive_ids": [owner_id],
            "field": field,
            "evaluation_region": best["evaluation_region"] if best else trigger["bbox"],
            "evaluation_region_source": "affected_scene_owners",
            "assessment": best["assessment"] if best else None,
            "search_report": str(evidence / f"owner-{index:03d}" / "search-report.json"),
            "accepted": bool(best),
            "status": "accepted" if best else "rejected",
        }
        result["owner_decisions"].append(owner_decision)
        if best:
            _adopt_candidate(Path(best["artifact_dir"]), output)
            if (sha256(scene_path) != best["scene_sha256"]
                    or sha256(pptx_path) != best["pptx_sha256"]):
                raise ValueError("native text style adopted artifact hash mismatch")
            result["accepted"] = True
            result["selected_candidate_id"] = best_id
            result["affected_label_ids"].append(trigger["source_label_id"])
            result["affected_primitive_ids"].append(owner_id)
            result["evaluation_region"] = best["evaluation_region"]
            result["evaluation_region_source"] = "affected_scene_owners"
        write_json(evidence / "in-progress.json", result)
    result["status"] = "accepted" if result["accepted"] else "rejected" if triggers else "unchanged"
    result["final_scene_sha256"] = sha256(scene_path)
    result["final_pptx_sha256"] = sha256(pptx_path)
    if result["accepted"]:
        _copy_artifacts(output, evidence / "final")
    write_json(evidence / "decision.json", result)
    return result


def run(root: Path, reference_path: Path, config: dict[str, Any]) -> dict[str, Any]:
    return _run(
        root, reference_path, config,
        evidence_name="native-text-style-registration",
        schema_version="decomposer.native-text-style-registration.v1",
        mechanism="native-text-source-ink-style-and-registration",
        field="style.font_family+style.font_size+bbox",
        trigger_fn=dark_ink_candidates, search_fn=search_owner,
    )


def run_monospace(root: Path, reference_path: Path, config: dict[str, Any]) -> dict[str, Any]:
    return _run(
        root, reference_path, config,
        evidence_name="single-line-monospace-registration",
        schema_version="decomposer.single-line-monospace-registration.v1",
        mechanism="single-line-monospace-ink-scale-and-registration",
        field="style.font_size+bbox",
        trigger_fn=single_line_monospace_ink_candidates,
        search_fn=search_single_line_monospace_owner,
    )
