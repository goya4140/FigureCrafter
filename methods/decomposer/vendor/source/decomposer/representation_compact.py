"""Sparse wire format for the unchanged canonical Representation contract."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import write_json
from .model import fixture_or_model
from .schemas import NUMBER_ARRAY, POINT_ARRAY, object_schema


STYLE_KEYS = (
    "fill", "stroke", "stroke_width", "color", "font_size", "line_spacing",
    "font_family", "font_face", "bold", "italic", "align", "dashed",
    "dash_type", "shadow",
)
BOOLEAN_STYLE_KEYS = {"bold", "italic", "dashed"}
NUMBER_STYLE_KEYS = {"stroke_width", "font_size", "line_spacing"}
STYLE_ENTRY = object_schema({
    "key": {"enum": list(STYLE_KEYS)},
    "value": {"anyOf": [
        {"type": "string"}, {"type": "number"}, {"type": "boolean"},
        object_schema({
            "type": {"enum": ["outer"]}, "color": {"type": "string"},
            "opacity": {"type": "number"}, "blur": {"type": "number"},
            "offset": {"type": "number"}, "angle": {"type": "number"},
        }, ("type",)),
    ]},
}, ("key", "value"))
STYLE_LIST = {"type": "array", "items": STYLE_ENTRY}
RUN = object_schema({
    "text": {"type": "string"}, "source_text": {"type": "string"},
    "style": STYLE_LIST,
    "script": {"enum": ["normal", "subscript", "superscript"]},
}, ("text", "source_text", "style", "script"))


def _primitive(kind: str, properties: dict[str, Any]) -> dict[str, Any]:
    return object_schema({
        "id": {"type": "string"}, "kind": {"enum": [kind]},
        **properties, "style": STYLE_LIST,
    }, tuple(["id", "kind", *properties, "style"]))


PRIMITIVE = {"anyOf": [
    *[_primitive(kind, {"bbox": NUMBER_ARRAY})
      for kind in ("rect", "round_rect", "ellipse")],
    *[_primitive(kind, {"points": POINT_ARRAY,
                       "arrow_start": {"type": "boolean"},
                       "arrow_end": {"type": "boolean"}})
      for kind in ("line", "polyline")],
    _primitive("polygon", {"points": POINT_ARRAY}),
    _primitive("path", {"bbox": NUMBER_ARRAY, "path": {"type": "string"},
                        "arrow_start": {"type": "boolean"},
                        "arrow_end": {"type": "boolean"}}),
    _primitive("text", {"bbox": NUMBER_ARRAY, "text": {"type": "string"},
                        "runs": {"type": "array", "items": RUN}}),
    _primitive("image", {"bbox": NUMBER_ARRAY}),
]}
COMPACT_SCHEMA = object_schema({
    "components": {"type": "array", "items": object_schema({
        "label_id": {"type": "string"},
        "primitives": {"type": "array", "items": PRIMITIVE},
    }, ("label_id", "primitives"))},
}, ("components",))


def _decode_style(entries: Any) -> dict[str, Any]:
    if not isinstance(entries, list):
        raise ValueError("compact style must be an array")
    result: dict[str, Any] = {}
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("key") not in STYLE_KEYS:
            raise ValueError("invalid compact style entry")
        key = entry["key"]
        if key in result:
            raise ValueError(f"duplicate compact style key: {key}")
        value = entry.get("value")
        if key in BOOLEAN_STYLE_KEYS:
            if isinstance(value, str) and value.lower() in {"true", "false"}:
                value = value.lower() == "true"
            if not isinstance(value, bool):
                raise ValueError(f"invalid boolean style value: {key}")
        elif key in NUMBER_STYLE_KEYS:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"invalid numeric style value: {key}")
        elif key == "shadow":
            if not isinstance(value, dict) or value.get("type") != "outer":
                raise ValueError("invalid shadow style value")
        elif not isinstance(value, str):
            raise ValueError(f"invalid string style value: {key}")
        result[key] = value
    return result


def decode(proposal: Any, recognition: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(proposal, dict) or not isinstance(proposal.get("components"), list):
        raise ValueError("compact representation missing components")
    expected = {row["id"] for row in recognition["objects"]}
    components = proposal["components"]
    labels = [row.get("label_id") for row in components if isinstance(row, dict)]
    if len(labels) != len(expected) or len(set(labels)) != len(expected) or set(labels) != expected:
        raise ValueError("compact representation returned missing, duplicate, or foreign labels")
    by_id = {row["id"]: row for row in recognition["objects"]}
    for component in components:
        if not isinstance(component.get("primitives"), list) or not component["primitives"]:
            raise ValueError(f"compact representation has empty component: {component['label_id']}")
        for primitive in component["primitives"]:
            primitive["style"] = _decode_style(primitive.get("style"))
            if primitive.get("kind") == "text":
                # Sparse wire styles are overrides on measured Recognition
                # text style. Missing color otherwise becomes transparent in
                # the current canonicalizer, and missing bold/align can move
                # glyph ink even when the model preserved the right text.
                hint = by_id[component["label_id"]].get("style_hint") or {}
                if isinstance(hint, dict):
                    for key in ("color", "font_size", "font_family", "font_face",
                                "bold", "italic", "align", "line_spacing"):
                        if key not in primitive["style"] and hint.get(key) is not None:
                            primitive["style"][key] = hint[key]
                if primitive["style"].get("color") in {None, "none", "transparent"}:
                    color = hint.get("color") if isinstance(hint, dict) else None
                    primitive["style"]["color"] = (
                        color if isinstance(color, str) and color.lower() not in {"none", "transparent"}
                        else "#000000"
                    )
                for run in primitive.get("runs") or []:
                    run["style"] = _decode_style(run.get("style"))
                    if run["style"].get("color") in {"none", "transparent"}:
                        run["style"]["color"] = primitive["style"]["color"]
    return proposal


def generate(
    image: str | Path,
    recognition: dict[str, Any],
    asset_decisions: dict[str, Any],
    config: dict[str, Any],
    calls_dir: str | Path,
    prompt: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    compact_prompt = prompt + (
        "\nReturn the same component-local representation in the sparse wire schema. "
        "Use an empty style array only when canonical defaults match the image; list "
        "the required visible "
        "style properties as key/value entries. Set stroke to 'none' explicitly "
        "for shapes without outlines; the canonical default stroke is black. "
        "For text primitives, use an empty "
        "runs array unless styled runs are needed. The compiler will take component "
        "width and height from Recognition bboxes. Return exactly one nonempty "
        "component for every recognized label ID.\n"
    )
    proposal, metadata = fixture_or_model(
        "representation", compact_prompt,
        {"recognition": recognition, "asset_decisions": asset_decisions,
         "representation_strategy": config.get("representation", {}),
         "coordinate_system": "component-local xywh", "applicable_memories": []},
        COMPACT_SCHEMA, image, config, Path(calls_dir) / "representation", None,
    )
    if metadata.get("status") != "complete" or metadata.get("fallback_used"):
        raise RuntimeError(f"compact representation model call failed: {metadata.get('error', metadata.get('status'))}")
    try:
        decoded = decode(proposal, recognition)
    except ValueError as exc:
        metadata = {**metadata, "status": "contract_failed", "error": str(exc)}
        write_json(Path(calls_dir) / "representation" / "metadata.json", metadata)
        raise
    return decoded, metadata
