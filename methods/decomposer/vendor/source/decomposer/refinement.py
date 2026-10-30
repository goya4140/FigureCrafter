from __future__ import annotations

import copy
import json
from pathlib import Path
import re
import shutil
from typing import Any

from PIL import Image, ImageDraw

from .assemble import assemble
from .common import canonical_bbox, canonical_points, clamp, number, read_json, sha256, style, write_json
from .knowledge import capture, link_evidence, refinement_guidance
from .layout import validate as validate_layout
from .refinement_context import bounded
from .local_repair import RepairPreflightError, expand_text_owner, preflight, local_guard
from .model import fixture_or_model
from .polygons import _path_commands, normalize_numeric_path_words
from .pptx import compile_scene
from .provenance import build as build_provenance
from .quality import compare, discover
from .recognition import category, validate as validate_recognition
from .rendered_text_ink import assess as assess_rendered_text_ink
from .representation import canonical_text_runs, validate as validate_representation
from .schemas import REFINEMENT_SCHEMA
from .validation import validate_run


MECHANISM = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SAFE_BBOX_KIND_FAMILY = frozenset({"rect", "round_rect", "ellipse"})
MECHANISM_ALIASES = {
    "raw-latex-in-text-primitive": "literal-math-markup-in-text-primitive",
    "literal-latex-in-text-primitive": "literal-math-markup-in-text-primitive",
    "literal-tex-in-text-primitive": "literal-math-markup-in-text-primitive",
    "partial-object-raster-crop": "partial-object-source-crop",
    "partial-object-crop-stretched-to-full-bbox": "partial-object-source-crop",
    "partial-source-crop-stretched-to-object": "partial-object-source-crop",
    "source-crop-stretched-to-object-bounds": "partial-object-source-crop",
}


def _canonical_mechanism(decision: dict[str, Any]) -> None:
    value = str(decision.get("mechanism") or "")
    if value in MECHANISM_ALIASES:
        decision["diagnosis_mechanism"] = value
        decision["mechanism"] = MECHANISM_ALIASES[value]
        return
    if len(value) <= 48 and MECHANISM.fullmatch(value): return
    decision["diagnosis_mechanism"] = value
    owner = re.sub(r"[^a-z0-9]+", "-", str(decision.get("owner_stage") or "unresolved").lower()).strip("-")
    field = re.sub(r"[^a-z0-9]+", "-", str(decision.get("field") or "visual").lower()).strip("-")
    decision["mechanism"] = f"{owner}-{field}-mismatch"[:48].rstrip("-")


def _evaluation_region(proposal: dict[str, Any], discovery: dict[str, Any], scene: dict[str, Any]) -> tuple[list[float], str]:
    """Resolve the acceptance region from the diagnosed owners, not an unrelated fallback tile."""
    affected_labels = {str(value) for value in proposal.get("affected_label_ids", []) if value}
    affected_primitives = {str(value) for value in proposal.get("affected_primitive_ids", []) if value}
    selected = next(
        (item for item in discovery.get("candidates", []) if item.get("candidate_id") == proposal.get("selected_candidate_id")),
        None,
    )
    if selected and affected_labels.intersection(selected.get("label_ids", [])):
        return list(selected["region"]), "selected_difference_tile"

    owned = []
    for element in scene.get("elements", []):
        element_id = str(element.get("id") or "")
        label_id = str(element.get("source_label_id") or "")
        local_id = element_id.split("/", 1)[-1]
        if label_id in affected_labels or element_id in affected_primitives or local_id in affected_primitives:
            owned.append(element["bbox"])
    if owned:
        x1 = min(box[0] for box in owned); y1 = min(box[1] for box in owned)
        x2 = max(box[0] + box[2] for box in owned); y2 = max(box[1] + box[3] for box in owned)
        canvas = scene.get("canvas", {})
        width = float(canvas.get("width", x2)); height = float(canvas.get("height", y2))
        margin = max(8.0, min(max(x2 - x1, y2 - y1) * .15, 48.0))
        left, top = max(0.0, x1 - margin), max(0.0, y1 - margin)
        right, bottom = min(width, x2 + margin), min(height, y2 + margin)
        return [round(left, 4), round(top, 4), round(right - left, 4), round(bottom - top, 4)], "affected_scene_owners"

    # A selected or highest-scoring tile with no affected owner is not a valid
    # local evaluation region. Such proposals are rejected by candidate
    # alignment; retain an explicit unresolved region for diagnosis only.
    canvas = scene.get("canvas", {})
    return [0.0, 0.0, float(canvas.get("width", 1)), float(canvas.get("height", 1))], "full_canvas_unresolved"


