from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import canonical_bbox, canonical_color, canonical_points, clamp, number, safe_id, style, unique_id, write_json
from .evidence import dispositions
from .model import fixture_or_model
from .schemas import CATEGORIES, RECOGNITION_PROPOSAL_SCHEMA


CATEGORY_ALIASES = {
    "textbox": "text", "label": "text", "caption": "text", "formula": "equation",
    "math": "equation", "box": "shape", "node": "shape", "icon": "shape",
    "connector": "arrow", "line": "arrow", "picture": "image", "photo": "image",
    "plot": "chart", "graph": "chart",
}


def category(value: Any) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    normalized = CATEGORY_ALIASES.get(normalized, normalized)
    return normalized if normalized in CATEGORIES else "shape"


def _evidence_items(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    values = evidence.get("candidates")
    return values if isinstance(values, list) else evidence.get("items", [])


def _candidate_objects(candidates: dict[str, Any]) -> list[dict[str, Any]]:
    objects = []
    for index, item in enumerate(_evidence_items(candidates), 1):
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("type") or "shape")
        candidate_category = category(label)
        if item.get("source") in {"ocr", "paddleocr", "pp-ocrv5", "remote_paddleocr"}:
            candidate_category = "text"
        elif item.get("crop_candidate"):
            candidate_category = "image"
        objects.append({
            "id": item.get("id") or f"candidate-{index}",
            "category": candidate_category,
            "bbox": item.get("bbox"),
            "text": item.get("text") or "",
            "geometry_hint": label,
            "style_hint": {},
            "candidate_ids": [str(item.get("id"))] if item.get("id") else [],
            "confidence": item.get("confidence", 0.5),
            "semantic_level": item.get("semantic_level", "unknown"),
            "representation_preference": "localized_raster" if item.get("crop_candidate") else "native",
        })
    return objects


