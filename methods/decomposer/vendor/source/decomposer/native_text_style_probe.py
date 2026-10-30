"""Conservative source-ink diagnostics for native two-line text owners.

These functions propose candidates only. They never mutate a scene, compile a
PPTX, or accept a replacement without the normal visual and provenance gates.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .common import sha256, write_json
from .pptx import compile_scene
from .provenance import build as build_provenance
from .quality import affected_scene_region, compare


def _dark_color(value: str) -> bool:
    if len(value) != 7 or not value.startswith("#"):
        return False
    try:
        return max(int(value[index:index + 2], 16) for index in (1, 3, 5)) < 100
    except ValueError:
        return False


def _dark_ink(pixels: np.ndarray) -> np.ndarray:
    return np.max(pixels, axis=2) < 128


def dark_ink_candidates(scene: dict[str, Any], reference_path: Path,
                        preview_path: Path, *, maximum_ratio: float = .75) -> list[dict[str, Any]]:
    """Find under-rendered, dark Arial-bold two-line owners for later probes."""
    with Image.open(reference_path) as image:
        source = np.asarray(image.convert("RGB"))
    with Image.open(preview_path) as image:
        preview = np.asarray(image.convert("RGB"))
    if source.shape != preview.shape:
        raise ValueError("source and preview dimensions differ")
    canvas_height, canvas_width = source.shape[:2]
    results = []
    for element in scene.get("elements", []):
        style = element.get("style") or {}
        color = str(style.get("color") or "")
        text = str(element.get("text") or "")
        font = float(style.get("font_size") or 0)
        if (element.get("kind") != "text" or element.get("source_primitive_id")
                or text.count("\n") != 1 or not all(part.strip() for part in text.split("\n"))
                or style.get("font_family") != "Arial" or style.get("bold") is not True
                or not 30 <= font <= 55 or not _dark_color(color)):
            continue
        if any(
            (run.get("style") or {}).get("color", color) not in (color, "#000000")
            or (run.get("style") or {}).get("font_family", "Arial") != "Arial"
            or float((run.get("style") or {}).get("font_size") or font) != font
            for run in element.get("runs") or []
        ):
            continue
        box = element.get("bbox") or []
        if len(box) != 4:
            continue
        x, y, width, height = box
        left, top = round(x), round(y)
        right, bottom = round(x + width), round(y + height)
        if not (0 <= left < right <= canvas_width and 0 <= top < bottom <= canvas_height):
            continue
        source_count = int(_dark_ink(source[top:bottom, left:right]).sum())
        rendered_count = int(_dark_ink(preview[top:bottom, left:right]).sum())
        ratio = rendered_count / max(1, source_count)
        if source_count < 500 or not 0 < ratio < maximum_ratio:
            continue
        results.append({
            "id": element["id"], "source_label_id": element["source_label_id"],
            "bbox": box[:], "font_size": font,
            "source_dark_pixels": source_count,
            "rendered_dark_pixels": rendered_count,
            "ink_ratio": round(ratio, 6),
        })
    return sorted(results, key=lambda item: (item["ink_ratio"], item["id"]))


def single_line_monospace_ink_candidates(
    scene: dict[str, Any], reference_path: Path, preview_path: Path,
    *, maximum_ratio: float = .75,
) -> list[dict[str, Any]]:
    """Detect source-grounded under-rendering in plain, bold monospace labels."""
    with Image.open(reference_path) as image:
        source = np.asarray(image.convert("RGB"))
    with Image.open(preview_path) as image:
        preview = np.asarray(image.convert("RGB"))
    if source.shape != preview.shape:
        raise ValueError("source and preview dimensions differ")
    canvas_height, canvas_width = source.shape[:2]
    results = []
    for element in scene.get("elements", []):
        style = element.get("style") or {}
        text = str(element.get("text") or "")
        color = str(style.get("color") or "")
        font = float(style.get("font_size") or 0)
        if (element.get("kind") != "text" or element.get("source_primitive_id")
                or "\n" in text or not 5 <= len(text) <= 25
                or style.get("font_family") != "Courier New" or style.get("bold") is not True
                or not 25 <= font <= 55 or not _dark_color(color)):
            continue
        if any(
            (run.get("style") or {}).get("color", color) not in (color, "#000000")
            or (run.get("style") or {}).get("font_family", "Courier New") != "Courier New"
            or float((run.get("style") or {}).get("font_size") or font) != font
            for run in element.get("runs") or []
        ):
            continue
        box = element.get("bbox") or []
        if len(box) != 4:
            continue
        x, y, width, height = box
        left, top = round(x), round(y)
        right, bottom = round(x + width), round(y + height)
        if not (0 <= left < right <= canvas_width and 0 <= top < bottom <= canvas_height):
            continue
        source_count = int(_dark_ink(source[top:bottom, left:right]).sum())
        rendered_count = int(_dark_ink(preview[top:bottom, left:right]).sum())
        ratio = rendered_count / max(1, source_count)
        if source_count < 500 or not 0 < ratio < maximum_ratio:
            continue
        results.append({
            "id": element["id"], "source_label_id": element["source_label_id"],
            "bbox": box[:], "font_size": font,
            "source_dark_pixels": source_count,
            "rendered_dark_pixels": rendered_count,
            "ink_ratio": round(ratio, 6),
        })
    return sorted(results, key=lambda item: (item["ink_ratio"], item["id"]))


def _shift_mask(mask: np.ndarray, dx: int, dy: int) -> np.ndarray:
    height, width = mask.shape
    shifted = np.zeros_like(mask)
    x0, x1 = max(0, -dx), min(width, width - dx)
    y0, y1 = max(0, -dy), min(height, height - dy)
    if x0 < x1 and y0 < y1:
        shifted[y0 + dy:y1 + dy, x0 + dx:x1 + dx] = mask[y0:y1, x0:x1]
    return shifted


def dark_ink_shifts(reference_path: Path, preview_path: Path, bbox: list[float],
                    *, max_shift: int = 8, padding: int = 12,
                    top_k: int = 5) -> list[dict[str, Any]]:
    """Rank image-derived translations; require a real recompile to accept one."""
    with Image.open(reference_path) as image:
        source = np.asarray(image.convert("RGB"))
    with Image.open(preview_path) as image:
        preview = np.asarray(image.convert("RGB"))
    if source.shape != preview.shape:
        raise ValueError("source and preview dimensions differ")
    if max_shift < 0 or top_k <= 0 or padding < max_shift:
        raise ValueError("invalid bounded shift search")
    height, width = source.shape[:2]
    x, y, box_width, box_height = bbox
    left = max(0, int(x - padding))
    top = max(0, int(y - padding))
    right = min(width, int(x + box_width + padding + .999))
    bottom = min(height, int(y + box_height + padding + .999))
    if left >= right or top >= bottom:
        raise ValueError("empty owner search region")
    source_mask = _dark_ink(source[top:bottom, left:right])
    preview_mask = _dark_ink(preview[top:bottom, left:right])
    ranked = []
    for dy in range(-max_shift, max_shift + 1):
        for dx in range(-max_shift, max_shift + 1):
            shifted = _shift_mask(preview_mask, dx, dy)
            union = int((source_mask | shifted).sum())
            score = int((source_mask & shifted).sum()) / union if union else 0.0
            ranked.append((score, -(abs(dx) + abs(dy)), -abs(dy), -abs(dx), dx, dy))
    ranked.sort(reverse=True)
    return [{"shift_x": dx, "shift_y": dy, "ink_iou": round(score, 6)}
            for score, _, _, _, dx, dy in ranked[:top_k]]


def styled_scene(scene: dict[str, Any], primitive_id: str, family: str, size: float,
                 dx: int = 0, dy: int = 0) -> dict[str, Any]:
    """Create an independent native-text-only proposal without changing identity."""
    candidate = copy.deepcopy(scene)
    owner = next((item for item in candidate["elements"] if item["id"] == primitive_id), None)
    if owner is None or owner.get("kind") != "text":
        raise ValueError("native text owner missing")
    canvas = candidate["canvas"]
    x, y, width, height = owner["bbox"]
    if not (0 <= x + dx and x + dx + width <= canvas["width"]
            and 0 <= y + dy and y + dy + height <= canvas["height"]):
        raise ValueError("styled native text box outside canvas")
    owner["style"]["font_family"] = family
    owner["style"]["font_size"] = size
    for run in owner.get("runs") or []:
        run["style"]["font_family"] = family
        run["style"]["font_size"] = size
    owner["bbox"] = [x + dx, y + dy, width, height]
    return candidate


def scaled_monospace_scene(scene: dict[str, Any], primitive_id: str, size: float,
                           dx: int = 0, dy: int = 0) -> dict[str, Any]:
    """Raise single-line text size and box height while preserving exact text."""
    candidate = styled_scene(scene, primitive_id, "Courier New", size)
    owner = next(item for item in candidate["elements"] if item["id"] == primitive_id)
    x, y, width, height = owner["bbox"]
    next_height = max(height, round(size * 1.2))
    next_y = y - round((next_height - height) * .8) + dy
    next_x = x + dx
    canvas = candidate["canvas"]
    if (next_x < 0 or next_y < 0 or next_x + width > canvas["width"]
            or next_y + next_height > canvas["height"]):
        raise ValueError("scaled native text box outside canvas")
    owner["bbox"] = [next_x, next_y, width, next_height]
    return candidate


def _render(scene: dict[str, Any], output: Path, config: dict[str, Any]) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "scene.json", scene)
    report = compile_scene(output / "scene.json", output, config, True)
    build_provenance(scene, output / "editable.pptx", output)
    return report


def search_owner(scene: dict[str, Any], reference: Path, before_preview: Path,
                 owner_id: str, config: dict[str, Any], output: Path,
                 baseline_report: dict[str, Any]) -> dict[str, Any]:
    """Measure guarded font/size/shift alternatives, without adopting any.

    The output directory is created only after the conservative trigger passes.
    Every measured candidate retains its PPTX, preview, provenance and scores.
    """
    if output.exists():
        raise FileExistsError(output)
    triggered = {item["id"]: item for item in
                 dark_ink_candidates(scene, reference, before_preview)}
    if owner_id not in triggered:
        raise ValueError("owner did not pass conservative dark-ink trigger")
    original = next(item for item in scene["elements"] if item["id"] == owner_id)
    current_size = float(original["style"]["font_size"])
    baseline_fallbacks = baseline_report["compiler"].get("fallbacks", [])
    output.mkdir(parents=True)
    attempts = []
    for family in ("Arial", "Arial Black"):
        for offset in (0, -1, -2, -3):
            size = current_size + offset
            if family == original["style"]["font_family"] and size == current_size:
                continue
            identity = f"{family.lower().replace(' ', '-')}-{size:g}"
            style_scene = styled_scene(scene, owner_id, family, size)
            style_dir = output / identity / "style"
            style_report = _render(style_scene, style_dir, config)
            if style_report["status"] != "render_success":
                attempts.append({"candidate_id": identity, "status": "style-render-failed"})
                continue
            shift = dark_ink_shifts(reference, style_dir / "preview.png",
                                    original["bbox"], top_k=1)[0]
            dx, dy = shift["shift_x"], shift["shift_y"]
            try:
                candidate_scene = styled_scene(scene, owner_id, family, size, dx, dy)
            except ValueError:
                attempts.append({"candidate_id": identity, "status": "shift-outside-canvas",
                                 "source_ink_shift": shift})
                continue
            candidate_dir = output / identity / "candidate"
            candidate_report = _render(candidate_scene, candidate_dir, config)
            region = affected_scene_region(scene, candidate_scene, [owner_id])
            assessment = compare(reference, before_preview,
                                 candidate_dir / "preview.png", region)
            before_local, after_local = assessment["before_local"], assessment["after_local"]
            before_global, after_global = assessment["before_global"], assessment["after_global"]
            qualified = (
                candidate_report["status"] == "render_success"
                and candidate_report["compiler"].get("fallbacks", []) == baseline_fallbacks
                and after_local["pixel_similarity"] >= before_local["pixel_similarity"]
                and (after_local["edge_iou"] or 0) >= (before_local["edge_iou"] or 0)
                and after_global["pixel_similarity"] > before_global["pixel_similarity"]
                and (after_global["edge_iou"] or 0) >= (before_global["edge_iou"] or 0)
            )
            attempts.append({
                "candidate_id": identity, "status": "measured", "qualified": qualified,
                "font_family": family, "font_size": size, "source_ink_shift": shift,
                "owner_stage": "representation", "mechanism": "native-text-source-ink-style-and-registration",
                "affected_label_ids": [original["source_label_id"]],
                "affected_primitive_ids": [owner_id], "field": "style.font_family+style.font_size+bbox",
                "evaluation_region": region,
                "evaluation_region_source": "affected_scene_owners",
                "assessment": assessment,
                "scene_sha256": sha256(candidate_dir / "scene.json"),
                "pptx_sha256": sha256(candidate_dir / "editable.pptx"),
                "compiler_fallbacks": candidate_report["compiler"].get("fallbacks", []),
                "artifact_dir": str(candidate_dir),
            })
    qualified = [item for item in attempts if item.get("qualified")]
    best = max(qualified, key=lambda item: (
        item["assessment"]["after_global"]["pixel_similarity"],
        item["assessment"]["after_global"]["edge_iou"] or 0,
        item["assessment"]["after_local"]["pixel_similarity"],
    )) if qualified else None
    result = {
        "schema_version": "decomposer.native-text-style-probe.v1",
        "scope": "isolated-probe-no-source-mutation-or-promotion",
        "owner_id": owner_id, "trigger": triggered[owner_id],
        "baseline_fallbacks": baseline_fallbacks,
        "attempts": attempts, "best_candidate_id": best["candidate_id"] if best else None,
        "best_artifact_dir": best["artifact_dir"] if best else None,
    }
    write_json(output / "search-report.json", result)
    return result


def search_single_line_monospace_owner(
    scene: dict[str, Any], reference: Path, before_preview: Path,
    owner_id: str, config: dict[str, Any], output: Path,
    baseline_report: dict[str, Any],
) -> dict[str, Any]:
    """Bounded positive font/box search for one eligible editable label."""
    if output.exists():
        raise FileExistsError(output)
    triggered = {item["id"]: item for item in
                 single_line_monospace_ink_candidates(scene, reference, before_preview)}
    if owner_id not in triggered:
        raise ValueError("owner did not pass conservative single-line ink trigger")
    original = next(item for item in scene["elements"] if item["id"] == owner_id)
    current_size = float(original["style"]["font_size"])
    baseline_fallbacks = baseline_report["compiler"].get("fallbacks", [])
    output.mkdir(parents=True)
    attempts = []
    for offset in (2, 4, 5, 6, 8):
        size = current_size + offset
        identity = f"courier-new-{size:g}"
        try:
            style_scene = scaled_monospace_scene(scene, owner_id, size)
        except ValueError:
            attempts.append({"candidate_id": identity, "status": "box-outside-canvas"})
            continue
        style_dir = output / identity / "style"
        style_report = _render(style_scene, style_dir, config)
        if style_report["status"] != "render_success":
            attempts.append({"candidate_id": identity, "status": "style-render-failed"})
            continue
        style_owner = next(item for item in style_scene["elements"] if item["id"] == owner_id)
        shift = dark_ink_shifts(reference, style_dir / "preview.png",
                                style_owner["bbox"], top_k=1)[0]
        try:
            candidate_scene = scaled_monospace_scene(
                scene, owner_id, size, shift["shift_x"], shift["shift_y"])
        except ValueError:
            attempts.append({"candidate_id": identity, "status": "shift-outside-canvas",
                             "source_ink_shift": shift})
            continue
        candidate_dir = output / identity / "candidate"
        candidate_report = _render(candidate_scene, candidate_dir, config)
        region = affected_scene_region(scene, candidate_scene, [owner_id])
        assessment = compare(reference, before_preview,
                             candidate_dir / "preview.png", region)
        before_local, after_local = assessment["before_local"], assessment["after_local"]
        before_global, after_global = assessment["before_global"], assessment["after_global"]
        qualified = (
            candidate_report["status"] == "render_success"
            and candidate_report["compiler"].get("fallbacks", []) == baseline_fallbacks
            and after_local["pixel_similarity"] >= before_local["pixel_similarity"]
            and (after_local["edge_iou"] or 0) >= (before_local["edge_iou"] or 0)
            and after_global["pixel_similarity"] > before_global["pixel_similarity"]
            and (after_global["edge_iou"] or 0) >= (before_global["edge_iou"] or 0)
        )
        attempts.append({
            "candidate_id": identity, "status": "measured", "qualified": qualified,
            "font_family": "Courier New", "font_size": size,
            "source_ink_shift": shift,
            "owner_stage": "representation",
            "mechanism": "single-line-monospace-ink-scale-and-registration",
            "affected_label_ids": [original["source_label_id"]],
            "affected_primitive_ids": [owner_id],
            "field": "style.font_size+bbox",
            "evaluation_region": region,
            "evaluation_region_source": "affected_scene_owners",
            "assessment": assessment,
            "scene_sha256": sha256(candidate_dir / "scene.json"),
            "pptx_sha256": sha256(candidate_dir / "editable.pptx"),
            "compiler_fallbacks": candidate_report["compiler"].get("fallbacks", []),
            "artifact_dir": str(candidate_dir),
        })
    qualified_rows = [item for item in attempts if item.get("qualified")]
    best = max(qualified_rows, key=lambda item: (
        item["assessment"]["after_global"]["pixel_similarity"],
        item["assessment"]["after_global"]["edge_iou"] or 0,
        item["assessment"]["after_local"]["pixel_similarity"],
    )) if qualified_rows else None
    result = {
        "schema_version": "decomposer.single-line-monospace-ink-probe.v1",
        "scope": "isolated-probe-no-source-mutation-or-promotion",
        "owner_id": owner_id, "trigger": triggered[owner_id],
        "baseline_fallbacks": baseline_fallbacks,
        "attempts": attempts,
        "best_candidate_id": best["candidate_id"] if best else None,
        "best_artifact_dir": best["artifact_dir"] if best else None,
    }
    write_json(output / "search-report.json", result)
    return result