def _candidate_alignment(proposal: dict[str, Any], discovery: dict[str, Any]) -> tuple[bool, str]:
    """Require every actionable diagnosis to be owned by one discovered tile."""
    selected_id = str(proposal.get("selected_candidate_id") or "")
    selected = next(
        (item for item in discovery.get("candidates", []) if str(item.get("candidate_id") or "") == selected_id),
        None,
    )
    if selected is None:
        return False, "selected_candidate_missing_or_unknown"
    affected = {str(value) for value in proposal.get("affected_label_ids", []) if value}
    if not affected:
        return False, "affected_labels_empty"
    if not affected.intersection(str(value) for value in selected.get("label_ids", [])):
        return False, "affected_labels_do_not_intersect_selected_candidate"
    return True, "aligned"


def _native_text_visibility_regression(
    reference: Path,
    current_scene: dict[str, Any],
    current_preview: Path,
    candidate_scene: dict[str, Any],
    candidate_preview: Path,
    affected_label_ids: list[str],
) -> dict[str, Any]:
    """Reject a visually improving patch that newly collapses an affected text owner."""
    affected = set(affected_label_ids)
    before = assess_rendered_text_ink(current_scene, reference, current_preview)
    after = assess_rendered_text_ink(candidate_scene, reference, candidate_preview)
    before_by_id = {row["element_id"]: row for row in before["assessed"]}
    regressions = []
    for row in after["failures"]:
        if row["source_label_id"] not in affected:
            continue
        previous = before_by_id.get(row["element_id"])
        if previous is not None and previous["rendered_to_source_ratio"] < previous["minimum_ratio"]:
            continue
        regressions.append({
            "element_id": row["element_id"],
            "source_label_id": row["source_label_id"],
            "evaluation_region": row["evaluation_region"],
            "evaluation_region_source": row["evaluation_region_source"],
            "before_ratio": previous["rendered_to_source_ratio"] if previous else None,
            "after_ratio": row["rendered_to_source_ratio"],
            "after_dark_pixels": row["rendered_dark_pixels"],
            "source_dark_pixels": row["source_dark_pixels"],
            "minimum_ratio": row["minimum_ratio"],
        })
    return {"passed": not regressions, "regressions": regressions}


def _text_identity(recognition: dict[str, Any], representation: dict[str, Any]) -> dict[str, Any]:
    return {
        "recognition": {item["id"]: str(item.get("text") or "") for item in recognition["objects"]},
        "representation": {
            f"{component['label_id']}/{primitive['id']}": str(primitive.get("text") or "")
            for component in representation["components"]
            for primitive in component["primitives"] if primitive.get("kind") == "text"
        },
    }


def _hard_failure_keys(validation: dict[str, Any]) -> set[tuple[str, tuple[str, ...]]]:
    """Visual threshold misses are quality state; all other failures are hard gates."""
    return {
        (str(item.get("code")), tuple(sorted(str(value) for value in item.get("label_ids", []))))
        for item in validation.get("failures", [])
        if item.get("code") != "visual-quality-below-threshold"
    }


def _knowledge_use(proposal: dict[str, Any], guidance: list[dict[str, Any]]) -> dict[str, Any]:
    memory_ids = {str(item.get("memory_id")) for item in guidance}
    skill_ids = {str(item.get("skill_id")) for item in guidance}
    requested_memories = [str(value) for value in proposal.get("referenced_memory_ids", [])]
    requested_skills = [str(value) for value in proposal.get("referenced_skill_ids", [])]
    requested_sources = [str(value) for value in proposal.get("patch_source_memory_ids", [])]
    return {
        "retrieved_memory_ids": [item["memory_id"] for item in guidance],
        "retrieved_skill_ids": [item["skill_id"] for item in guidance],
        "retrieved_statuses": {item["memory_id"]: item["status"] for item in guidance},
        "referenced_memory_ids": [value for value in requested_memories if value in memory_ids],
        "referenced_skill_ids": [value for value in requested_skills if value in skill_ids],
        "patch_source_memory_ids": [value for value in requested_sources if value in memory_ids],
        "invalid_reference_ids": sorted(
            {value for value in requested_memories if value not in memory_ids}
            | {value for value in requested_skills if value not in skill_ids}
            | {value for value in requested_sources if value not in memory_ids}
        ),
        "outcome": "pending",
    }


