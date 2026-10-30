from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
import unicodedata

from PIL import Image, ImageDraw

from .common import canonical_bbox, canonical_points, number, safe_id, style, unique_id, write_json
from .model import fixture_or_model
from .polygons import _path_commands, normalize_numeric_path_words
from .schemas import PRIMITIVES, REPRESENTATION_PROPOSAL_SCHEMA
from .representation_shards import generate as generate_sharded
from .representation_compact import generate as generate_compact


KIND_ALIASES = {
    "shape": "rect", "rectangle": "rect", "roundrect": "round_rect", "rounded_rectangle": "round_rect",
    "circle": "ellipse", "connector": "line", "arrow": "line", "picture": "image", "crop": "image",
}


def representation_model_config(config: dict[str, Any]) -> dict[str, Any]:
    """Apply model settings to Representation without changing downstream stages."""
    overrides = config.get("representation", {}).get("model_overrides", {})
    if not isinstance(overrides, dict):
        raise ValueError("representation.model_overrides must be an object")
    return {**config, "model": {**config.get("model", {}), **overrides}} if overrides else config


def representation_prompt_strategy(config: dict[str, Any]) -> dict[str, Any]:
    """Expose figure strategy choices, excluding model routing controls."""
    return {
        key: value for key, value in config.get("representation", {}).items()
        if key not in {"model_overrides", "fidelity_guard", "geometry_precision_hint"}
    }


def _text_identity(value: Any) -> str:
    return str(value or "")


def canonical_text_runs(raw: Any, source_text: str, base_style: dict[str, Any]) -> list[dict[str, Any]]:
    """Canonicalize editable rich-text runs while preserving exact source identity."""
    if not isinstance(raw, list) or not raw:
        return []
    runs = []
    for item in raw:
        if not isinstance(item, dict):
            return []
        visible = str(item.get("text") or "")
        source = str(item.get("source_text")) if item.get("source_text") is not None else visible
        if not visible and not source:
            continue
        run_style = style({**base_style, **(item.get("style") if isinstance(item.get("style"), dict) else {})}, text=True)
        script = item.get("script") if item.get("script") in {"normal", "subscript", "superscript"} else "normal"
        if (script == "subscript" and source
                and all("SUBSCRIPT" in unicodedata.name(character, "") for character in source)):
            # Unicode subscript glyphs already encode their vertical position.
            # Applying OOXML subscript again can shrink them into invisible ink.
            visible, script = source, "normal"
        runs.append({"text": visible, "source_text": source, "style": run_style, "script": script})
    if not runs or "".join(item["source_text"] for item in runs) != source_text:
        return []
    return runs