def canonicalize(raw: Any, canvas: tuple[int, int], candidates: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    raw = raw if isinstance(raw, dict) else {}
    raw_objects = raw.get("objects") if isinstance(raw.get("objects"), list) else []
    fallback_kind = None
    if not raw_objects:
        raw_objects = _candidate_objects(candidates)
        fallback_kind = "detector_candidates"
    if not raw_objects:
        raw_objects = [{
            "id": "fallback-background", "category": "shape", "bbox": [0, 0, canvas[0], canvas[1]],
            "text": "", "geometry_hint": "editable blank canvas fallback", "confidence": 0,
            "semantic_level": "container", "representation_preference": "native",
        }]
        fallback_kind = "editable_blank_canvas"

    valid_candidate_ids = {str(item.get("id")) for item in _evidence_items(candidates) if isinstance(item, dict) and item.get("id")}
    used: set[str] = set()
    objects: list[dict[str, Any]] = []
    repairs: list[dict[str, Any]] = []
    for index, item in enumerate(raw_objects, 1):
        if not isinstance(item, dict):
            repairs.append({"index": index - 1, "action": "dropped_non_object"})
            continue
        preferred = safe_id(item.get("id"), f"object-{index}")
        object_id = unique_id(preferred, used)
        object_category = category(item.get("category"))
        box = canonical_bbox(item.get("bbox"), canvas)
        points = canonical_points(item.get("points"), canvas)
        if object_category == "arrow" and len(points) < 2:
            x, y, width, height = box
            points = [[x, y + height / 2], [x + width, y + height / 2]]
            repairs.append({"id": object_id, "action": "synthesized_arrow_points"})
        connection = item.get("connects") if isinstance(item.get("connects"), dict) else None
        raw_references = item.get("candidate_ids") if isinstance(item.get("candidate_ids"), list) else []
        referenced = [str(value) for value in raw_references if str(value) in valid_candidate_ids]
        unknown_references = [str(value) for value in raw_references if str(value) not in valid_candidate_ids]
        if unknown_references:
            repairs.append({"id": object_id, "action": "removed_unknown_candidate_references", "candidate_ids": unknown_references})
        candidate_values = [
            value for value in _evidence_items(candidates)
            if isinstance(value, dict) and str(value.get("id")) in referenced
        ]
        inferred_level = "container" if any(value.get("semantic_level") == "container" for value in candidate_values) else "leaf"
        semantic_level = str(item.get("semantic_level") or inferred_level)
        if semantic_level not in {"container", "leaf", "unknown"}:
            semantic_level = inferred_level
        preference = str(item.get("representation_preference") or "auto")
        if preference not in {"native", "localized_raster", "auto"}:
            preference = "auto"
        area_ratio = (box[2] * box[3]) / max(1.0, float(canvas[0] * canvas[1]))
        if object_category == "image" and area_ratio >= 0.85:
            object_category = "shape"
            preference = "native"
            semantic_level = "container"
            repairs.append({"id": object_id, "action": "converted_whole_canvas_image_to_native_container"})
        objects.append({
            "id": object_id,
            "category": object_category,
            "bbox": box,
            "text": str(item.get("text") or ""),
            "geometry_hint": str(item.get("geometry_hint") or item.get("geometry") or ""),
            "points": points,
            "style_hint": style(item.get("style_hint") or item.get("style"), text=object_category == "text"),
            "candidate_ids": referenced,
            "confidence": round(clamp(number(item.get("confidence"), 0.5), 0, 1), 4),
            "uncertain": str(item.get("uncertain")) if item.get("uncertain") else None,
            "connects": connection,
            "semantic_level": semantic_level,
            "representation_preference": preference,
        })
    if not objects:
        fallback_kind = "editable_blank_canvas"
        objects = [{
            "id": "fallback-background", "category": "shape", "bbox": [0.0, 0.0, float(canvas[0]), float(canvas[1])],
            "text": "", "geometry_hint": "editable blank canvas fallback", "points": [], "style_hint": style({"fill": "#FFFFFF", "stroke": "none"}),
            "candidate_ids": [], "confidence": 0.0, "uncertain": "recognition proposal contained no usable objects",
            "connects": None, "semantic_level": "container", "representation_preference": "native",
        }]
        repairs.append({"action": "generated_editable_blank_canvas_fallback"})
    ids = {item["id"] for item in objects}
    for item in objects:
        connection = item["connects"]
        if connection and ({connection.get("source_id"), connection.get("target_id")} - ids):
            item["connects"] = None
            repairs.append({"id": item["id"], "action": "removed_invalid_connection"})
    result = {
        "schema_version": "decomposer.recognition.v1",
        "canvas": {"width": canvas[0], "height": canvas[1]},
        "background": canonical_color(raw.get("background"), "#FFFFFF"),
        "objects": objects,
        "unresolved": [str(value) for value in raw.get("unresolved", [])] if isinstance(raw.get("unresolved"), list) else [],
        "degraded": fallback_kind is not None,
    }
    report = {"fallback": fallback_kind, "repair_count": len(repairs), "repairs": repairs, "object_count": len(objects)}
    validate(result)
    return result, report


def validate(data: dict[str, Any]) -> None:
    canvas = data["canvas"]
    if canvas["width"] <= 0 or canvas["height"] <= 0 or not data["objects"]:
        raise AssertionError("canonical recognition must have a positive canvas and at least one object")
    ids = [item["id"] for item in data["objects"]]
    if len(ids) != len(set(ids)):
        raise AssertionError("canonical recognition IDs must be unique")
    for item in data["objects"]:
        if item["category"] not in CATEGORIES or item["bbox"][2] <= 0 or item["bbox"][3] <= 0:
            raise AssertionError(f"invalid canonical recognition object: {item['id']}")


def run(
    image: str | Path,
    canvas: tuple[int, int],
    evidence: dict[str, Any],
    config: dict[str, Any],
    directory: str | Path,
    calls: str | Path,
    fixture_dir: str | Path | None = None,
) -> dict[str, Any]:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "canvas": list(canvas), "categories": list(CATEGORIES),
        "evidence": {
            "candidates": evidence.get("candidates", []),
            "relations": evidence.get("relations", []),
            "detector_sources": evidence.get("detector_sources", {}),
        },
        # Historical knowledge is deliberately unavailable to first-pass generation.
        "applicable_memories": [],
    }
    prompt = (Path(__file__).parents[1] / "prompts" / "recognition.md").read_text()
    proposal, metadata = fixture_or_model("recognition", prompt, payload, RECOGNITION_PROPOSAL_SCHEMA, image, config, Path(calls) / "recognition", fixture_dir)
    write_json(directory / "model-output.json", proposal)
    result, report = canonicalize(proposal, canvas, evidence)
    report["model"] = metadata
    write_json(directory / "canonicalization-report.json", report)
    write_json(directory / "recognition.json", result)
    write_json(directory / "candidate-dispositions.json", dispositions(evidence, result, proposal))
    return result