def _existing_history(root: Path) -> tuple[list[dict[str, Any]], set[str], int]:
    """Reconstruct completed attempts so resume is idempotent and non-repeating."""
    history = []
    signatures: set[str] = set()
    accepted_rounds = 0
    for round_dir in sorted((root / "refinement").glob("round-*")):
        decision_path, assessment_path = round_dir / "decision.json", round_dir / "assessment.json"
        if not decision_path.is_file():
            continue
        proposal = read_json(decision_path)
        assessment = read_json(assessment_path) if assessment_path.is_file() else None
        trace_path = round_dir / "knowledge-trace.json"
        trace = read_json(trace_path) if trace_path.is_file() else {}
        if assessment is None and not trace.get("outcome"):
            continue
        if proposal.get("operations"):
            signatures.add(json.dumps({
                "selected_candidate_id": proposal.get("selected_candidate_id"),
                "owner_stage": proposal.get("owner_stage"),
                "mechanism": proposal.get("mechanism"),
                "affected_label_ids": proposal.get("affected_label_ids", []),
                "affected_primitive_ids": proposal.get("affected_primitive_ids", []),
                "operations": proposal.get("operations", []),
            }, sort_keys=True, ensure_ascii=False))
        accepted = assessment.get("accepted") if assessment else None
        accepted_rounds += int(accepted is True and (round_dir / "commit.json").is_file())
        history.append({
            "round": int(round_dir.name.rsplit("-", 1)[-1]),
            "evidence_id": trace.get("evidence_id"),
            "owner_stage": proposal.get("owner_stage"),
            "mechanism": proposal.get("mechanism"),
            "selected_candidate_id": proposal.get("selected_candidate_id"),
            "affected_label_ids": proposal.get("affected_label_ids", []),
            "affected_primitive_ids": proposal.get("affected_primitive_ids", []),
            "accepted": accepted,
            "knowledge_use": trace,
        })
    return history, signatures, accepted_rounds


def _recover_pending_commit(root: Path) -> list[str]:
    """Finish an already validated accepted commit after an interrupted copy."""
    recovered = []
    for round_dir in sorted((root / "refinement").glob("round-*")):
        assessment_path = round_dir / "assessment.json"
        if not assessment_path.is_file() or (round_dir / "commit.json").is_file():
            continue
        assessment = read_json(assessment_path)
        if assessment.get("accepted") is not True:
            continue
        required = [
            round_dir / "recognition" / "recognition.json",
            round_dir / "representation" / "representation.json",
            round_dir / "layout" / "layout.json",
            round_dir / "output" / "editable.pptx",
            round_dir / "output" / "preview.png",
            round_dir / "output" / "scene.json",
        ]
        if not all(path.is_file() for path in required):
            continue
        for stage in ("recognition", "representation", "layout"):
            shutil.copy2(round_dir / stage / f"{stage}.json", root / stage / f"{stage}.json")
        shutil.copytree(round_dir / "output", root / "output", dirs_exist_ok=True)
        write_json(round_dir / "commit.json", {
            "status": "recovered_after_interruption",
            "canonical_hashes": {
                "recognition": sha256(root / "recognition" / "recognition.json"),
                "representation": sha256(root / "representation" / "representation.json"),
                "layout": sha256(root / "layout" / "layout.json"),
                "pptx": sha256(root / "output" / "editable.pptx"),
            },
        })
        recovered.append(round_dir.name)
    return recovered


def _next_round(root: Path, max_rounds: int) -> int:
    existing = sorted((root / "refinement").glob("round-*"))
    for path in existing:
        trace_path = path / "knowledge-trace.json"
        assessment_path = path / "assessment.json"
        trace = read_json(trace_path) if trace_path.is_file() else {}
        if not assessment_path.is_file() and not trace.get("outcome"):
            return int(path.name.rsplit("-", 1)[-1])
    return min(max_rounds + 1, max([int(path.name.rsplit("-", 1)[-1]) for path in existing] or [0]) + 1)


def _diagnostic_sheet(reference: Path, current: Path, overlay: Path, target: Path) -> None:
    images = [Image.open(path).convert("RGB") for path in (reference, current, overlay)]
    try:
        width, height = images[0].size
        sheet = Image.new("RGB", (width * 3, height + 32), "white")
        labels = ("REFERENCE", "CURRENT PPTX", "DIFFERENCE CANDIDATES")
        draw = ImageDraw.Draw(sheet)
        for index, (image, label) in enumerate(zip(images, labels)):
            sheet.paste(image.resize((width, height)), (index * width, 32))
            draw.text((index * width + 8, 8), label, fill="black")
        sheet.save(target)
    finally:
        for image in images: image.close()