def _crop_asset(
    image_path: Path, source_bbox: list[float], label_id: str, assets_dir: Path,
    source_candidate_id: str | None, source_basis: str | None,
    native_text_exclusions: list[dict[str, Any]] | None = None,
    asset_transforms: list[dict[str, Any]] | None = None,
) -> tuple[str, dict[str, Any]]:
    with Image.open(image_path) as image:
        x, y, width, height = source_bbox
        bounds = [int(x), int(y), max(int(x + width + 0.999), int(x) + 1), max(int(y + height + 0.999), int(y) + 1)]
        from .asset_transforms import transformed_crop
        cropped = transformed_crop(image, source_bbox, asset_transforms or [])
        for exclusion in native_text_exclusions or []:
            ex,ey,ew,eh=exclusion["bbox"]
            if exclusion.get("fill")!="#FFFFFF":raise ValueError("Unverified photo patch fill")
            ImageDraw.Draw(cropped).rectangle([int(ex)-bounds[0],int(ey)-bounds[1],int(ex+ew)-bounds[0]-1,int(ey+eh)-bounds[1]-1],fill="white")
        asset_id = "asset-" + hashlib.sha256((label_id + repr(bounds)).encode()).hexdigest()[:16]
        assets_dir.mkdir(parents=True, exist_ok=True)
        path = assets_dir / f"{asset_id}.png"
        cropped.save(path)
    return asset_id, {
        "status": "ready", "source_label_id": label_id, "source_bbox": source_bbox,
        "source_candidate_id": source_candidate_id,
        "source_basis": source_basis,
        "mode": "source_crop", "path": str(path.resolve()),
        "native_text_exclusions": native_text_exclusions or [],
        "asset_transforms": asset_transforms or [],
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _fallback_component(obj: dict[str, Any], allow_raster: bool = False) -> dict[str, Any]:
    _, _, width, height = obj["bbox"]
    primitive_style = obj["style_hint"]
    category = obj["category"]
    if category == "image" and allow_raster:
        primitives = [{"id": "content", "kind": "image", "bbox": [0, 0, width, height], "style": style({})}]
    elif category == "equation" and obj.get("text"):
        primitives = [{"id": "equation", "kind": "text", "bbox": [0, 0, width, height], "text": obj["text"], "style": style(primitive_style, text=True)}]
    elif category in ("image", "chart", "equation"):
        primitives = [{"id": "native-placeholder", "kind": "rect", "bbox": [0, 0, width, height], "style": primitive_style}]
    elif category == "text":
        primitives = [{"id": "text", "kind": "text", "bbox": [0, 0, width, height], "text": obj["text"] or " ", "style": primitive_style}]
    elif category == "arrow":
        points = [[point[0] - obj["bbox"][0], point[1] - obj["bbox"][1]] for point in obj.get("points", [])]
        if len(points) < 2:
            points = [[0, height / 2], [width, height / 2]]
        primitives = [{"id": "arrow", "kind": "line", "points": points, "arrow_start": False, "arrow_end": True, "style": primitive_style}]
    else:
        primitives = [{"id": "shape", "kind": "rect", "bbox": [0, 0, width, height], "style": primitive_style}]
        if obj["text"]:
            primitives.append({"id": "text", "kind": "text", "bbox": [0, 0, width, height], "text": obj["text"], "style": style(primitive_style, text=True)})
    return {"label_id": obj["id"], "category": category, "width": width, "height": height, "primitives": primitives}


def _approved_raster_text_primitives(candidate: dict[str, Any], expected_text: str) -> list[dict[str, Any]]:
    """Keep exact, visible native text while replacing untrusted image geometry."""
    if not expected_text.strip():
        return []
    texts = [item for item in candidate.get("primitives", [])
             if isinstance(item, dict) and item.get("kind") == "text"]
    if "".join(str(item.get("text") or "") for item in texts) != expected_text:
        return []
    for item in texts:
        base_color = str((item.get("style") or {}).get("color") or "none").casefold()
        runs = item.get("runs")
        if isinstance(runs, list) and runs:
            if any(not isinstance(run, dict) for run in runs):
                return []
            source_runs = "".join(str(run.get("source_text") if run.get("source_text") is not None
                                      else run.get("text") or "") for run in runs)
            if source_runs != str(item.get("text") or ""):
                return []
            glyph_runs = [run for run in runs
                          if isinstance(run, dict) and str(run.get("text") or "").strip()]
            if glyph_runs and any(str((run.get("style") or {}).get("color") or base_color).casefold() == "none"
                                  for run in glyph_runs):
                return []
            if not glyph_runs and base_color == "none":
                return []
        elif base_color == "none":
            return []
    return texts


def _primitive(raw: Any, index: int, size: tuple[float, float], used: set[str]) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    primitive_id = unique_id(safe_id(raw.get("id"), f"primitive-{index}"), used)
    kind = str(raw.get("kind") or "rect").strip().lower().replace("-", "_")
    kind = KIND_ALIASES.get(kind, kind)
    points = canonical_points(raw.get("points"), size)
    if kind not in PRIMITIVES:
        kind = "polygon" if len(points) >= 3 else "rect"
    result: dict[str, Any] = {"id": primitive_id, "kind": kind, "style": style(raw.get("style"), text=kind == "text")}
    if kind in ("line", "polyline", "polygon"):
        needed = 3 if kind == "polygon" else 2
        if len(points) < needed:
            if kind == "polygon":
                points = [[0, 0], [size[0], 0], [size[0], size[1]], [0, size[1]]]
            else:
                points = [[0, size[1] / 2], [size[0], size[1] / 2]]
        result.update(points=points, arrow_start=bool(raw.get("arrow_start", False)), arrow_end=bool(raw.get("arrow_end", kind in ("line", "polyline"))))
    else:
        result["bbox"] = canonical_bbox(raw.get("bbox"), size)
    if kind == "text":
        result["text"] = str(raw.get("text") or " ")
        runs = canonical_text_runs(raw.get("runs"), result["text"], result["style"])
        if raw.get("runs") and not runs:
            raise ValueError("Invalid native rich-text source identity; refusing literal fallback")
        if runs:
            result["runs"] = runs
    elif kind == "path":
        result["path"] = str(raw.get("path") or "")
        if raw.get("coordinate_space") in {"primitive", "component"}:
            result["coordinate_space"] = raw["coordinate_space"]
        result["fallback_kind"] = "rect"
        result["arrow_start"] = bool(raw.get("arrow_start", False))
        result["arrow_end"] = bool(raw.get("arrow_end", False))
    elif kind == "image":
        result["asset_request"] = raw.get("asset_request") if isinstance(raw.get("asset_request"), dict) else {"mode": "source_crop"}
    return result


def canonicalize(
    raw: Any,
    recognition: dict[str, Any],
    image: str | Path,
    directory: str | Path,
    asset_decisions: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    raw = raw if isinstance(raw, dict) else {}
    supplied = {
        item.get("label_id"): item for item in raw.get("components", [])
        if isinstance(item, dict) and isinstance(item.get("label_id"), str)
    }
    assets: dict[str, Any] = {}
    components: list[dict[str, Any]] = []
    repairs: list[dict[str, Any]] = []
    policy_overrides: list[dict[str, Any]] = []
    path_validation_issues: list[dict[str, Any]] = []
    assets_dir = Path(directory) / "assets"
    decisions = {
        item.get("label_id"): item
        for item in (asset_decisions or {}).get("decisions", [])
        if isinstance(item, dict) and item.get("label_id")
    }
    for obj in recognition["objects"]:
        decision = decisions.get(obj["id"], {"decision": "native"})
        allow_raster = decision.get("decision") == "localized_raster" and isinstance(decision.get("approved_bbox"), list)
        candidate = supplied.get(obj["id"])
        if candidate and candidate.get("group_only"):
            from .representation_recipes import graph_members
            members = graph_members(obj, recognition)
            if not members or members != candidate.get("members"):
                raise ValueError("Logical graph group lacks verified native children")
            components.append({
                "label_id": obj["id"], "category": obj["category"],
                "width": obj["bbox"][2], "height": obj["bbox"][3],
                "primitives": [], "primitive_order": [],
                "group_only": True, "members": members,
            })
            continue
        if not candidate:
            candidate = _fallback_component(obj, allow_raster=allow_raster)
            repairs.append({"label_id": obj["id"], "action": "generated_fallback_component"})
        elif allow_raster and obj["category"] == "image":
            replaced_kinds = sorted({
                str(item.get("kind") or "") for item in candidate.get("primitives", [])
                if isinstance(item, dict)
            })
            retained_text = _approved_raster_text_primitives(candidate, str(obj.get("text") or ""))
            candidate = _fallback_component(obj, allow_raster=True)
            candidate["primitives"].extend(retained_text)
            policy_overrides.append({
                "label_id": obj["id"],
                "action": "replaced_visual_surrogate_with_approved_source_crop",
                "source_basis": decision.get("source_basis"),
                "replaced_kinds": replaced_kinds,
                "retained_exact_visible_text_primitives": [item.get("id") for item in retained_text],
            })
        width = max(1.0, number(candidate.get("width"), obj["bbox"][2]))
        height = max(1.0, number(candidate.get("height"), obj["bbox"][3]))
        used: set[str] = set()
        primitives = [value for index, item in enumerate(candidate.get("primitives", []), 1) if (value := _primitive(item, index, (width, height), used))]
        if not primitives:
            candidate = _fallback_component(obj, allow_raster=allow_raster)
            primitives = [value for index, item in enumerate(candidate["primitives"], 1) if (value := _primitive(item, index, (width, height), used))]
            repairs.append({"label_id": obj["id"], "action": "replaced_empty_component"})
        expected_text = str(obj.get("text") or "")
        if obj["category"] != "equation" and expected_text.strip():
            actual_text = "".join(str(item.get("text") or "") for item in primitives if item["kind"] == "text")
            if _text_identity(actual_text) != _text_identity(expected_text):
                text_primitives = [item for item in primitives if item["kind"] == "text"]
                if len(text_primitives) == 1 and not text_primitives[0].get("runs"):
                    text_primitives[0]["text"] = expected_text
                    repairs.append({"label_id": obj["id"], "action": "restored_exact_editable_text_identity", "previous_text": actual_text})
                else:
                    text_id = unique_id("text", used)
                    replacement = {
                        "id": text_id, "kind": "text", "bbox": [0.0, 0.0, width, height],
                        "text": expected_text, "style": style(obj.get("style_hint"), text=True), "editable": True,
                    }
                    # A pure text label must not retain glyph-outline paths. Composite
                    # objects keep geometry but receive an exact editable text layer.
                    primitives = [replacement] if obj["category"] == "text" else [
                        item for item in primitives if item["kind"] != "text"
                    ] + [replacement]
                    repairs.append({
                        "label_id": obj["id"], "action": "restored_editable_text_coverage",
                        "expected": expected_text,
                        "preserved_non_text_geometry": obj["category"] != "text",
                        "replaced_kinds": sorted({item["kind"] for item in candidate.get("primitives", []) if isinstance(item, dict)}),
                    })
        for primitive in primitives:
            if primitive["kind"] == "path":
                original_path = primitive["path"]
                normalized_path = normalize_numeric_path_words(original_path)
                try:
                    _path_commands(normalized_path)
                except ValueError as exc:
                    path_validation_issues.append({
                        "label_id": obj["id"], "primitive_id": primitive["id"],
                        "error": str(exc),
                    })
                    if not primitive.get("arrow_start") and not primitive.get("arrow_end"):
                        primitive["kind"] = "rect"
                        primitive.pop("path", None)
                        primitive.pop("fallback_kind", None)
                        primitive.pop("arrow_start", None)
                        primitive.pop("arrow_end", None)
                        repairs.append({
                            "label_id": obj["id"], "primitive_id": primitive["id"],
                            "action": "replaced_invalid_path_with_native_rect",
                            "error": str(exc),
                        })
                else:
                    if normalized_path != original_path:
                        primitive["path"] = normalized_path
                        repairs.append({
                            "label_id": obj["id"], "primitive_id": primitive["id"],
                            "action": "normalized_numeric_path_words",
                            "original_path": original_path,
                            "normalized_path": normalized_path,
                        })
            if primitive["kind"] == "image":
                if allow_raster:
                    # The approved crop is measured in source-image coordinates.
                    # Preserve those coordinates relative to its Recognition owner;
                    # stretching a smaller or offset crop across the full owner
                    # changes both geometry and visual provenance. A Recognition
                    # bbox fallback naturally maps to the full component here.
                    source_x, source_y, source_w, source_h = decision["approved_bbox"]
                    owner_x, owner_y, owner_w, owner_h = obj["bbox"]
                    primitive["bbox"] = [
                        round((source_x - owner_x) * width / owner_w, 4),
                        round((source_y - owner_y) * height / owner_h, 4),
                        round(source_w * width / owner_w, 4),
                        round(source_h * height / owner_h, 4),
                    ]
                    asset_id, manifest = _crop_asset(
                        Path(image), decision["approved_bbox"], obj["id"], assets_dir,
                        decision.get("source_candidate_id"), decision.get("source_basis"),
                        decision.get("native_text_exclusions"), decision.get("asset_transforms"),
                    )
                    assets[asset_id] = manifest
                    primitive.pop("asset_request", None)
                    primitive["asset_id"] = asset_id
                    primitive["editable"] = False
                    primitive["fallback_reason"] = "approved_localized_raster_representation"
                else:
                    primitive.pop("asset_request", None)
                    primitive["kind"] = "rect"
                    primitive["editable"] = True
                    primitive["fallback_reason"] = "unapproved_image_request_replaced_with_native_placeholder"
                    repairs.append({"label_id": obj["id"], "action": "rejected_unapproved_image_primitive"})
            else:
                primitive["editable"] = True
        requested_order = candidate.get("primitive_order", []) if isinstance(candidate.get("primitive_order"), list) else []
        valid_ids = [item["id"] for item in primitives]
        order = [value for value in requested_order if value in valid_ids]
        order.extend(value for value in valid_ids if value not in order)
        components.append({
            "label_id": obj["id"], "category": obj["category"], "width": width, "height": height,
            "primitives": primitives, "primitive_order": order,
        })
    result = {
        "schema_version": "decomposer.representation.v1",
        "components": components,
        "assets": assets,
        # A policy-approved localized raster is an intentional leaf
        # representation, not a silent fallback. Degradation means the
        # canonicalizer repaired or replaced model output.
        "degraded": bool(repairs),
    }
    report = {
        "repair_count": len(repairs), "repairs": repairs,
        "path_validation_issues": path_validation_issues,
        "policy_override_count": len(policy_overrides), "policy_overrides": policy_overrides,
        "component_count": len(components), "asset_count": len(assets),
        "approved_raster_count": sum(item.get("decision") == "localized_raster" for item in decisions.values()),
    }
    validate(result, recognition)
    return result, report


def validate(data: dict[str, Any], recognition: dict[str, Any]) -> None:
    expected = [item["id"] for item in recognition["objects"]]
    actual = [item["label_id"] for item in data["components"]]
    if actual != expected:
        raise AssertionError("representation must preserve recognition label order and identity")
    for component in data["components"]:
        if component.get("group_only"):
            from .representation_recipes import graph_members
            owner = next(o for o in recognition["objects"] if o["id"] == component["label_id"])
            if (component["primitives"] or component["primitive_order"]
                    or not graph_members(owner, recognition)
                    or component.get("members") != graph_members(owner, recognition)):
                raise AssertionError("Invalid logical group coverage")
            continue
        ids = [item["id"] for item in component["primitives"]]
        if not ids or len(ids) != len(set(ids)) or set(ids) != set(component["primitive_order"]):
            raise AssertionError(f"invalid canonical component {component['label_id']}")
    by_recognition = {item["id"]: item for item in recognition["objects"]}
    for component in data["components"]:
        obj = by_recognition[component["label_id"]]
        for primitive in component["primitives"]:
            runs = primitive.get("runs")
            if primitive["kind"] == "text" and isinstance(runs, list) and runs:
                source = "".join(str(run.get("source_text", run.get("text", ""))) for run in runs)
                if source != str(primitive.get("text", "")):
                    raise AssertionError(f"styled text source identity mismatch for {component['label_id']}/{primitive['id']}")
        if obj["category"] != "equation" and str(obj.get("text") or "").strip():
            actual_text = "".join(str(item.get("text") or "") for item in component["primitives"] if item["kind"] == "text")
            if actual_text != obj["text"]:
                raise AssertionError(f"exact recognition text identity mismatch for {component['label_id']}")


def run(
    image: str | Path,
    recognition: dict[str, Any],
    config: dict[str, Any],
    directory: str | Path,
    calls: str | Path,
    asset_decisions: dict[str, Any] | None = None,
    fixture_dir: str | Path | None = None,
) -> dict[str, Any]:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "recognition": recognition, "asset_decisions": asset_decisions or {},
        "primitive_kinds": list(PRIMITIVES), "coordinate_system": "component-local xywh",
        # Historical knowledge is deliberately unavailable to first-pass generation.
        "applicable_memories": [],
    }
    # Runtime model routing is not part of the figure description. Keeping it
    # out of the prompt also makes standard and fast-tier calls comparable.
    prompt_strategy = representation_prompt_strategy(config)
    if prompt_strategy:
        payload["representation_strategy"] = prompt_strategy
    prompt = (Path(__file__).parents[1] / "prompts" / "representation.md").read_text()
    if config.get("representation", {}).get("fidelity_guard"):
        prompt += "\n" + (Path(__file__).parents[1] / "prompts" / "representation-fidelity.md").read_text()
    if config.get("representation", {}).get("geometry_precision_hint"):
        prompt += "\n" + (Path(__file__).parents[1] / "prompts" / "representation-geometry-precision.md").read_text()
    representation_config = representation_model_config(config)
    strategy = config.get("representation", {}).get("strategy")
    if strategy == "parallel_shards" and fixture_dir is None:
        proposal, metadata = generate_sharded(
            image, recognition, asset_decisions or {}, representation_config, calls, prompt,
        )
    elif strategy == "compact_wire" and fixture_dir is None:
        proposal, metadata = generate_compact(
            image, recognition, asset_decisions or {}, representation_config, calls, prompt,
        )
    else:
        proposal, metadata = fixture_or_model("representation", prompt, payload, REPRESENTATION_PROPOSAL_SCHEMA, image, representation_config, Path(calls) / "representation", fixture_dir)
    write_json(directory / "model-output.json", proposal)
    result, report = canonicalize(proposal, recognition, image, directory, asset_decisions)
    report["model"] = metadata
    write_json(directory / "canonicalization-report.json", report)
    write_json(directory / "assets.json", result["assets"])
    write_json(directory / "representation.json", result)
    return result
