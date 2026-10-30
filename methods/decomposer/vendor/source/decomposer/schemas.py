from __future__ import annotations

from typing import Any


CATEGORIES = ("text", "equation", "shape", "arrow", "image", "chart")
PRIMITIVES = ("rect", "round_rect", "ellipse", "line", "polyline", "polygon", "path", "text", "image")


def object_schema(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    """Return a strict wire schema; logical optionals use null on the wire."""
    return {
        "type": "object",
        "properties": {
            name: value if name in required else {"anyOf": [value, {"type": "null"}]}
            for name, value in properties.items()
        },
        "required": list(properties),
        "additionalProperties": False,
    }


NUMBER_ARRAY = {"type": "array", "items": {"type": "number"}}
POINT_ARRAY = {"type": "array", "items": NUMBER_ARRAY}
SHADOW_SCHEMA = object_schema({
    "type": {"enum": ["outer"]}, "color": {"type": "string"},
    "opacity": {"type": "number"}, "blur": {"type": "number"},
    "offset": {"type": "number"}, "angle": {"type": "number"},
}, ("type",))
STYLE_SCHEMA = object_schema({
    "fill": {"type": "string"}, "stroke": {"type": "string"},
    "stroke_width": {"type": "number"}, "color": {"type": "string"},
    "font_size": {"type": "number"}, "line_spacing": {"type": "number"}, "font_family": {"type": "string"},
    "font_face": {"type": "string"}, "bold": {"type": "boolean"},
    "italic": {"type": "boolean"},
    "align": {"enum": ["left", "center", "right"]},
    "dashed": {"type": "boolean"},
    "dash_type": {"enum": ["solid", "dash", "dashDot", "lgDash", "lgDashDot", "lgDashDotDot", "sysDash", "sysDot"]},
    "shadow": SHADOW_SCHEMA,
})
TEXT_RUN_SCHEMA = object_schema({
    "text": {"type": "string"},
    "source_text": {"type": "string"},
    "style": STYLE_SCHEMA,
    "script": {"enum": ["normal", "subscript", "superscript"]},
}, ("text",))
CONNECTION_SCHEMA = object_schema({
    "source_id": {"type": "string"}, "target_id": {"type": "string"},
})
ASSET_REQUEST_SCHEMA = object_schema({"mode": {"enum": ["source_crop"]}})
PRIMITIVE_SCHEMA = object_schema({
    "id": {"type": "string"}, "kind": {"type": "string"},
    "bbox": NUMBER_ARRAY, "points": POINT_ARRAY, "text": {"type": "string"},
    "runs": {"type": "array", "items": TEXT_RUN_SCHEMA},
    "path": {"type": "string"}, "arrow_start": {"type": "boolean"},
    "arrow_end": {"type": "boolean"}, "style": STYLE_SCHEMA,
    "asset_request": ASSET_REQUEST_SCHEMA,
}, ("id", "kind"))


RECOGNITION_PROPOSAL_SCHEMA = object_schema({
    "background": {"type": "string"},
    "objects": {
        "type": "array",
        "items": object_schema({
            "id": {"type": "string"}, "category": {"type": "string"},
            "bbox": NUMBER_ARRAY, "text": {"type": "string"},
            "geometry_hint": {"type": "string"}, "points": POINT_ARRAY,
            "style_hint": STYLE_SCHEMA,
            "candidate_ids": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "number"},
            "uncertain": {"type": "string"},
            "connects": CONNECTION_SCHEMA,
            "semantic_level": {"enum": ["container", "leaf", "unknown"]},
            "representation_preference": {"enum": ["native", "localized_raster", "auto"]},
        }, ("category", "bbox")),
    },
    "candidate_decisions": {
        "type": "array",
        "items": object_schema({
            "candidate_id": {"type": "string"},
            "status": {"enum": ["rejected", "merged", "uncertain"]},
            "reason": {"type": "string"},
        }, ("candidate_id", "status", "reason")),
    },
}, ("objects",))


REPRESENTATION_PROPOSAL_SCHEMA = object_schema({
    "components": {
        "type": "array",
        "items": object_schema({
            "label_id": {"type": "string"}, "category": {"type": "string"},
            "width": {"type": "number"}, "height": {"type": "number"},
            "primitives": {"type": "array", "items": PRIMITIVE_SCHEMA},
            "primitive_order": {"type": "array", "items": {"type": "string"}},
        }, ("label_id", "primitives")),
    }
}, ("components",))


LAYOUT_PROPOSAL_SCHEMA = object_schema({
    "operations": {
        "type": "array",
        "items": object_schema({
            "op": {"type": "string"}, "label_id": {"type": "string"},
            "label_ids": {"type": "array", "items": {"type": "string"}},
            "bbox": NUMBER_ARRAY, "rotation_deg": {"type": "number"},
            "factor": {"type": "number"},
        }, ("op",)),
    }
}, ("operations",))


REFINEMENT_OPERATION_SCHEMA = object_schema({
    "op": {"enum": [
        "recognition_bbox", "recognition_text", "recognition_category", "recognition_connects",
        "primitive_bbox", "primitive_points", "primitive_text", "primitive_style", "primitive_kind", "primitive_arrowheads",
        "primitive_text_runs", "primitive_path", "primitive_background", "primitive_rounding",
        "layout_bbox", "layout_rotation", "layout_font_scale", "layout_front", "layout_back",
    ]},
    "label_id": {"type": "string"},
    "primitive_id": {"type": "string"},
    "bbox": NUMBER_ARRAY,
    "points": POINT_ARRAY,
    "text": {"type": "string"},
    "path": {"type": "string"},
    "runs": {"type": "array", "items": TEXT_RUN_SCHEMA},
    "category": {"type": "string"},
    "kind": {"enum": list(PRIMITIVES)},
    "connects": CONNECTION_SCHEMA,
    "style": STYLE_SCHEMA,
    "arrow_start": {"type": "boolean"},
    "arrow_end": {"type": "boolean"},
    "number": {"type": "number"},
    "label_ids": {"type": "array", "items": {"type": "string"}},
}, ("op", "label_id"))


REFINEMENT_SCHEMA = object_schema({
    "acceptable": {"type": "boolean"},
    "selected_candidate_id": {"type": "string"},
    "owner_stage": {"enum": ["none", "recognition", "representation", "layout", "compiler", "infrastructure", "unresolved"]},
    "mechanism": {"type": "string", "pattern": "^[a-z0-9]+(?:-[a-z0-9]+)*$", "maxLength": 48},
    "summary": {"type": "string"},
    "affected_label_ids": {"type": "array", "items": {"type": "string"}},
    "affected_primitive_ids": {"type": "array", "items": {"type": "string"}},
    "field": {"type": "string"},
    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    "strategy": {"type": "string"},
    "referenced_memory_ids": {"type": "array", "items": {"type": "string"}},
    "referenced_skill_ids": {"type": "array", "items": {"type": "string"}},
    "patch_source_memory_ids": {"type": "array", "items": {"type": "string"}},
    "operations": {"type": "array", "maxItems": 8, "items": REFINEMENT_OPERATION_SCHEMA},
}, ("acceptable", "owner_stage", "mechanism", "summary", "affected_label_ids", "affected_primitive_ids", "confidence", "operations"))


def omit_null_fields(value: Any) -> Any:
    """Restore ordinary optional-field semantics before canonicalization."""
    if isinstance(value, dict):
        return {key: omit_null_fields(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [omit_null_fields(item) for item in value]
    return value