def _find_primitive(representation: dict[str, Any], label_id: str, primitive_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    component = next((item for item in representation["components"] if item["label_id"] == label_id), None)
    if component is None: raise ValueError(f"unknown representation label {label_id}")
    # Provenance and diagnostic output use globally unique `label/primitive`
    # identifiers, while the canonical component stores the local primitive id.
    # Accept both wire forms, but reject a qualified id owned by another label.
    local_id = str(primitive_id or "")
    if "/" in local_id:
        owner, local_id = local_id.split("/", 1)
        if owner != label_id:
            raise ValueError(f"primitive {primitive_id} is not owned by label {label_id}")
    primitive = next((item for item in component["primitives"] if item["id"] == local_id), None)
    if primitive is None: raise ValueError(f"unknown primitive {label_id}/{local_id}")
    return component, primitive


def apply_patch(recognition: dict[str, Any], representation: dict[str, Any], layout: dict[str, Any], decision: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Apply a label-local declarative patch and revalidate every canonical contract."""
    recognition, representation, layout = copy.deepcopy(recognition), copy.deepcopy(representation), copy.deepcopy(layout)
    labels = {item["id"] for item in recognition["objects"]}
    declared = set(decision.get("affected_label_ids", []))
    if not declared <= labels: raise ValueError(f"diagnosis contains unknown labels: {sorted(declared - labels)}")
    owner = decision["owner_stage"]
    prefixes = {"recognition": "recognition_", "representation": "primitive_", "layout": "layout_"}
    if owner not in prefixes and decision.get("operations"): raise ValueError(f"stage {owner} cannot carry a source patch")
    by_object = {item["id"]: item for item in recognition["objects"]}
    by_placement = {item["label_id"]: item for item in layout["placements"]}
    bbox_owners = [(op['label_id'],op.get('primitive_id','')) for op in decision.get('operations',[]) if op['op']=='primitive_bbox']
    if len(bbox_owners) != len(set(bbox_owners)):
        raise RepairPreflightError("duplicate_bbox_operations_for_same_primitive")
    for operation in decision.get("operations", []):
        op, label_id = operation["op"], operation["label_id"]
        if label_id not in declared: raise ValueError(f"operation changes undeclared label {label_id}")
        if not op.startswith(prefixes[owner]): raise ValueError(f"operation {op} is not owned by {owner}")
        if op == "recognition_bbox": by_object[label_id]["bbox"] = canonical_bbox(operation.get("bbox"), (recognition["canvas"]["width"], recognition["canvas"]["height"]))
        elif op == "recognition_text": by_object[label_id]["text"] = str(operation.get("text") or "")
        elif op == "recognition_category": by_object[label_id]["category"] = category(operation.get("category"))
        elif op == "recognition_connects":
            value = operation.get("connects")
            if not isinstance(value, dict) or {value.get("source_id"), value.get("target_id")} - labels: raise ValueError("invalid recognition connection")
            by_object[label_id]["connects"] = {"source_id": value["source_id"], "target_id": value["target_id"]}
        elif op.startswith("primitive_"):
            component, primitive = _find_primitive(representation, label_id, operation.get("primitive_id", ""))
            if op == "primitive_background":
                if primitive['kind']!='text':raise ValueError('Background requires a text anchor')
                pid=primitive['id']+'_background'
                if any(p['id']==pid for p in component['primitives']):raise ValueError('Background exists')
                bg={'id':pid,'kind':'rect','bbox':canonical_bbox(operation.get('bbox') or primitive['bbox'],(component['width'],component['height'])),'style':style({**operation.get('style',{}),'stroke':'none'}),'editable':True}
                component['primitives'].insert(0,bg);component['primitive_order'].insert(0,pid)
            elif op == "primitive_rounding":
                if primitive['kind'] not in {'rect','round_rect'}:raise ValueError('Rounding requires rectangular shape')
                from .fidelity_contracts import rounded_path
                w,h=primitive['bbox'][2:];radius=float(operation.get('number',0))
                if not 0<=radius<=min(w,h)/2:raise ValueError('Invalid radius')
                primitive.update(kind='path',path=rounded_path(w,h,radius),coordinate_space='primitive',arrow_start=False,arrow_end=False)
            elif op == "primitive_bbox":
                if primitive["kind"] == "text" and sum(op.get("label_id")==label_id and op.get("op")=="primitive_bbox" for op in decision.get("operations",[]))>1:
                    raise RepairPreflightError("text_owner_expansion_cannot_mix_group_bbox_operations")
                if not expand_text_owner(component, primitive, by_placement[label_id], operation.get("bbox"), recognition["canvas"]):
                    primitive["bbox"] = canonical_bbox(operation.get("bbox"), (component["width"], component["height"]))
            elif op == "primitive_points": primitive["points"] = canonical_points(operation.get("points"), (component["width"], component["height"]))
            elif op == "primitive_text": primitive["text"] = str(operation.get("text") or " ")
            elif op == "primitive_path":
                if primitive["kind"] != "path":
                    raise ValueError(f"primitive_path is unsupported for {primitive['kind']}")
                path = normalize_numeric_path_words(str(operation.get("path") or ""))
                _path_commands(path)
                primitive["path"] = path
            elif op == "primitive_text_runs":
                if primitive["kind"] != "text":
                    raise ValueError(f"primitive_text_runs is unsupported for {primitive['kind']}")
                runs = canonical_text_runs(operation.get("runs"), primitive["text"], primitive["style"])
                if not runs:
                    raise ValueError("primitive_text_runs source_text must exactly preserve primitive text")
                primitive["runs"] = runs
            elif op == "primitive_style":
                changes = operation.get("style", {})
                merged = {**primitive.get("style", {}), **changes}
                if "dashed" in changes and "dash_type" not in changes:
                    merged.pop("dash_type", None)
                primitive["style"] = style(merged, text=primitive["kind"] == "text")
            elif op == "primitive_arrowheads":
                if primitive["kind"] not in {"line", "polyline", "path"}:
                    raise ValueError(f"primitive_arrowheads is unsupported for {primitive['kind']}")
                primitive["arrow_start"] = bool(operation.get("arrow_start", primitive.get("arrow_start", False)))
                primitive["arrow_end"] = bool(operation.get("arrow_end", primitive.get("arrow_end", False)))
            elif op == "primitive_kind":
                target_kind = str(operation.get("kind") or "")
                if primitive["kind"] not in SAFE_BBOX_KIND_FAMILY or target_kind not in SAFE_BBOX_KIND_FAMILY:
                    raise ValueError(
                        f"unsafe primitive kind transition {primitive['kind']} -> {target_kind}; "
                        "primitive_kind is limited to bbox-native shapes"
                    )
                primitive["kind"] = target_kind
        elif op == "layout_bbox":
            # Validate the FINAL rotated visible extent below, after all ops.
            # A legitimate quarter-turn box can have a negative unrotated x/y.
            proposed = operation.get("bbox")
            if not isinstance(proposed, (list, tuple)) or len(proposed) != 4:
                raise ValueError("layout_bbox requires four finite coordinates")
            import math
            proposed = [float(v) for v in proposed]
            if not all(math.isfinite(v) for v in proposed) or min(proposed[2:]) <= 0:
                raise ValueError("layout_bbox requires finite coordinates and positive dimensions")
            by_placement[label_id]["bbox"] = proposed
        elif op == "layout_rotation": by_placement[label_id]["rotation_deg"] = round(number(operation.get("number"), 0) % 360, 4)
        elif op == "layout_font_scale": by_placement[label_id]["font_scale"] = round(clamp(number(operation.get("number"), 1), .1, 10), 4)
        elif op in ("layout_front", "layout_back"):
            selected = [value for value in operation.get("label_ids", [label_id]) if value in declared]
            remaining = [value for value in layout["component_order"] if value not in selected]
            layout["component_order"] = remaining + selected if op == "layout_front" else selected + remaining
    validate_recognition(recognition)
    validate_representation(representation, recognition)
    validate_layout(layout, representation, recognition)
    return recognition, representation, layout


def run(root: str | Path, image: str | Path, config: dict[str, Any], *, fixture_dir: str | Path | None = None) -> dict[str, Any]:
    root, image = Path(root), Path(image)
    settings = config.get("refinement", {})
    max_rounds = int(settings.get("max_rounds", 0))
    output = root / "output"
    if max_rounds <= 0 or not (output / "preview.png").exists():
        result = {"status": "skipped", "reason": "disabled_or_render_unavailable", "accepted_rounds": 0}
        write_json(root / "refinement" / "summary.json", result); return result
    recovered_commits = _recover_pending_commit(root)
    recognition = read_json(root / "recognition" / "recognition.json")
    representation = read_json(root / "representation" / "representation.json")
    layout = read_json(root / "layout" / "layout.json")
    current_scene = read_json(output / "scene.json")
    current_preview = output / "preview.png"
    knowledge_root = settings.get("knowledge_path") or str(root / "knowledge")
    history, attempted_signatures, accepted_rounds = _existing_history(root)
    stop_reason = "round_limit"
    baseline_validation = validate_run(root, image, config)
    baseline_hard_failures = _hard_failure_keys(baseline_validation)
    start_round = _next_round(root, max_rounds)
    for round_number in range(start_round, max_rounds + 1):
        round_dir = root / "refinement" / f"round-{round_number:03d}"; round_dir.mkdir(parents=True, exist_ok=True)
        discovery = discover(image, current_preview, current_scene, round_dir / "discover")
        sheet = round_dir / "diagnostic-sheet.png"
        _diagnostic_sheet(image, current_preview, Path(discovery["overlay"]), sheet)
        guidance = refinement_guidance(
            knowledge_root,
            candidates=discovery["candidates"],
            previous_attempts=history,
            limit=int(settings.get("knowledge_limit", 8)),
        )
        payload = {
            "candidates": discovery["candidates"], "recognition": recognition,
            "representation": representation, "layout": layout,
            "provenance": read_json(output / "provenance.json"),
            "applicable_memories": guidance,
            "previous_attempts": history,
            "executor_capabilities": {"primitive_background":"Add one separate native rect behind a text primitive; give bbox and fill, never text fill", "primitive_rounding":"Native rectangular shape to rounded path, number=radius in source pixels", "group_bbox":"Multiple distinct non-text primitive_bbox operations in same owner commit atomically; no owner expansion", "constraints":"Prefer unresolved unreadable text, missing structure, duplicate geometry and wrong directions. Font resource absence cannot be repaired by geometric operations. Keep memory/skill references and all local/neighbor gates."},
        }
        payload = bounded(payload)
        prompt = (Path(__file__).parents[1] / "prompts" / "refinement.md").read_text()
        proposal, metadata = fixture_or_model("refinement", prompt, payload, REFINEMENT_SCHEMA, sheet, config, round_dir / "call", fixture_dir)
        if metadata.get("fallback_used") or not proposal:
            write_json(round_dir / "decision.json", proposal)
            knowledge_use = {
                "retrieved_memory_ids": [item["memory_id"] for item in guidance],
                "retrieved_skill_ids": [item["skill_id"] for item in guidance],
                "outcome": "diagnosis_failed",
            }
            write_json(round_dir / "knowledge-trace.json", knowledge_use)
            history.append({"round": round_number, "accepted": None, "knowledge_use": knowledge_use})
            stop_reason = "diagnosis_failed"; break
        allowed=set(payload['scope_policy']['selectable_owner_ids'])
        requested=set(proposal.get('affected_label_ids',[])) | {op.get('label_id') for op in proposal.get('operations',[]) if op.get('label_id')}
        if not requested <= allowed:
            write_json(round_dir / 'scope-rejection.json', {'requested':sorted(requested),'allowed':sorted(allowed)})
            write_json(round_dir / 'decision.json',proposal)
            write_json(round_dir / 'knowledge-trace.json', {**_knowledge_use(proposal,guidance),'outcome':'scope_rejected'})
            stop_reason='scope_rejected';break
        _canonical_mechanism(proposal)
        knowledge_use = _knowledge_use(proposal, guidance)
        if proposal.get("acceptable"):
            write_json(round_dir / "decision.json", proposal)
            knowledge_use["outcome"] = "model_accepted_current_render"
            write_json(round_dir / "knowledge-trace.json", knowledge_use)
            history.append({
                "round": round_number, "owner_stage": proposal.get("owner_stage"),
                "mechanism": proposal.get("mechanism"), "accepted": None,
                "knowledge_use": knowledge_use,
            })
            stop_reason = "model_accepted"; break
        evaluation_region, evaluation_region_source = _evaluation_region(proposal, discovery, current_scene)
        proposal["evaluation_region"] = evaluation_region
        proposal["evaluation_region_source"] = evaluation_region_source
        aligned, localization_reason = _candidate_alignment(proposal, discovery)
        proposal["localization_status"] = "aligned" if aligned else "invalid"
        if not aligned:
            proposal["localization_error"] = localization_reason
        write_json(round_dir / "decision.json", proposal)
        issue = {**proposal, "detected_in": "render_audit", "region": evaluation_region}
        if not aligned:
            knowledge_use["outcome"] = "localization_failed"
            evidence_id = capture(
                knowledge_root, issue, sample_id=root.name, round_id=round_dir.name,
                accepted=None, assessment=None,
                artifact_paths=[round_dir / "decision.json", sheet],
                knowledge_trace=knowledge_use,
            )
            knowledge_use["evidence_id"] = evidence_id
            write_json(round_dir / "knowledge-trace.json", knowledge_use)
            history.append({
                "round": round_number, "evidence_id": evidence_id,
                "owner_stage": proposal["owner_stage"], "mechanism": proposal["mechanism"],
                "selected_candidate_id": proposal.get("selected_candidate_id"),
                "affected_label_ids": proposal.get("affected_label_ids", []),
                "affected_primitive_ids": proposal.get("affected_primitive_ids", []),
                "field": proposal.get("field"),
                "evaluation_region": evaluation_region,
                "evaluation_region_source": evaluation_region_source,
                "accepted": None,
                "localization_status": "invalid",
                "localization_error": localization_reason,
                "knowledge_use": knowledge_use,
            })
            stop_reason = "localization_failed"; break
        if proposal.get("owner_stage") not in {"recognition", "representation", "layout"} or not proposal.get("operations"):
            knowledge_use["outcome"] = "no_supported_patch"
            evidence_id = capture(knowledge_root, issue, sample_id=root.name, round_id=round_dir.name, accepted=None, assessment=None, artifact_paths=[round_dir / "decision.json", sheet], knowledge_trace=knowledge_use)
            knowledge_use["evidence_id"] = evidence_id
            write_json(round_dir / "knowledge-trace.json", knowledge_use)
            history.append({
                "round": round_number, "evidence_id": evidence_id,
                "owner_stage": proposal.get("owner_stage"), "mechanism": proposal.get("mechanism"),
                "selected_candidate_id": proposal.get("selected_candidate_id"),
                "affected_label_ids": proposal.get("affected_label_ids", []),
                "affected_primitive_ids": proposal.get("affected_primitive_ids", []),
                "accepted": None, "knowledge_use": knowledge_use,
            })
            stop_reason = "no_supported_patch"; break
        signature = json.dumps({
            "selected_candidate_id": proposal.get("selected_candidate_id"),
            "owner_stage": proposal.get("owner_stage"),
            "mechanism": proposal.get("mechanism"),
            "affected_label_ids": proposal.get("affected_label_ids", []),
            "affected_primitive_ids": proposal.get("affected_primitive_ids", []),
            "operations": proposal.get("operations", []),
        }, sort_keys=True, ensure_ascii=False)
        if signature in attempted_signatures:
            knowledge_use["outcome"] = "repeated_candidate_attempt"
            evidence_id = capture(knowledge_root, issue, sample_id=root.name, round_id=round_dir.name, accepted=None, assessment=None, artifact_paths=[round_dir / "decision.json", sheet], knowledge_trace=knowledge_use)
            knowledge_use["evidence_id"] = evidence_id
            write_json(round_dir / "knowledge-trace.json", knowledge_use)
            history.append({
                "round": round_number, "evidence_id": evidence_id,
                "owner_stage": proposal.get("owner_stage"), "mechanism": proposal.get("mechanism"),
                "selected_candidate_id": proposal.get("selected_candidate_id"),
                "affected_label_ids": proposal.get("affected_label_ids", []),
                "affected_primitive_ids": proposal.get("affected_primitive_ids", []),
                "accepted": None, "knowledge_use": knowledge_use,
            })
            stop_reason = "repeated_candidate_attempt"; break
        attempted_signatures.add(signature)
        before_hashes = None
        try:
            before_hashes = {
                "recognition": sha256(root / "recognition" / "recognition.json"),
                "representation": sha256(root / "representation" / "representation.json"),
                "layout": sha256(root / "layout" / "layout.json"),
                "pptx": sha256(output / "editable.pptx"),
            }
            before_preview = round_dir / "before-preview.png"
            shutil.copy2(current_preview, before_preview)
            candidate_recognition, candidate_representation, candidate_layout = apply_patch(recognition, representation, layout, proposal)
            if _text_identity(recognition, representation) != _text_identity(candidate_recognition, candidate_representation):
                raise ValueError("refinement patch changes canonical text content")
            candidate_scene = assemble(candidate_recognition, candidate_representation, candidate_layout)
            execution = preflight(current_scene, candidate_scene, proposal)
            write_json(round_dir / "execution-preflight.json", execution)
            evaluation_region = execution["region"]
            evaluation_region_source = execution["region_source"]
            candidate_output = round_dir / "output"; candidate_output.mkdir(exist_ok=True)
            write_json(round_dir / "recognition" / "recognition.json", candidate_recognition)
            write_json(round_dir / "representation" / "representation.json", candidate_representation)
            write_json(round_dir / "layout" / "layout.json", candidate_layout)
            write_json(candidate_output / "scene.json", candidate_scene)
            compile_scene(candidate_output / "scene.json", candidate_output, config, True)
            build_provenance(candidate_scene, candidate_output / "editable.pptx", candidate_output)
            assessment = compare(image, before_preview, candidate_output / "preview.png", evaluation_region, float(settings.get("global_regression_tolerance", .002)))
            assessment["evaluation_region"] = evaluation_region
            assessment["evaluation_region_source"] = evaluation_region_source
            assessment["execution_preflight"] = execution
            local = local_guard(image, current_scene, before_preview, candidate_scene,
                                candidate_output / "preview.png", execution)
            assessment["local_repair_guard"] = local
            if not local["passed"]:
                assessment["accepted"] = False
                assessment["rejection_reason"] = "local_text_or_neighbor_regression"
            visibility = _native_text_visibility_regression(
                image, current_scene, before_preview, candidate_scene,
                candidate_output / "preview.png", proposal.get("affected_label_ids", []),
            )
            assessment["native_text_visibility_guard"] = visibility
            if not visibility["passed"]:
                assessment["accepted"] = False
                assessment["rejection_reason"] = "affected_native_text_ink_collapse"
            candidate_validation = validate_run(round_dir, image, config)
            candidate_hard_failures = _hard_failure_keys(candidate_validation)
            new_hard_failures = sorted(candidate_hard_failures - baseline_hard_failures)
            assessment["hard_validation"] = {
                "baseline_failure_keys": sorted(baseline_hard_failures),
                "candidate_failure_keys": sorted(candidate_hard_failures),
                "new_failure_keys": new_hard_failures,
                "passed": not new_hard_failures,
            }
            if new_hard_failures:
                assessment["accepted"] = False
                assessment["rejection_reason"] = "new_hard_validation_failure"
            assessment["canonical_before_hashes"] = before_hashes
            write_json(round_dir / "assessment.json", assessment)
        except Exception as exc:
            assessment = {"accepted": False, "error": f"{type(exc).__name__}: {exc}"}
            assessment["rejection_reason"] = str(exc).split(":", 1)[0] if isinstance(exc, RepairPreflightError) else "patch_execution_or_validation_failed"
            assessment["render_skipped"] = isinstance(exc, RepairPreflightError)
        if not assessment["accepted"] and before_hashes is not None:
            assessment["canonical_after_hashes"] = {
                "recognition": sha256(root / "recognition" / "recognition.json"),
                "representation": sha256(root / "representation" / "representation.json"),
                "layout": sha256(root / "layout" / "layout.json"),
                "pptx": sha256(output / "editable.pptx"),
            }
            assessment["rollback_intact"] = assessment["canonical_after_hashes"] == before_hashes
        write_json(round_dir / "assessment.json", assessment)
        knowledge_use["outcome"] = "patch_accepted" if assessment["accepted"] else "patch_rejected"
        artifact_paths = [
            round_dir / "decision.json", round_dir / "assessment.json", sheet,
            round_dir / "before-preview.png", round_dir / "output" / "preview.png",
            round_dir / "output" / "editable.pptx",
        ]
        evidence_id = capture(
            knowledge_root, issue, sample_id=root.name, round_id=round_dir.name,
            accepted=assessment["accepted"], assessment=assessment,
            artifact_paths=artifact_paths, knowledge_trace=knowledge_use,
        )
        linked = link_evidence(knowledge_root, knowledge_use["patch_source_memory_ids"], evidence_id)
        knowledge_use.update(evidence_id=evidence_id, linked_source_memory_ids=linked)
        write_json(round_dir / "knowledge-trace.json", knowledge_use)
        history.append({
            "round": round_number, "evidence_id": evidence_id,
            "owner_stage": proposal["owner_stage"], "mechanism": proposal["mechanism"],
            "selected_candidate_id": proposal.get("selected_candidate_id"),
            "affected_label_ids": proposal.get("affected_label_ids", []),
            "affected_primitive_ids": proposal.get("affected_primitive_ids", []),
            "field": proposal.get("field"),
            "evaluation_region": evaluation_region,
            "evaluation_region_source": evaluation_region_source,
            "accepted": assessment["accepted"],
            "knowledge_use": knowledge_use,
            "assessment": {
                key: assessment.get(key)
                for key in ("local_pixel_delta", "local_edge_delta", "global_pixel_delta", "rejection_reason", "native_text_visibility_guard", "local_repair_guard", "error", "render_skipped")
                if key in assessment
            },
        })
        if not assessment["accepted"]:
            stop_reason = "candidate_rejected"
            continue
        recognition, representation, layout = candidate_recognition, candidate_representation, candidate_layout
        current_scene = candidate_scene; current_preview = candidate_output / "preview.png"; accepted_rounds += 1
        write_json(root / "recognition" / "recognition.json", recognition)
        write_json(root / "representation" / "representation.json", representation)
        write_json(root / "layout" / "layout.json", layout)
        shutil.copytree(candidate_output, output, dirs_exist_ok=True)
        write_json(round_dir / "commit.json", {
            "status": "committed",
            "canonical_hashes": {
                "recognition": sha256(root / "recognition" / "recognition.json"),
                "representation": sha256(root / "representation" / "representation.json"),
                "layout": sha256(root / "layout" / "layout.json"),
                "pptx": sha256(output / "editable.pptx"),
            },
        })
        baseline_validation = read_json(output / "validation.json")
        baseline_hard_failures = _hard_failure_keys(baseline_validation)
    result = {
        "status": "improved" if accepted_rounds else "unchanged",
        "accepted_rounds": accepted_rounds, "stop_reason": stop_reason, "history": history,
        "knowledge": {
            "rounds_with_matches": sum(bool((item.get("knowledge_use") or {}).get("retrieved_memory_ids")) for item in history),
            "referenced_memory_count": sum(len((item.get("knowledge_use") or {}).get("referenced_memory_ids", [])) for item in history),
            "knowledge_guided_patch_count": sum(bool((item.get("knowledge_use") or {}).get("patch_source_memory_ids")) for item in history),
            "recovered_commits": recovered_commits,
        },
    }
    write_json(root / "refinement" / "summary.json", result)
    return result
