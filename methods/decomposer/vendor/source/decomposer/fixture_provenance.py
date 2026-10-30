"""Attest reuse of completed model proposals as deterministic regression fixtures.

The copied proposal is not a new model call. A replay is valid only when its
source call completed, the prompt input changed solely through the guarded
SAM-to-Recognition crop policy, and the exported proposal matches the source
response after schema-null normalization.
"""
from __future__ import annotations

import copy
from pathlib import Path
import shutil
from typing import Any

from .common import read_json, sha256, write_json
from .schemas import omit_null_fields


EVIDENCE_FILES = {
    "model-output.json": "model-output.json",
    "call-input.json": "input.json",
    "response.txt": "response.txt",
    "metadata.json": "metadata.json",
}


def safe_asset_decision_delta(
    old: dict[str, Any], new: dict[str, Any], recognition: dict[str, Any],
) -> list[str]:
    """Permit only a stricter coverage gate or its safe image-leaf crop fallback."""
    old_policy = dict(old.get("policy") or {})
    new_policy = dict(new.get("policy") or {})
    old_coverage = float(old_policy.pop("minimum_sam_object_coverage", 0))
    new_coverage = float(new_policy.pop("minimum_sam_object_coverage", 0))
    if old_policy != new_policy or new_coverage < max(.6, old_coverage):
        raise ValueError("asset policy changed beyond the stricter SAM coverage gate")
    old_rows = {item["label_id"]: item for item in old.get("decisions", [])}
    new_rows = {item["label_id"]: item for item in new.get("decisions", [])}
    if old_rows.keys() != new_rows.keys() or old.get("summary") != new.get("summary"):
        raise ValueError("asset decision set or counts changed")
    objects = {item["id"]: item for item in recognition.get("objects", [])}
    changed: list[str] = []
    for label_id, before in old_rows.items():
        after = new_rows[label_id]
        if before == after:
            if new_coverage > old_coverage and before.get("source_basis") == "sam_candidate":
                obj = objects.get(label_id)
                box, object_box = before.get("approved_bbox") or [], (obj or {}).get("bbox") or []
                if len(box) != 4 or len(object_box) != 4 or object_box[2] * object_box[3] <= 0:
                    raise ValueError(f"unsafe unchanged SAM decision for {label_id}")
                if box[2] * box[3] / (object_box[2] * object_box[3]) < new_coverage:
                    raise ValueError(f"unsafe unchanged SAM decision for {label_id}")
            continue
        obj = objects.get(label_id)
        if not obj or obj.get("category") != "image" or obj.get("bbox") != after.get("approved_bbox"):
            raise ValueError(f"unsafe asset change for {label_id}: not the recognized image leaf")
        if not (before.get("decision") == after.get("decision") == "localized_raster"
                and before.get("source_basis") == "sam_candidate"
                and after.get("source_basis") == "recognition_bbox"
                and after.get("source_candidate_id") == f"recognition:{label_id}"
                and {"sam_insufficient_object_coverage", "recognition_bbox_fallback"}
                .issubset(after.get("reason_codes") or [])):
            raise ValueError(f"unsafe asset change for {label_id}: source transition")
        old_box, object_box = before.get("approved_bbox") or [], obj["bbox"]
        if len(old_box) != 4 or len(object_box) != 4:
            raise ValueError(f"unsafe asset change for {label_id}: invalid bbox")
        if object_box[2] * object_box[3] <= 0:
            raise ValueError(f"unsafe asset change for {label_id}: invalid object area")
        coverage = old_box[2] * old_box[3] / (object_box[2] * object_box[3])
        if coverage >= new_coverage:
            raise ValueError(f"unsafe asset change for {label_id}: SAM crop was already sufficient")
        changed.append(label_id)
    return sorted(changed)


def _components_without_asset_ids(components: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = copy.deepcopy(components)
    for component in result:
        for primitive in component.get("primitives", []):
            if primitive.get("kind") == "image":
                primitive.pop("asset_id", None)
    return result


def attest_replay(source_run: Path, replay_run: Path) -> dict[str, Any]:
    source_run, replay_run = source_run.resolve(), replay_run.resolve()
    evidence = replay_run / "fixture-evidence"
    if evidence.exists():
        raise FileExistsError(evidence)
    evidence.mkdir()
    hashes: dict[str, dict[str, str]] = {}
    for stage in ("representation", "layout"):
        target = evidence / stage
        target.mkdir()
        source_stage = source_run / stage
        source_call = source_run / "calls" / stage
        sources = {
            "model-output.json": source_stage / "model-output.json",
            "call-input.json": source_call / "input.json",
            "response.txt": source_call / "response.txt",
            "metadata.json": source_call / "metadata.json",
        }
        hashes[stage] = {}
        for name, source in sources.items():
            shutil.copy2(source, target / name)
            hashes[stage][name] = sha256(target / name)
    manifest = {
        "schema_version": "decomposer.fixture-replay-provenance.v1",
        "source_run": str(source_run),
        "source_input_sha256": sha256(source_run / "input" / "normalized.png"),
        "stages": hashes,
    }
    write_json(evidence / "manifest.json", manifest)
    verify_replay(replay_run)
    return manifest


def verify_replay(replay_run: Path) -> dict[str, Any]:
    replay_run = replay_run.resolve()
    evidence = replay_run / "fixture-evidence"
    manifest = read_json(evidence / "manifest.json")
    if manifest.get("schema_version") != "decomposer.fixture-replay-provenance.v1":
        raise ValueError("fixture provenance schema mismatch")
    source_run = Path(manifest["source_run"])
    if (not source_run.is_dir()
            or sha256(source_run / "input" / "normalized.png") != manifest["source_input_sha256"]
            or sha256(replay_run / "input" / "normalized.png") != manifest["source_input_sha256"]):
        raise ValueError("fixture source image identity mismatch")
    recognition = read_json(replay_run / "recognition" / "recognition.json")
    changed_labels: list[str] = []
    old_layout_input: dict[str, Any] | None = None
    for stage in ("representation", "layout"):
        files = manifest["stages"][stage]
        for name in EVIDENCE_FILES:
            copied = evidence / stage / name
            original = (source_run / stage / name if name == "model-output.json"
                        else source_run / "calls" / stage / EVIDENCE_FILES[name])
            if sha256(copied) != files[name] or sha256(original) != files[name]:
                raise ValueError(f"{stage} source evidence hash mismatch: {name}")
        metadata = read_json(evidence / stage / "metadata.json")
        replay_metadata = read_json(replay_run / "calls" / stage / "metadata.json")
        if (metadata.get("status") != "complete" or metadata.get("fallback_used") is not False
                or replay_metadata.get("status") != "fixture"
                or replay_metadata.get("fallback_used") is not False):
            raise ValueError(f"{stage} source call did not complete or replay is not marked fixture")
        proposal = read_json(evidence / stage / "model-output.json")
        response = omit_null_fields(read_json(evidence / stage / "response.txt"))
        if (proposal != response
                or proposal != read_json(replay_run / "calls" / stage / "response.json")
                or proposal != read_json(replay_run / stage / "model-output.json")):
            raise ValueError(f"{stage} fixture does not match completed model response")
        old_input = read_json(evidence / stage / "call-input.json")
        new_input = read_json(replay_run / "calls" / stage / "input.json")
        if stage == "representation":
            if old_input.get("recognition") != recognition or new_input.get("recognition") != recognition:
                raise ValueError("representation recognition input drift")
            changed_labels = safe_asset_decision_delta(
                old_input["asset_decisions"], new_input["asset_decisions"], recognition)
        else:
            old_layout_input = old_input
            if old_input.keys() != new_input.keys():
                raise ValueError("layout prompt fields changed")
            if any(old_input[key] != new_input[key] for key in old_input if key != "components"):
                raise ValueError("layout non-component prompt changed")
            if _components_without_asset_ids(old_input["components"]) != _components_without_asset_ids(new_input["components"]):
                raise ValueError("layout component geometry or semantics changed")
            old_by_id = {item["label_id"]: item for item in old_input["components"]}
            new_by_id = {item["label_id"]: item for item in new_input["components"]}
            changed_components = sorted(label for label in old_by_id if old_by_id[label] != new_by_id[label])
            if changed_components != changed_labels:
                raise ValueError("layout asset-ID change does not match safe crop transition")
    return {"verified": True, "changed_asset_labels": changed_labels,
            "source_run": str(source_run), "layout_input_checked": old_layout_input is not None}
